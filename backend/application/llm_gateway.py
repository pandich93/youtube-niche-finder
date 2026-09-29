"""Gateway every caller goes through for optional LLM enrichment -- adds a
cache (same task+model+input never costs twice) and a daily USD budget on
top of whatever infrastructure/llm provider is configured.

LLM_PROVIDER=none (the default) makes infrastructure.llm.factory.get_provider()
return NullProvider, so run() always returns None and nothing here spends a
cent or makes a network call -- this module only changes behaviour for
someone who has opted in.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

import infrastructure.postgres as db
from domain.users import SYSTEM_USER_ID
from infrastructure import quota_owner
from infrastructure.llm import factory

DAILY_BUDGET_USD = float(os.environ.get("LLM_DAILY_BUDGET_USD", "1.0"))


def user_daily_budget_usd() -> float:
    """plan 15 (5.10): each signed-in user's share of the day's LLM spend in
    multi-user mode; 0 = no personal limit. The installation-wide
    LLM_DAILY_BUDGET_USD still caps everyone together."""
    try:
        return max(0.0, float(os.environ.get("NF_USER_DAILY_LLM_USD") or 0.25))
    except ValueError:
        return 0.25


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _cache_key(task: str, model: str, system: str, user: str, schema: dict) -> str:
    normalized = json.dumps({"system": system, "user": user, "schema": schema},
                            sort_keys=True)
    return hashlib.sha256(f"{task}:{model}:{normalized}".encode()).hexdigest()


def spent_today(conn) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE day=?",
        (today_utc(),)).fetchone()
    return float(row[0] or 0)


def user_spent_today(conn, user_id) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE day=? AND user_id=?",
        (today_utc(), user_id)).fetchone()
    return float(row[0] or 0)


def budget_blocked_today(conn) -> bool:
    return db.get_meta(conn, "llm_budget_blocked_until") == today_utc()


def run(task: str, system: str, user: str, schema: dict, model: str = None,
        private: bool = False) -> dict | None:
    """private=True: the prompt carries the user's own text (draft titles, a
    topic), so the answer skips llm_cache, which every user shares (plan 15,
    rule 4). Budget and usage are counted as usual."""
    provider = factory.get_provider()
    model = model or factory.default_model()
    key = _cache_key(task, model, system, user, schema)

    conn = db.get_conn()
    try:
        row = None if private else conn.execute(
            "SELECT result FROM llm_cache WHERE key=?", (key,)).fetchone()
        if row:
            return json.loads(row["result"])

        if budget_blocked_today(conn):
            return None
        if spent_today(conn) >= DAILY_BUDGET_USD:
            db.set_meta(conn, "llm_budget_blocked_until", today_utc())
            conn.commit()
            return None
        # the signed-in user who asked (multi-user mode; the worker has none)
        owner = quota_owner.current()
        limit = user_daily_budget_usd()
        if owner is not None and limit and user_spent_today(conn, owner) >= limit:
            return None

        result = provider.complete_json(system, user, schema, model=model)
        if result is None:
            return None

        if not private:
            conn.execute(
                "INSERT INTO llm_cache (key, task, model, result, created_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(key) DO NOTHING",
                (key, task, result.model, json.dumps(result.data),
                 datetime.now(timezone.utc).isoformat()))
        conn.execute(
            "INSERT INTO llm_usage (user_id, day, model, calls, prompt_tokens, completion_tokens, "
            "cost_usd) VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id, day, model) DO UPDATE SET "
            "calls=llm_usage.calls+excluded.calls, "
            "prompt_tokens=llm_usage.prompt_tokens+excluded.prompt_tokens, "
            "completion_tokens=llm_usage.completion_tokens+excluded.completion_tokens, "
            "cost_usd=llm_usage.cost_usd+excluded.cost_usd",
            (owner if owner is not None else SYSTEM_USER_ID, today_utc(), result.model, 1,
             result.prompt_tokens, result.completion_tokens, result.cost_usd or 0))
        conn.commit()
        return result.data
    finally:
        conn.close()

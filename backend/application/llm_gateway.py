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
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from application import plans
from domain.users import SYSTEM_USER_ID
from infrastructure import quota_owner
from infrastructure.llm import factory

DAILY_BUDGET_USD = float(os.environ.get("LLM_DAILY_BUDGET_USD", "1.0"))
# multi-user plan P1-09: the part of the day's budget only the worker (and
# single-user mode) may spend, so background enrichment keeps going when
# signed-in users have used up the rest -- like WORKER_RESERVE_UNITS for YouTube
WORKER_RESERVE_USD = float(os.environ.get("LLM_WORKER_RESERVE_USD", "0.25"))
# a reservation older than this belongs to a call that died mid-way
RESERVATION_TTL_MINUTES = 15


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


def installation_spent_today() -> float:
    """Расход всей установки за день -- для сводки рядом с общим бюджетом.
    Отдельным соединением ролью владельца: в запросе пользователя под RLS
    (P1-02) spent_today видел бы только его строки, без worker."""
    with db.system_scope():
        conn = db.get_conn()
    try:
        return spent_today(conn)
    finally:
        conn.close()


def user_spent_today(conn, user_id) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE day=? AND user_id=?",
        (today_utc(), user_id)).fetchone()
    return float(row[0] or 0)


def _held(conn, where: str = "", params=()) -> float:
    """Money reserved for LLM calls still in flight today. A reservation past
    RESERVATION_TTL_MINUTES no longer counts."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=RESERVATION_TTL_MINUTES)).isoformat()
    return float(conn.execute(
        "SELECT COALESCE(SUM(usd), 0) FROM llm_reservations WHERE day=? AND created_at > ?"
        + where, (today_utc(), cutoff, *params)).fetchone()[0] or 0)


def held_today(conn) -> float:
    return _held(conn)


def user_held_today(conn, user_id) -> float:
    return _held(conn, " AND user_id=?", (user_id,))


def budget_blocked_today(conn) -> bool:
    return db.get_meta(conn, "llm_budget_blocked_until") == today_utc()


def _max_cost(provider, model, system, user) -> float:
    """What the call can cost at most, if the provider knows its prices
    (OpenRouter does; Ollama and test doubles are free)."""
    estimate = getattr(provider, "max_cost_usd", None)
    return max(0.0, float(estimate(model, system, user))) if estimate else 0.0


def _over(spent: float, usd: float, cap: float) -> bool:
    # a free call (usd 0) still needs some budget left
    return spent >= cap or spent + usd > cap


def _reserve(owner, limit, usd: float):
    """Hold usd of today's budget for one call (multi-user plan P1-09).
    Spent plus held is checked against the shared budget -- minus the
    worker's reserve for a signed-in user -- and the user's own limit, under
    one lock, so parallel calls see each other's holds. The id of the hold,
    or None when the budget has no room.

    Budget and holds are the installation's, not one user's: the connection
    is the owner's, outside Row-Level Security (P1-02) -- the worker's
    SYSTEM_USER_ID rows count too, and a request may book one."""
    with db.system_scope():
        conn = db.get_conn()
    try:
        db.lock_until_commit(conn, "llm_budget", 0)
        total = spent_today(conn) + held_today(conn)
        if total >= DAILY_BUDGET_USD:
            db.set_meta(conn, "llm_budget_blocked_until", today_utc())
            conn.commit()
            return None
        cap = DAILY_BUDGET_USD - (WORKER_RESERVE_USD if owner is not None else 0)
        if _over(total, usd, max(0.0, cap)):
            return None
        if owner is not None and limit is not None and _over(
                user_spent_today(conn, owner) + user_held_today(conn, owner), usd, limit):
            return None
        row = conn.execute(
            "INSERT INTO llm_reservations (user_id, day, usd, created_at) VALUES (?,?,?,?) "
            "RETURNING id", (owner if owner is not None else SYSTEM_USER_ID, today_utc(), usd,
                             datetime.now(timezone.utc).isoformat())).fetchone()
        conn.commit()
        return row["id"]
    finally:
        conn.close()


def _settle(hold_id, owner, key, task, private, result):
    """Drop the hold and, for an answer, record what it really cost (owner's
    connection, like _reserve: the row may be SYSTEM_USER_ID's)."""
    with db.system_scope():
        conn = db.get_conn()
    try:
        conn.execute("DELETE FROM llm_reservations WHERE id=?", (hold_id,))
        if result is not None:
            if not private:
                conn.execute(
                    "INSERT INTO llm_cache (key, task, model, result, created_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(key) DO NOTHING",
                    (key, task, result.model, json.dumps(result.data),
                     datetime.now(timezone.utc).isoformat()))
            conn.execute(
                "INSERT INTO llm_usage (user_id, day, model, calls, prompt_tokens, "
                "completion_tokens, cost_usd) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id, day, model) DO UPDATE SET "
                "calls=llm_usage.calls+excluded.calls, "
                "prompt_tokens=llm_usage.prompt_tokens+excluded.prompt_tokens, "
                "completion_tokens=llm_usage.completion_tokens+excluded.completion_tokens, "
                "cost_usd=llm_usage.cost_usd+excluded.cost_usd",
                (owner if owner is not None else SYSTEM_USER_ID, today_utc(), result.model, 1,
                 result.prompt_tokens, result.completion_tokens, result.cost_usd or 0))
        conn.commit()
    finally:
        conn.close()


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
    finally:
        conn.close()

    # the signed-in user who asked (multi-user mode; the worker has none)
    owner, limit = quota_owner.current(), None
    if owner is not None:
        ent = plans.plan_for(owner)
        if not ent.features["llm"]:
            return None
        limit = ent.limits["llm_usd"]

    hold = _reserve(owner, limit, _max_cost(provider, model, system, user))
    if hold is None:
        return None
    result = None
    try:
        # no database connection is held while the provider answers
        result = provider.complete_json(system, user, schema, model=model)
    finally:
        _settle(hold, owner, key, task, private, result)
    return result.data if result is not None else None

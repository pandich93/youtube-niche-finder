"""What /metrics reads from the database at scrape time (multi-user plan
P1-14): the shared YouTube quota, the LLM spend, the worker's steps and the
accounts. These live in other processes (the worker, the MCP server), so the
database is the one place they all meet. Read-only; numbers only, no names.
"""
import json
from datetime import datetime

import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from application import llm_gateway


def _ts(value):
    try:
        return datetime.fromisoformat(value).timestamp() if value else None
    except ValueError:
        return None


def collect() -> list:
    """[(name, help, [(labels, value)])] for observability.render()."""
    conn = db.get_conn()
    try:
        units = int(db.get_meta(conn, yt.units_meta_key()) or 0)
        searches = int(db.get_meta(conn, yt.search_meta_key()) or 0)
        llm = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS usd, COALESCE(SUM(calls), 0) AS n "
                           "FROM llm_usage WHERE day = ?", (llm_gateway.today_utc(),)).fetchone()
        last_cycle = db.get_meta(conn, "worker_last_cycle")
        steps = conn.execute("SELECT key, value FROM meta WHERE key LIKE 'worker_step_%'").fetchall()
        users = conn.execute("SELECT COUNT(*) FILTER (WHERE disabled_at IS NULL) AS active, "
                             "COUNT(*) FILTER (WHERE disabled_at IS NOT NULL) AS disabled "
                             "FROM users").fetchone()
    finally:
        conn.close()

    last_run, duration, ok, failures = [], [], [], []
    for row in steps:
        key, value = row["key"], row["value"]
        if key.startswith("worker_step_failures_"):
            failures.append(({"step": key[len("worker_step_failures_"):]}, int(value or 0)))
            continue
        try:
            step = json.loads(value or "{}")
        except ValueError:
            continue
        labels = {"step": key[len("worker_step_"):]}
        last_run.append((labels, _ts(step.get("at"))))
        duration.append((labels, step.get("seconds")))
        ok.append((labels, 1 if step.get("outcome") == "ok" else 0))

    return [
        ("nf_youtube_units_used_today", "YouTube Data API units spent today (Pacific day), whole server",
         [({}, units)]),
        ("nf_youtube_units_daily_limit", "YouTube Data API units available per day",
         [({}, yt.DAILY_UNIT_LIMIT)]),
        ("nf_youtube_worker_reserve_units", "Units of the daily pool kept for the worker",
         [({}, yt.WORKER_RESERVE_UNITS)]),
        ("nf_youtube_search_calls_today", "search.list calls today, whole server", [({}, searches)]),
        ("nf_youtube_search_daily_limit", "search.list calls available per day",
         [({}, yt.SEARCH_DAILY_CALL_LIMIT)]),
        ("nf_llm_cost_usd_today", "LLM spend today (UTC day), whole server", [({}, float(llm["usd"]))]),
        ("nf_llm_calls_today", "LLM calls today (UTC day), whole server", [({}, int(llm["n"]))]),
        ("nf_llm_daily_budget_usd", "LLM_DAILY_BUDGET_USD", [({}, llm_gateway.DAILY_BUDGET_USD)]),
        ("nf_worker_last_cycle_timestamp_seconds", "When the worker last finished a cycle (unix time)",
         [({}, _ts(last_cycle))]),
        ("nf_worker_step_last_run_timestamp_seconds", "When each worker step last ran (unix time)", last_run),
        ("nf_worker_step_last_duration_seconds", "How long each worker step took last time", duration),
        ("nf_worker_step_last_ok", "1 if the step's last run succeeded, 0 if it failed or hit the quota", ok),
        ("nf_worker_step_failures", "Failed runs of each worker step since the database was created",
         failures),
        ("nf_users", "Accounts by state", [({"state": "active"}, users["active"]),
                                           ({"state": "disabled"}, users["disabled"])]),
    ]

"""Daily limits come from the user's tariff: YouTube units and searches in
the client, LLM dollars and the llm feature in the gateway. YouTube and the
LLM are faked -- no network, no money. Throwaway schema.
Run with pytest, or directly: python3 tests/test_plan_daily_limits.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import llm_gateway as G  # noqa: E402
from application import plans as PL  # noqa: E402
from infrastructure import quota_owner  # noqa: E402
from infrastructure.llm import factory  # noqa: E402

PW = "correct horse battery"


class _Resp:
    status_code = 200
    text = "{}"

    def json(self):
        return {"items": []}


class _Result:
    data = {"ok": True}
    model = "fake"
    prompt_tokens = completion_tokens = 1
    cost_usd = 0.01


class _Provider:
    calls = 0

    def complete_json(self, *a, **k):
        _Provider.calls += 1
        return _Result()


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setenv("NF_USER_DAILY_UNITS", "2000")
    conn = db.get_conn()
    conn.execute("DELETE FROM meta WHERE key LIKE 'yt_units_%' OR key LIKE 'search_calls_%' "
                 "OR key = 'llm_budget_blocked_until'")
    for t in ("plan_changes", "user_plans", "llm_usage", "llm_cache"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM plans WHERE slug != 'free'")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    monkeypatch.setattr(yt.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(factory, "get_provider", lambda: _Provider())
    _Provider.calls = 0


def on_plan(limits, features=None):
    uid = AUTH.create_user("a@example.com", PW)
    p = PL.create_plan({"slug": "t", "name": "T", "limits": limits, "features": features or {}})
    PL.choose(uid, p["id"])
    return uid


def as_user(uid, fn):
    token = quota_owner.set_owner(uid)
    try:
        return fn()
    finally:
        quota_owner.reset(token)


def test_units_limit_comes_from_plan():
    uid = on_plan({"yt_units": 2})
    as_user(uid, lambda: [yt._get("videos", "KEY") for _ in range(2)])
    with pytest.raises(yt.UserQuotaExceeded):
        as_user(uid, lambda: yt._get("videos", "KEY"))
    assert yt.user_usage(uid)["unitsLeft"] == 0


def test_zero_means_not_at_all_and_null_means_unlimited():
    uid = on_plan({"search_calls": 0, "yt_units": None})
    with pytest.raises(yt.UserQuotaExceeded):
        as_user(uid, lambda: yt._get("search", "KEY"))
    as_user(uid, lambda: [yt._get("videos", "KEY") for _ in range(5)])
    assert yt.user_usage(uid)["unitsLeft"] is None


def test_free_plan_still_reads_env(monkeypatch):
    monkeypatch.setenv("NF_USER_DAILY_UNITS", "1")
    uid = AUTH.create_user("a@example.com", PW)
    as_user(uid, lambda: yt._get("videos", "KEY"))
    with pytest.raises(yt.UserQuotaExceeded):
        as_user(uid, lambda: yt._get("videos", "KEY"))


def test_llm_budget_from_plan():
    uid = on_plan({"llm_usd": 0.01})
    assert as_user(uid, lambda: G.run("t", "s", "u1", {}, model="fake", private=True)) == {"ok": True}
    assert as_user(uid, lambda: G.run("t", "s", "u2", {}, model="fake", private=True)) is None
    assert _Provider.calls == 1


def test_llm_feature_off_returns_none():
    uid = on_plan({}, {"llm": False})
    assert as_user(uid, lambda: G.run("t", "s", "u", {}, model="fake", private=True)) is None
    assert _Provider.calls == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

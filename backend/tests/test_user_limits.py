"""Per-user LLM budget and rate limit (plan 15, sub-stage 5.10): a signed-in
user who spent their share of the day's LLM budget gets no more LLM answers
while others still do, the installation-wide budget still caps everyone, and
the request rate limit counts per signed-in user. The LLM provider is a stub
-- no network, no cost. Throwaway schema.
Run with pytest, or directly: python3 tests/test_user_limits.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import llm_gateway as gw  # noqa: E402
from infrastructure import quota_owner  # noqa: E402
from infrastructure.llm import factory  # noqa: E402
from infrastructure.llm.base import LLMResult  # noqa: E402

PW = "correct horse battery"


class _Paid:
    def complete_json(self, system, user, schema, *, model=None, max_tokens=1024):
        return LLMResult(data={"ok": user}, model="m", prompt_tokens=10, completion_tokens=5,
                         cost_usd=0.10)


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    conn = db.get_conn()
    for t in ("llm_cache", "llm_usage", "sessions"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM meta WHERE key = 'llm_budget_blocked_until'")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    monkeypatch.setattr(factory, "get_provider", lambda: _Paid())
    monkeypatch.setattr(gw, "DAILY_BUDGET_USD", 10.0)
    monkeypatch.setenv("NF_USER_DAILY_LLM_USD", "0.25")


def ask(owner, text):
    token = quota_owner.set_owner(owner) if owner is not None else None
    try:
        return gw.run("t", "sys", text, {})
    finally:
        if token is not None:
            quota_owner.reset(token)


def test_a_user_who_spent_their_share_gets_no_more_llm_answers():
    assert ask(101, "a1") and ask(101, "a2") and ask(101, "a3")      # 0.30 >= 0.25 after the 3rd
    assert ask(101, "a4") is None
    assert ask(102, "b1") == {"ok": "b1"}                             # B is untouched


def test_the_worker_and_single_user_mode_have_no_personal_llm_limit():
    for i in range(5):
        assert ask(None, f"w{i}") is not None
    conn = db.get_conn()
    assert gw.user_spent_today(conn, 0) == pytest.approx(0.5)          # booked to "system"
    assert gw.user_spent_today(conn, 1) == 0                           # not to a person
    conn.close()


def test_cached_answers_cost_nothing_and_are_not_blocked():
    ask(101, "same")
    ask(101, "x1")
    ask(101, "x2")
    assert ask(101, "same") == {"ok": "same"}                         # from llm_cache


def test_the_installation_budget_still_caps_everyone(monkeypatch):
    monkeypatch.setattr(gw, "DAILY_BUDGET_USD", 0.2)
    ask(101, "a1")
    ask(102, "b1")
    assert ask(103, "c1") is None


def test_the_rate_limit_counts_per_signed_in_user(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 3)
    api._rate_hits.clear()
    clients = []
    for email in ("a@example.com", "b@example.com"):
        AUTH.create_user(email, PW)
        c = TestClient(api.app, headers={"X-NF-Client": "tests"})
        c.post("/api/auth/login", json={"email": email, "password": PW})
        clients.append(c)
    api._rate_hits.clear()                                             # logins do not count
    a, b = clients
    assert [a.get("/api/auth/me").status_code for _ in range(3)] == [200, 200, 200]
    assert a.get("/api/auth/me").status_code == 429
    assert b.get("/api/auth/me").status_code == 200                    # B has their own window
    api._rate_hits.clear()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

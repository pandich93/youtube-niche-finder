"""Per-user YouTube budgets (plan 15, sub-stage 5.5): one API key serves the
installation, each signed-in user spends a daily share of it, and running out
blocks only that user. The HTTP call to YouTube is faked -- no network, no
real quota. Throwaway schema.
Run with pytest, or directly: python3 tests/test_user_quota.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import collecting as collector  # noqa: E402
from infrastructure import quota_owner  # noqa: E402

PW = "correct horse battery"


class _Resp:
    status_code = 200
    text = "{}"

    def json(self):
        return {"items": []}


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    conn = db.get_conn()
    conn.execute("DELETE FROM meta WHERE key LIKE 'yt_units_%' OR key LIKE 'search_calls_%'")
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    monkeypatch.setattr(yt.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setenv("NF_USER_DAILY_UNITS", "3")
    monkeypatch.setenv("NF_USER_DAILY_SEARCH_CALLS", "1")


def spend(user_id, path="videos", n=1):
    token = quota_owner.set_owner(user_id)
    try:
        for _ in range(n):
            yt._get(path, "KEY")
    finally:
        quota_owner.reset(token)


def test_a_user_is_stopped_at_their_budget_and_others_are_not():
    spend(101, n=3)
    with pytest.raises(yt.UserQuotaExceeded):
        spend(101)
    spend(102, n=3)                                     # B still has all of theirs
    assert yt.user_usage(101)["units"] == 3 and yt.user_usage(101)["unitsLeft"] == 0


def test_the_user_budget_is_a_quota_error_the_api_already_maps_to_429():
    assert issubclass(yt.UserQuotaExceeded, yt.QuotaExceeded)


def test_search_calls_have_their_own_budget():
    spend(101, path="search")
    with pytest.raises(yt.UserQuotaExceeded):
        spend(101, path="search")
    spend(101, path="videos")                           # other calls still fine


def test_the_worker_and_single_user_mode_have_no_personal_limit():
    for _ in range(10):
        yt._get("videos", "KEY")                        # no owner set
    conn = db.get_conn()
    total = int(db.get_meta(conn, yt.units_meta_key()) or 0)
    conn.close()
    assert total == 10                                   # still counted in the shared pool


def test_zero_means_no_limit(monkeypatch):
    monkeypatch.setenv("NF_USER_DAILY_UNITS", "0")
    spend(101, n=10)
    assert yt.user_usage(101)["unitsLeft"] is None


def test_the_signed_in_user_owns_the_quota_of_their_request(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(api, "API_KEY", "TESTKEY")
    uid = AUTH.create_user("a@example.com", PW)
    monkeypatch.setattr(collector, "collect_channel",
                        lambda key, ref, **kw: {"channelId": "UC1", "owner": quota_owner.current()})
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    c.post("/api/auth/login", json={"email": "a@example.com", "password": PW})
    assert c.post("/api/collect/channel", json={"channel": "@x"}).json()["owner"] == uid
    me = c.get("/api/auth/me").json()
    assert me["quota"]["limits"] == {"units": 3, "searchCalls": 1}
    assert quota_owner.current() is None                # reset after the request


def test_an_exhausted_budget_answers_429(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(api, "API_KEY", "TESTKEY")
    uid = AUTH.create_user("a@example.com", PW)
    spend(uid, n=3)

    def collect(key, ref, **kw):
        yt._get("channels", key)
        return {"channelId": "UC1"}
    monkeypatch.setattr(collector, "collect_channel", collect)
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    c.post("/api/auth/login", json={"email": "a@example.com", "password": PW})
    resp = c.post("/api/collect/channel", json={"channel": "@x"})
    assert resp.status_code == 429 and "your daily YouTube budget" in resp.text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

"""Features of a tariff: switched-off routes answer 403 FeatureOff, API-token
requests need api_access, MCP tools are refused the same way, disconnecting
an own channel always works, and the worker skips users without the feature.
Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_plan_features.py
"""
import asyncio
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
from application import notify_settings as NS  # noqa: E402
from application import own_channels as OWN  # noqa: E402
from application import plans as PL  # noqa: E402
from domain import plans as PD  # noqa: E402
from interfaces.mcp import auth as MA  # noqa: E402

PW = "correct horse battery"
OFF = {k: False for k in PD.FEATURES}


def setup_module(_=None):
    db.init_db()


@pytest.fixture
def user(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    conn = db.get_conn()
    for t in ("plan_changes", "user_plans", "sessions", "api_tokens", "user_settings",
              "own_channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM plans WHERE slug != 'free'")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    uid = AUTH.create_user("a@example.com", PW)
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": "a@example.com", "password": PW}).status_code == 200
    c.uid = uid
    return c


_n = [0]


def switch_off(uid, features=OFF):
    _n[0] += 1     # a test may switch twice; slugs are unique
    p = PL.create_plan({"slug": f"off{_n[0]}", "name": "Off", "features": dict(features)})
    PL.choose(uid, p["id"])


def test_routes_answer_feature_off(user):
    token = AUTH.create_api_token(user.uid, "ext")["token"]
    switch_off(user.uid)
    for method, path, feature in (("GET", "/api/own/channels", "own_channels"),
                                  ("GET", "/api/niche/demo/export.csv", "export"),
                                  ("PUT", "/api/settings/notifications", "notifications")):
        r = user.request(method, path, json={})
        assert r.status_code == 403, path
        assert r.json() == {"error": "FeatureOff", "feature": feature,
                            "detail": str(PD.FeatureOff(feature))}
    assert user.get("/api/own/status").status_code == 200          # stays open
    bare = TestClient(api.app, headers={"X-NF-Client": "tests", "Authorization": f"Bearer {token}"})
    r = bare.get("/api/saved")
    assert r.status_code == 403 and r.json()["feature"] == "api_access"


def test_disconnect_works_with_own_channels_off(user, monkeypatch):
    conn = db.get_conn()
    conn.execute("INSERT INTO own_channels (user_id, channel_id, title, connected_at) "
                 "VALUES (?,?,?,?)", (user.uid, "UCmine", "Mine", db.now_iso()))
    conn.commit()
    conn.close()
    monkeypatch.setattr(OWN.OA, "revoke", lambda t: True)
    switch_off(user.uid)
    assert user.delete("/api/own/channels/UCmine").status_code == 200


def test_topic_search_needs_feature(user, monkeypatch):
    import numpy as np

    from application import topic_watch as TW
    monkeypatch.setattr(TW, "_embed", lambda text: np.zeros(4, dtype=np.float32))
    switch_off(user.uid, {"topic_youtube_search": False})
    assert user.post("/api/topics", json={"text": "ai", "searchYoutube": True}).status_code == 403
    assert user.post("/api/topics", json={"text": "ai"}).status_code == 200


def test_mcp_blocked_feature(user):
    assert MA.blocked_feature(user.uid, "tools/list", None) is None
    switch_off(user.uid, {"own_channels": False})
    assert MA.blocked_feature(user.uid, "tools/call", {"name": "own_channels"}) == "own_channels"
    assert MA.blocked_feature(user.uid, "tools/call", {"name": "search_videos"}) is None
    assert MA.blocked_feature(user.uid, "tools/call", {"name": "rpm_calibration"}) == "own_channels"
    switch_off(user.uid, {"own_revenue": False})
    assert MA.blocked_feature(user.uid, "tools/call", {"name": "rpm_calibration"}) == "own_revenue"
    assert MA.blocked_feature(user.uid, "tools/call", {"name": "own_channels"}) is None
    switch_off(user.uid, {"own_channels": False})
    switch_off(user.uid)
    assert MA.blocked_feature(user.uid, "tools/list", None) == "api_access"


def test_mcp_middleware_refuses(user, monkeypatch):
    switch_off(user.uid)

    class Tok:
        client_id = str(user.uid)

    class Ctx:
        method = "tools/call"
        params = {"name": "own_channels"}

    monkeypatch.setattr(MA, "get_access_token", lambda: Tok())

    async def call_next(ctx):
        raise AssertionError("must not reach the tool")

    with pytest.raises(PD.FeatureOff):
        asyncio.run(MA.quota_owner_middleware(Ctx(), call_next))


def test_worker_skips_users_without_notifications(user):
    conn = db.get_conn()
    conn.execute("INSERT INTO user_settings (user_id, notify_mode, updated_at) VALUES (?,?,?)",
                 (user.uid, "instant", db.now_iso()))
    conn.commit()
    conn.close()
    assert user.uid in NS.recipients()
    switch_off(user.uid, {"notifications": False})
    assert user.uid not in NS.recipients()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

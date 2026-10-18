"""Count limits of a tariff through HTTP: at the limit a new row gets 403
PlanLimit, existing rows stay and stay readable, re-tracking a channel already
tracked is not a new row, and reconnecting an own channel already connected
is not blocked. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_plan_counts_http.py
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
from application import own_channels as OWN  # noqa: E402
from application import plans as PL  # noqa: E402

PW = "correct horse battery"
CH1, CH2 = "UC" + "a" * 22, "UC" + "b" * 22


def setup_module(_=None):
    db.init_db()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    conn = db.get_conn()
    for t in ("plan_changes", "user_plans", "tracked_channels", "saved_items", "drafts",
              "api_tokens", "sessions", "own_channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM plans WHERE slug != 'free'")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    uid = AUTH.create_user("a@example.com", PW)
    p = PL.create_plan({"slug": "tiny", "name": "Tiny", "limits": {
        "tracked_channels": 1, "saved_items": 1, "drafts": 1, "api_tokens": 1, "own_channels": 1}})
    PL.choose(uid, p["id"])
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": "a@example.com", "password": PW}).status_code == 200
    c.uid = uid
    return c


def test_track_limit_and_retrack(client):
    assert client.post("/api/channels/track", json={"channel_id": CH1}).status_code == 200
    assert client.post("/api/channels/track", json={"channel_id": CH1}).status_code == 200
    r = client.post("/api/channels/track", json={"channel_id": CH2})
    assert r.status_code == 403
    body = r.json()
    assert body["error"] == "PlanLimit" and body["limit"] == "tracked_channels" and body["max"] == 1


def test_saved_drafts_tokens_limits(client):
    assert client.post("/api/saved", json={"kind": "video", "refId": "v1"}).status_code == 200
    assert client.post("/api/saved", json={"kind": "video", "refId": "v2"}).status_code == 403
    assert client.post("/api/drafts", json={"title": "one"}).status_code == 200
    assert client.post("/api/drafts", json={"title": "two"}).status_code == 403
    assert client.post("/api/auth/tokens", json={"name": "x"}).status_code == 200
    assert client.post("/api/auth/tokens", json={"name": "y"}).status_code == 403


def test_downgrade_keeps_rows_readable(client):
    conn = db.get_conn()
    for ch in (CH1, CH2, "UC" + "c" * 22):
        db.track_channel(conn, ch, None, user_id=client.uid)
    conn.commit()
    conn.close()
    tracked = client.get("/api/channels/tracked").json()["channels"]
    assert len(tracked) == 3                                     # nothing deleted
    assert client.post("/api/channels/track",
                       json={"channel_id": "UC" + "d" * 22}).status_code == 403


def _fake_google(monkeypatch, channel_id):
    monkeypatch.setattr(OWN, "_require_configured", lambda connecting=True: None)
    monkeypatch.setattr(OWN, "_client", lambda: ("cid", "secret"))
    monkeypatch.setattr(OWN.OA, "exchange_code",
                        lambda *a, **k: {"refresh_token": "r", "access_token": "a", "scope": "s"})
    monkeypatch.setattr(OWN.YA, "mine_channel", lambda token: {
        "channelId": channel_id, "title": "Mine", "publishedAt": "2020-01-01T00:00:00Z"})
    monkeypatch.setattr(OWN.SEC, "encrypt", lambda s: b"x")
    monkeypatch.setattr(OWN, "sync", lambda **k: {"channels": [{}]})
    revoked = []
    monkeypatch.setattr(OWN.OA, "revoke", lambda token: revoked.append(token) or True)
    return revoked


def _pending(uid, state):
    conn = db.get_conn()
    conn.execute("INSERT INTO own_oauth_pending (state, user_id, code_verifier, created_at, "
                 "redirect_uri) VALUES (?,?,?,?,?)", (state, uid, "v", db.now_iso(), "http://x"))
    conn.commit()
    conn.close()


def test_own_channel_limit_allows_reconnect(client, monkeypatch):
    first = _fake_google(monkeypatch, CH1)
    _pending(client.uid, "s1")
    OWN.finish_connect("s1", "code")                            # first channel
    _pending(client.uid, "s2")
    OWN.finish_connect("s2", "code")                            # same channel again: fine
    assert first == []
    second = _fake_google(monkeypatch, CH2)
    _pending(client.uid, "s3")
    with pytest.raises(OWN.ConnectError) as e:
        OWN.finish_connect("s3", "code")
    assert "тариф" in str(e.value)
    assert second == ["r"]                                      # the new grant is revoked
    conn = db.get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM own_channels WHERE user_id = ?",
                         (client.uid,)).fetchone()[0]
    finally:
        conn.close()
    assert n == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

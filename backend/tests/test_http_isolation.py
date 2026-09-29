"""Per-user isolation through HTTP (plan 15, sub-stage 5.4): with
NF_MULTI_USER=1, two signed-in users each get their own watchlist, swipe
file, drafts, transcript queue and alert read marks, and B cannot read or
change A's through any of those routes. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_http_isolation.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402

PW = "correct horse battery"
CH = "UC" + "httpiso".ljust(22, "0")
NOW = datetime.now(timezone.utc)


def setup_module(_=None):
    db.init_db()


@pytest.fixture
def ab(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    conn = db.get_conn()
    for t in ("tracked_channels", "saved_items", "drafts", "transcript_requests", "event_reads",
              "events", "sessions", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                 (CH, "Shared channel", 1000))
    conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count) "
                 "VALUES (?,?,?,?,?)", ("httpisov1", CH, "v", (NOW - timedelta(days=2)).isoformat(), 10))
    conn.commit()
    conn.close()
    clients = []
    for email in ("a@example.com", "b@example.com"):
        AUTH.create_user(email, PW)
        c = TestClient(api.app, headers={"X-NF-Client": "tests"})
        assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
        clients.append(c)
    return clients


def test_watchlists_are_separate(ab):
    a, b = ab
    assert a.post("/api/channels/track", json={"channel_id": CH, "note": "A's"}).status_code == 200
    assert [c["channel_id"] for c in a.get("/api/channels/tracked").json()["channels"]] == [CH]
    assert b.get("/api/channels/tracked").json()["channels"] == []
    b.delete(f"/api/channels/tracked/{CH}")                       # B untracks nothing of A's
    assert [c["channel_id"] for c in a.get("/api/channels/tracked").json()["channels"]] == [CH]
    assert a.get(f"/api/inspect/channel?ref={CH}&fetch=false").json()["tracked"] is True
    assert b.get(f"/api/inspect/channel?ref={CH}&fetch=false").json()["tracked"] is False


def test_swipe_files_are_separate(ab):
    a, b = ab
    item = a.post("/api/saved", json={"kind": "video", "refId": "httpisov1", "folder": "ideas"}).json()
    assert b.get("/api/saved").json()["items"] == []
    assert b.get("/api/saved/folders").json()["folders"] == []
    assert b.delete(f"/api/saved/{item['id']}").status_code == 404
    assert [i["id"] for i in a.get("/api/saved").json()["items"]] == [item["id"]]


def test_drafts_are_separate(ab):
    a, b = ab
    d = a.post("/api/drafts", json={"title": "A's draft"}).json()
    assert b.get("/api/drafts").json()["drafts"] == []
    assert b.post(f"/api/drafts/{d['id']}/link", json={"videoId": "httpisov1"}).status_code == 404
    assert a.get("/api/drafts").json()["drafts"][0]["videoId"] is None
    assert a.post(f"/api/drafts/{d['id']}/link", json={"videoId": "httpisov1"}).status_code == 200


def test_transcript_queues_are_separate(ab):
    a, b = ab
    a.post("/api/transcripts/request", json={"videoId": "httpisov1", "reason": "A"})
    assert [q["videoId"] for q in a.get("/api/transcripts/queue").json()["queue"]] == ["httpisov1"]
    assert b.get("/api/transcripts/queue").json()["queue"] == []


def test_reindexing_a_transcript_touches_only_your_own_queue(ab, monkeypatch):
    a, b = ab
    from application import transcripts as TR
    monkeypatch.setattr(TR, "save_transcript", lambda vid, text, language=None, user_id=1:
                        {"videoId": vid, "userId": user_id})
    conn = db.get_conn()
    conn.execute("INSERT INTO transcripts (video_id, text, created_at) VALUES (?,?,?) "
                 "ON CONFLICT (video_id) DO NOTHING", ("httpisov1", "hello", db.now_iso()))
    conn.commit()
    conn.close()
    uid_b = [u["id"] for u in AUTH.list_users() if u["email"] == "b@example.com"][0]
    assert b.post("/api/transcripts/httpisov1/reindex").json()["userId"] == uid_b


def test_alert_read_marks_are_separate(ab):
    a, b = ab
    a.post("/api/channels/track", json={"channel_id": CH})
    b.post("/api/channels/track", json={"channel_id": CH})
    conn = db.get_conn()
    conn.execute("INSERT INTO events (kind, ref_id, payload, created_at, channel_id) VALUES (?,?,?,?,?)",
                 ("outlier", "httpisov1", '{"channelId": "%s"}' % CH, db.now_iso(), CH))
    conn.commit()
    conn.close()
    assert a.get("/api/events").json()["unseenCount"] == 1
    a.post("/api/events/seen", json={"all": True})
    assert a.get("/api/events").json()["unseenCount"] == 0
    assert b.get("/api/events").json()["unseenCount"] == 1        # A's read mark is A's


def test_a_signed_out_client_sees_nothing(ab):
    a, _ = ab
    a.post("/api/drafts", json={"title": "A's draft"})
    anon = TestClient(api.app, headers={"X-NF-Client": "tests"})
    for path in ("/api/drafts", "/api/saved", "/api/channels/tracked", "/api/events",
                 "/api/transcripts/queue", "/api/own/channels"):
        assert anon.get(path).status_code == 401, path


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

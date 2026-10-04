"""Tests for application/topic_watch.py (plan 19): a user names a topic, and
any video first seen in the last 48 hours (RSS, a niche collection, the
trending chart -- any source) whose embedding is close enough raises a
personal `topic_match` event: only the topic's owner sees it and gets it.

Throwaway schema; no network, zero quota. Embeddings are faked with fixed
unit vectors, so no model is loaded.
Run with pytest, or directly: python3 tests/test_topic_watch.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import alerts as AL  # noqa: E402
from application import topic_watch as TW  # noqa: E402

NOW = datetime.now(timezone.utc)
CH = "UC" + "topics".ljust(22, "0")
# three orthogonal "meanings" and a mix that is close to the first
AI, COOKING, HISTORY = np.eye(3, db.EMBEDDING_DIM, dtype=np.float32)
NEAR_AI = (AI * 0.9 + COOKING * 0.3) / np.linalg.norm(AI * 0.9 + COOKING * 0.3)
TEXTS = {"ai tools": AI, "cooking": COOKING}


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    monkeypatch.setattr(TW, "_embed", lambda text: TEXTS[text])
    conn = db.get_conn()
    for t in ("user_topics", "events", "event_reads", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO channels (channel_id, title) VALUES (?,?)", (CH, "Some channel"))
    conn.commit()
    conn.close()


def _video(vid, vec, seen_hours_ago=1, title=None):
    conn = db.get_conn()
    conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, first_seen_at, "
                 "embedding) VALUES (?,?,?,?,?,?)",
                 (vid, CH, title or vid, NOW.isoformat(),
                  (NOW - timedelta(hours=seen_hours_ago)).isoformat(),
                  None if vec is None else vec.astype(np.float32).tobytes()))
    conn.commit()
    conn.close()


def _backdate_topic(tid, hours):
    conn = db.get_conn()
    conn.execute("UPDATE user_topics SET created_at = ? WHERE id = ?",
                 ((NOW - timedelta(hours=hours)).isoformat(), tid))
    conn.commit()
    conn.close()


def test_add_list_pause_and_remove_a_topic():
    t = TW.add_topic("ai tools", threshold=0.7)
    assert t["text"] == "ai tools" and t["threshold"] == 0.7 and t["paused"] is False
    assert [x["id"] for x in TW.list_topics()] == [t["id"]]
    assert TW.set_paused(t["id"], True)["paused"] is True
    assert TW.remove_topic(t["id"]) == {"removed": True}
    assert TW.list_topics() == []


def test_an_empty_topic_or_a_bad_threshold_is_rejected():
    with pytest.raises(ValueError):
        TW.add_topic("   ")
    with pytest.raises(ValueError):
        TW.add_topic("ai tools", threshold=1.5)


def test_a_close_new_video_raises_one_personal_event():
    t = TW.add_topic("ai tools", threshold=0.8)
    _backdate_topic(t["id"], 24)
    _video("vnear", NEAR_AI, title="New AI agent")
    _video("vfar", HISTORY)
    _video("vnoemb", None)
    res = TW.match_new()
    assert res == {"topics": 1, "videosChecked": 2, "matched": 1}
    ev = AL.list_events(kind="topic_match")
    assert len(ev) == 1
    p = ev[0]["payload"]
    assert p["videoId"] == "vnear" and p["topic"] == "ai tools" and p["similarity"] >= 0.8
    assert TW.match_new()["matched"] == 0            # no duplicate on the next run


def test_videos_seen_before_the_topic_existed_do_not_flood_it():
    _video("vold", NEAR_AI, seen_hours_ago=5)
    t = TW.add_topic("ai tools", threshold=0.8)
    _backdate_topic(t["id"], 2)
    assert TW.match_new()["matched"] == 0


def test_videos_older_than_the_window_and_paused_topics_are_skipped():
    t = TW.add_topic("ai tools", threshold=0.8)
    _backdate_topic(t["id"], 100)
    _video("vstale", NEAR_AI, seen_hours_ago=60)
    assert TW.match_new()["matched"] == 0
    _video("vfresh", NEAR_AI)
    TW.set_paused(t["id"], True)
    assert TW.match_new()["matched"] == 0


def test_only_the_owner_sees_a_topic_match():
    t = TW.add_topic("cooking", threshold=0.8, user_id=2)
    _backdate_topic(t["id"], 24)
    _video("vcook", COOKING)
    TW.match_new()
    assert [e["kind"] for e in AL.list_events(user_id=2)] == ["topic_match"]
    assert AL.list_events(user_id=1) == []
    assert AL.unseen_count(user_id=2) == 1 and AL.unseen_count(user_id=1) == 0
    assert TW.list_topics(user_id=1) == []
    assert TW.remove_topic(t["id"], user_id=1) == {"removed": False}   # not yours


# ------------------------------------------------- HTTP and MCP (plan 19)

def test_http_routes_add_list_pause_and_delete_a_topic():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    client = TestClient(api.app)
    r = client.post("/api/topics", json={"text": "ai tools", "threshold": 0.7})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert [t["id"] for t in client.get("/api/topics").json()["topics"]] == [tid]
    assert client.post(f"/api/topics/{tid}/pause", json={"paused": True}).json()["paused"] is True
    assert client.post("/api/topics", json={"text": ""}).status_code == 400
    assert client.delete(f"/api/topics/{tid}", headers={"X-NF-Client": "test"}).json() == {"removed": True}
    assert client.delete(f"/api/topics/{tid}", headers={"X-NF-Client": "test"}).status_code == 404


def test_mcp_tools_watch_list_and_unwatch():
    import interfaces.mcp.server as srv
    t = srv.watch_topic("cooking", threshold=0.75)
    assert [x["text"] for x in srv.list_watched_topics()] == ["cooking"]
    assert srv.unwatch_topic(t["id"]) == {"removed": True}


# ------------------------------------------- opt-in YouTube search per topic

class _FakeEmb:
    @staticmethod
    def embed(text):
        return NEAR_AI if "agent" in text.lower() else HISTORY

    @staticmethod
    def to_blob(v):
        return np.asarray(v, dtype=np.float32).tobytes()


@pytest.fixture
def yt_api(monkeypatch):
    import infrastructure.youtube.client as yt
    from application import collecting as collector
    calls = {"search": [], "videos": []}

    def search_videos(key, query, **kw):
        calls["search"].append((query, kw.get("published_after"), kw.get("order")))
        return {"items": [{"id": {"videoId": "vs1"}}, {"id": {"videoId": "vs2"}}]}

    def videos_list(key, ids, parts=None):
        calls["videos"].append(list(ids))
        titles = {"vs1": "My AI agent does my taxes", "vs2": "Medieval castles"}
        return [{"id": i, "snippet": {"channelId": CH, "title": titles[i], "description": "",
                                      "publishedAt": NOW.isoformat(), "thumbnails": {}},
                 "statistics": {"viewCount": "10"}, "contentDetails": {"duration": "PT5M"}}
                for i in ids]

    monkeypatch.setattr(yt, "search_videos", search_videos)
    monkeypatch.setattr(yt, "videos_list", videos_list)
    monkeypatch.setattr(yt, "channels_list", lambda key, ids, *a, **k: [])
    monkeypatch.setattr(collector, "_embeddings", lambda: _FakeEmb)
    conn = db.get_conn()
    conn.execute("DELETE FROM meta WHERE key LIKE 'search_calls%'")
    conn.commit()
    conn.close()
    return calls


def test_only_opted_in_topics_search_youtube_and_new_videos_then_match(yt_api):
    on = TW.add_topic("ai tools", threshold=0.8, search_youtube=True)
    TW.add_topic("cooking", threshold=0.8)                        # not opted in
    _backdate_topic(on["id"], 24)
    res = TW.search_topics("key")
    assert res["topicsSearched"] == 1 and res["videosStored"] == 2
    query, after, order = yt_api["search"][0]
    assert query == "ai tools" and after and order == "date"
    assert TW.match_new()["matched"] == 1                         # vs1 is close, vs2 is not
    assert TW.search_topics("key")["topicsSearched"] == 0         # once a day per topic


def test_known_videos_are_not_fetched_again(yt_api):
    TW.add_topic("ai tools", search_youtube=True)
    _video("vs1", NEAR_AI)
    TW.search_topics("key")
    assert yt_api["videos"] == [["vs2"]]


def test_the_search_toggle_and_the_daily_cap(yt_api, monkeypatch):
    t = TW.add_topic("ai tools")
    assert t["searchYoutube"] is False
    assert TW.set_search(t["id"], True)["searchYoutube"] is True
    monkeypatch.setattr(TW, "SEARCH_MAX_PER_DAY", 0)
    assert TW.search_topics("key")["topicsSearched"] == 0 and yt_api["search"] == []

"""Tests for application/freshness.py (plan 16): stored YouTube data older
than REFRESH_STALE_DAYS is re-read from the API -- titles, descriptions and
counters -- and nothing is ever deleted (the user's call, 2026-10-04: keep
every row). A video the API no longer returns is marked in gone_items.

Throwaway schema (tests/schema_scope.py), no YouTube key and no network:
every client call is monkeypatched.
Run with pytest, or directly: python3 tests/test_freshness.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
from application import freshness as F  # noqa: E402

KEY = "test-key"
NOW = datetime.now(timezone.utc)
CH = "UC" + "fresh".ljust(22, "0")


def setup_module(_=None):
    db.init_db()


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("gone_items", "video_stats_history", "channel_stats_history", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO channels (channel_id, title, description, subscriber_count, "
                 "video_count, view_count, updated_at) VALUES (?,?,?,?,?,?,?)",
                 (CH, "old channel title", "old", 10, 3, 100, _iso(40)))
    for vid, age in (("vstale1", 40), ("vstale2", 30), ("vfresh", 2), ("vgone", 60)):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, description, published_at, "
                     "view_count, updated_at, region) VALUES (?,?,?,?,?,?,?,?)",
                     (vid, CH, f"old {vid}", "old description", _iso(400), 1, _iso(age), "US"))
        conn.execute("INSERT INTO video_stats_history (video_id, captured_at, view_count) "
                     "VALUES (?,?,?)", (vid, _iso(age), 1))
    conn.commit()
    conn.close()


def _api_video(vid):
    return {"id": vid,
            "snippet": {"channelId": CH, "title": f"new {vid}", "description": "new description",
                        "publishedAt": _iso(400), "thumbnails": {}},
            "statistics": {"viewCount": "999", "likeCount": "5", "commentCount": "1"},
            "contentDetails": {"duration": "PT10M"}}


def _api_channel(cid):
    return {"id": cid, "snippet": {"title": "new channel title", "description": "new",
                                   "thumbnails": {}},
            "statistics": {"subscriberCount": "20", "videoCount": "3", "viewCount": "200"},
            "contentDetails": {}}


@pytest.fixture
def api(monkeypatch):
    calls = {"videos": [], "channels": []}

    def videos_list(key, ids, parts=yt.VIDEO_PARTS):
        calls["videos"].append(list(ids))
        return [_api_video(v) for v in ids if v != "vgone"]

    def channels_list(key, ids, *a, **kw):
        calls["channels"].append(list(ids))
        return [_api_channel(c) for c in ids]

    monkeypatch.setattr(yt, "videos_list", videos_list)
    monkeypatch.setattr(yt, "channels_list", channels_list)
    return calls


def _row(table, key, value):
    conn = db.get_conn()
    r = conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).fetchone()
    conn.close()
    return dict(r) if r else None


def _count(table):
    conn = db.get_conn()
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def test_status_counts_rows_older_than_the_refresh_age():
    s = F.status()
    assert s["staleDays"] == 25
    assert s["videos"]["stale"] == 3 and s["videos"]["total"] == 4
    assert s["channels"]["stale"] == 1
    assert s["deletes"] is False


def test_stale_videos_and_channels_are_re_read_and_nothing_is_deleted(api):
    before = _count("video_stats_history")
    res = F.refresh_stale(KEY)
    assert sorted(api["videos"][0]) == ["vgone", "vstale1", "vstale2"]   # oldest first, fresh skipped
    assert res["videos"] == {"requested": 3, "refreshed": 2, "missing": 1}
    v = _row("videos", "video_id", "vstale1")
    assert v["title"] == "new vstale1" and v["description"] == "new description"
    assert v["view_count"] == 999 and v["region"] == "US"        # a column the API lacks is kept
    assert _row("channels", "channel_id", CH)["title"] == "new channel title"
    # nothing removed: the gone video and every old snapshot stay
    assert _row("videos", "video_id", "vgone")["title"] == "old vgone"
    assert _count("video_stats_history") == before + 2
    assert _row("gone_items", "ref_id", "vgone")["miss_count"] == 1
    assert F.status()["videos"]["stale"] == 1                      # only the gone one is left


def test_the_daily_cap_limits_how_many_rows_are_read(api):
    res = F.refresh_stale(KEY, max_videos=1, max_channels=0)
    assert res["videos"]["requested"] == 1 and api["videos"][0] == ["vgone"]
    assert api["channels"] == []


def test_a_confirmed_gone_video_is_not_asked_for_again(api):
    conn = db.get_conn()
    conn.execute("INSERT INTO gone_items (kind, ref_id, first_missing_at, last_missing_at, "
                 "miss_count, confirmed_at) VALUES ('video','vgone',?,?,2,?)",
                 (_iso(3), _iso(1), _iso(1)))
    conn.commit()
    conn.close()
    F.refresh_stale(KEY)
    assert "vgone" not in api["videos"][0]


def test_quota_exceeded_changes_nothing(monkeypatch):
    def boom(*a, **kw):
        raise yt.QuotaExceeded("quota")
    monkeypatch.setattr(yt, "videos_list", boom)
    with pytest.raises(yt.QuotaExceeded):
        F.refresh_stale(KEY)
    assert _row("videos", "video_id", "vstale1")["title"] == "old vstale1"
    assert _count("gone_items") == 0

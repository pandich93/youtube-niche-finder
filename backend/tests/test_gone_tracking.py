"""Tests for "channel/video is gone" tracking in application/collecting.py
(plan 04): refresh_channels / refresh_stats record ids the API stopped
returning into gone_items, confirm them only after a second miss at least
GONE_MIN_HOURS later, and never record anything when the API call itself
failed (quota, network) -- otherwise an exhausted quota would look like every
channel vanished at once.

Same throwaway-schema setup as test_mcp_tools.py (tests/schema_scope.py), no
YouTube key and no network: every client call is monkeypatched.
Run with pytest, or directly: python3 tests/test_gone_tracking.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
from application import collecting as collector  # noqa: E402

KEY = "test-key"
CH_ALIVE = "UC" + "gonealive".ljust(22, "0")
CH_GONE = "UC" + "gonegone".ljust(22, "0")


def setup_module(_=None):
    db.init_db()


def _clean():
    conn = db.get_conn()
    conn.execute("DELETE FROM gone_items")
    conn.commit()
    conn.close()


def _gone_rows(kind=None):
    conn = db.get_conn()
    sql, params = "SELECT * FROM gone_items", ()
    if kind:
        sql, params = sql + " WHERE kind=?", (kind,)
    rows = {r["ref_id"]: dict(r) for r in conn.execute(sql, params).fetchall()}
    conn.close()
    return rows


def _backdate_first_miss(kind, ref_id, hours):
    then = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = db.get_conn()
    conn.execute("UPDATE gone_items SET first_missing_at=? WHERE kind=? AND ref_id=?",
                 (then, kind, ref_id))
    conn.commit()
    conn.close()


def _api_channel(cid):
    return {"id": cid,
            "snippet": {"title": cid, "publishedAt": "2020-01-01T00:00:00Z", "thumbnails": {}},
            "statistics": {"subscriberCount": "1000", "videoCount": "10", "viewCount": "100000"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}}}


def _api_video(vid, cid="UCgonevideoowner0000001"):
    return {"id": vid,
            "snippet": {"channelId": cid, "title": vid, "publishedAt": "2026-09-01T00:00:00Z",
                        "thumbnails": {}},
            "statistics": {"viewCount": "500", "likeCount": "5", "commentCount": "1"},
            "contentDetails": {"duration": "PT10M"}}


def _seed_videos(*vids):
    conn = db.get_conn()
    for vid in vids:
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at) "
                     "VALUES (?,?,?,?) ON CONFLICT (video_id) DO NOTHING",
                     (vid, "UCgonevideoowner0000001", vid, "2026-09-01T00:00:00+00:00"))
    conn.commit()
    conn.close()


def _quota_exceeded(*a, **k):
    raise yt.QuotaExceeded("YouTube API quota exceeded: test")


# ------------------------------------------------------------ channels

def test_missing_channel_becomes_an_unconfirmed_candidate(monkeypatch):
    _clean()
    monkeypatch.setattr(yt, "channels_list", lambda k, ids, **kw: [_api_channel(CH_ALIVE)])
    out = collector.refresh_channels(KEY, channel_ids=[CH_ALIVE, CH_GONE])
    assert out["gone"] == {"missing": 1, "confirmed": 0}, out
    rows = _gone_rows("channel")
    assert list(rows) == [CH_GONE]
    assert rows[CH_GONE]["miss_count"] == 1
    assert rows[CH_GONE]["confirmed_at"] is None


def test_second_miss_after_min_hours_confirms_the_channel(monkeypatch):
    _clean()
    monkeypatch.setattr(yt, "channels_list", lambda k, ids, **kw: [_api_channel(CH_ALIVE)])
    collector.refresh_channels(KEY, channel_ids=[CH_ALIVE, CH_GONE])
    _backdate_first_miss("channel", CH_GONE, hours=7)
    out = collector.refresh_channels(KEY, channel_ids=[CH_ALIVE, CH_GONE])
    assert out["gone"] == {"missing": 1, "confirmed": 1}, out
    row = _gone_rows("channel")[CH_GONE]
    assert row["miss_count"] == 2
    assert row["confirmed_at"] is not None


def test_second_miss_too_soon_stays_unconfirmed(monkeypatch):
    _clean()
    monkeypatch.setattr(yt, "channels_list", lambda k, ids, **kw: [_api_channel(CH_ALIVE)])
    collector.refresh_channels(KEY, channel_ids=[CH_ALIVE, CH_GONE])
    out = collector.refresh_channels(KEY, channel_ids=[CH_ALIVE, CH_GONE])
    assert out["gone"] == {"missing": 1, "confirmed": 0}, out
    assert _gone_rows("channel")[CH_GONE]["confirmed_at"] is None


def test_channel_that_comes_back_is_cleared(monkeypatch):
    _clean()
    monkeypatch.setattr(yt, "channels_list", lambda k, ids, **kw: [_api_channel(CH_ALIVE)])
    collector.refresh_channels(KEY, channel_ids=[CH_ALIVE, CH_GONE])
    monkeypatch.setattr(yt, "channels_list",
                        lambda k, ids, **kw: [_api_channel(CH_ALIVE), _api_channel(CH_GONE)])
    out = collector.refresh_channels(KEY, channel_ids=[CH_ALIVE, CH_GONE])
    assert out["gone"] == {"missing": 0, "confirmed": 0}, out
    assert _gone_rows("channel") == {}


def test_quota_exceeded_on_channels_records_nothing(monkeypatch):
    _clean()
    monkeypatch.setattr(yt, "channels_list", _quota_exceeded)
    try:
        collector.refresh_channels(KEY, channel_ids=[CH_ALIVE, CH_GONE])
    except yt.QuotaExceeded:
        pass
    else:
        raise AssertionError("QuotaExceeded must propagate to the caller")
    assert _gone_rows() == {}


def test_non_channel_id_refs_are_never_recorded_as_gone(monkeypatch):
    _clean()
    monkeypatch.setattr(yt, "channels_list", lambda k, ids, **kw: [])
    out = collector.refresh_channels(KEY, channel_ids=["@somehandle"])
    assert out["gone"] == {"missing": 0, "confirmed": 0}, out
    assert _gone_rows() == {}


# ------------------------------------------------------------ videos

def test_missing_video_is_recorded_even_when_failed_list_is_empty(monkeypatch):
    # videos_batch_get_stats falls back to videos.list on error, and then
    # reports failed=[] -- so a gone video is only visible as "requested but
    # not returned", never via `failed`.
    _clean()
    _seed_videos("vgonealive1", "vgonegone1")
    monkeypatch.setattr(yt, "videos_batch_get_stats",
                        lambda k, ids, **kw: ([_api_video("vgonealive1")], []))
    out = collector.refresh_stats(KEY, scope="all", limit=5000)
    assert out["gone"]["missing"] == 1, out
    assert "vgonegone1" in _gone_rows("video")
    assert "vgonealive1" not in _gone_rows("video")


def test_quota_exceeded_on_videos_records_nothing(monkeypatch):
    _clean()
    _seed_videos("vgonealive1", "vgonegone1")
    monkeypatch.setattr(yt, "videos_batch_get_stats", _quota_exceeded)
    try:
        collector.refresh_stats(KEY, scope="all", limit=5000)
    except yt.QuotaExceeded:
        pass
    else:
        raise AssertionError("QuotaExceeded must propagate to the caller")
    assert _gone_rows() == {}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

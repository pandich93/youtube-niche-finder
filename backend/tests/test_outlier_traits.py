"""Tests for application/outlier_traits.py (plan 29) and its HTTP/MCP doors:
a niche's outliers against its ordinary videos, long-form and Shorts apart.
Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_outlier_traits.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import outlier_traits as OT  # noqa: E402

NOW = datetime.now(timezone.utc)
NICHE = "traits-niche"


def iso(days):
    return (NOW - timedelta(days=days)).isoformat()


def setup_module(_=None):
    db.init_db()


def _video(conn, vid, cid, title, views, days, duration, short=0, tags=("a", "b")):
    conn.execute(
        "INSERT INTO videos (video_id, channel_id, title, published_at, first_seen_at, view_count, "
        "duration_seconds, is_short, tags) VALUES (?,?,?,?,?,?,?,?,?)",
        (vid, cid, title, iso(days), iso(days), views, duration, short, json.dumps(list(tags))))
    conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (vid, NICHE))


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("video_stats_history", "video_niches", "videos", "channels", "niches"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", (NICHE, NICHE, NICHE))
    # 12 channels: 11 plain uploads at 1,000 views, then one hit at 8,000 whose title has a number
    # and a question and which is shorter than the plain ones
    for c in range(12):
        cid = f"UCt{c}"
        conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                     (cid, f"T{c}", 5000))
        for i in range(11):
            _video(conn, f"t{c}-{i}", cid, "a plain title", 1_000, 120 - i * 5, 600)
        _video(conn, f"t{c}-hit", cid, "7 ways to win?", 8_000, 10, 300, tags=("a", "b", "c", "d", "e", "f"))
    # a few Shorts: far too few to compare
    for i in range(3):
        _video(conn, f"s{i}", "UCt0", "short", 100, 20 + i, 30, short=1)
    conn.commit()
    conn.close()


def test_long_form_outliers_are_compared_with_ordinary_videos():
    r = OT.outlier_traits(NICHE)
    f = r["formats"]["long"]
    assert f["reliable"] and f["outliers"] == 12 and f["ordinary"] >= 10
    sig = {t["key"]: t for t in f["traits"] if t["significant"]}
    assert {"number", "question", "duration", "tags"} <= set(sig)
    assert sig["number"]["outliers"] == 100.0 and sig["number"]["ordinary"] == 0.0
    assert sig["duration"]["direction"] == "less" and sig["tags"]["direction"] == "more"
    assert f["significant"] == len(sig) and f["videos"] == 12 * 12
    assert not f["concentrated"] and f["outlierChannels"] == 12


def test_shorts_are_kept_apart_and_a_tiny_group_says_so():
    s = OT.outlier_traits(NICHE)["formats"]["short"]
    assert not s["reliable"] and s["reason"] == "few-videos" and s["videos"] == 3 and s["traits"] == []


def test_the_thresholds_are_echoed_and_can_be_moved():
    r = OT.outlier_traits(NICHE, min_outlier=50.0)
    assert r["minOutlier"] == 50.0 and r["formats"]["long"]["outliers"] == 0
    assert r["formats"]["long"]["reason"] == "few-videos"


def test_an_empty_niche_has_a_hint_not_an_error():
    r = OT.outlier_traits("no-such-niche")
    assert r["videos"] == 0 and "hint" in r and not r["formats"]["long"]["reliable"]


def test_tags_stored_as_text_or_junk_are_counted_or_skipped():
    assert OT._tags_count('["a","b"]') == 2 and OT._tags_count(["a"]) == 1
    assert OT._tags_count(None) == 0
    assert OT._tags_count("not json") is None and OT._tags_count('{"a":1}') is None


def test_http_and_mcp_doors():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    r = TestClient(api.app).get(f"/api/niches/{NICHE}/outlier-traits")
    assert r.status_code == 200 and r.json()["formats"]["long"]["outliers"] == 12
    assert srv.outlier_traits(NICHE)["formats"]["long"]["reliable"] is True
    assert srv.outlier_traits()["videos"] == 12 * 12 + 3

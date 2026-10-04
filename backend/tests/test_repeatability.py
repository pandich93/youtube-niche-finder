"""Tests for application/repeatability.py (plan 21) and its HTTP/MCP doors:
neighbours of a video on OTHER channels, scored with the usual outlier
baseline, turned into a verdict. Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_repeatability.py
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
from application import repeatability as RP  # noqa: E402

NOW = datetime.now(timezone.utc)
rng = np.random.default_rng(7)


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


TARGET = _unit(rng.normal(size=db.EMBEDDING_DIM))


def _near():
    return _unit(TARGET + 0.15 * rng.normal(size=db.EMBEDDING_DIM) / np.sqrt(db.EMBEDDING_DIM))


def _far():
    return _unit(rng.normal(size=db.EMBEDDING_DIM))


def setup_module(_=None):
    db.init_db()


def _channel(conn, cid):
    db.upsert_channel(conn, {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None, "description": "",
        "default_language": None, "subscriber_count": 10_000, "video_count": 10,
        "view_count": 1, "thumbnail": None, "published_at": None, "topic_categories": None,
        "keywords": None, "uploads_playlist": None, "hidden_subs": 0})


def _video(conn, vid, cid, views, days_ago, vec, title=None):
    db.upsert_video(conn, {
        "video_id": vid, "channel_id": cid, "title": title or vid, "description": "",
        "published_at": (NOW - timedelta(days=days_ago)).isoformat(), "duration_seconds": 600,
        "view_count": views, "like_count": 0, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": vec.tobytes(),
        "updated_at": NOW.isoformat(), "category_id": None, "region": None, "is_short": 0,
        "topic_categories": None, "live_content": None})


def _channel_with(conn, cid, near_views, title=None):
    """5 baseline uploads at 1,000 views, then one on-format video."""
    _channel(conn, cid)
    for i in range(5):
        _video(conn, f"{cid}-b{i}", cid, 1_000, 200 - i * 10, _far())
    _video(conn, f"{cid}-hit", cid, near_views, 60, _near(), title=title)


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("video_niches", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    _channel_with(conn, "UCsrc", 8_000, title="I built an AI that edits my videos")
    conn.commit()
    conn.close()


def _others(*views):
    conn = db.get_conn()
    for i, v in enumerate(views):
        _channel_with(conn, f"UCo{i}", v, title="I built an AI that does my taxes")
    conn.commit()
    conn.close()


def test_a_format_that_worked_on_three_other_channels_is_repeatable():
    _others(3_000, 4_000, 2_500, 800, 900)
    r = RP.format_repeatability("UCsrc-hit")
    assert r["verdict"] == "repeatable", r
    assert r["channelsHit"] == 3 and r["channels"] >= 3
    assert all(e["channelId"] != "UCsrc" for e in r["examples"])
    assert r["titleOpening"] == {"opening": "i built an", "otherChannels": 5}


def test_title_openings_match_through_punctuation_and_emoji():
    conn = db.get_conn()
    _channel(conn, "UCpunct")
    _video(conn, "UCpunct-1", "UCpunct", 10, 30, _far(), title="I: Built an AI that cooks")
    _channel(conn, "UCemoji")
    _video(conn, "UCemoji-1", "UCemoji", 10, 30, _far(), title="🔥 I built an AI for my cat")
    conn.commit()
    conn.close()
    assert RP.format_repeatability("UCsrc-hit")["titleOpening"]["otherChannels"] == 2


def test_nobody_else_got_an_outlier_is_one_off():
    _others(900, 800, 1_000, 700, 950)
    assert RP.format_repeatability("UCsrc-hit")["verdict"] == "one_off"


def test_too_few_neighbours_is_unknown():
    _others(3_000)
    r = RP.format_repeatability("UCsrc-hit")
    assert r["verdict"] == "unknown" and r["reason"] == "few-similar-videos"


def test_a_video_without_an_embedding_says_so():
    conn = db.get_conn()
    conn.execute("UPDATE videos SET embedding = NULL, embedding_v = NULL WHERE video_id = 'UCsrc-hit'")
    conn.commit()
    conn.close()
    r = RP.format_repeatability("UCsrc-hit")
    assert r["verdict"] == "unknown" and r["reason"] == "no-embedding" and r["hint"]


def test_http_and_mcp_doors():
    _others(3_000, 4_000, 2_500, 800, 900)
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    r = TestClient(api.app).get("/api/videos/UCsrc-hit/repeatability")
    assert r.status_code == 200 and r.json()["verdict"] == "repeatable"
    assert srv.format_repeatability("UCsrc-hit")["verdict"] == "repeatable"

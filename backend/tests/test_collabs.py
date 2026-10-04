"""Tests for application/collabs.py (plan 31) and its HTTP/MCP doors:
similar channels of your size, active and not templated.
Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_collabs.py
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
from application import collabs as CO  # noqa: E402

NOW = datetime.now(timezone.utc)
rng = np.random.default_rng(31)


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


TOPIC = _unit(rng.normal(size=db.EMBEDDING_DIM))


def _near(k=0.15):
    return _unit(TOPIC + k * rng.normal(size=db.EMBEDDING_DIM) / np.sqrt(db.EMBEDDING_DIM))


def iso(days):
    return (NOW - timedelta(days=days)).isoformat()


def setup_module(_=None):
    db.init_db()


def _channel(conn, cid, subs, last_upload_days, vec=None, hidden=0, titles=None):
    db.upsert_channel(conn, {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None, "description": "",
        "default_language": None, "subscriber_count": subs, "video_count": 3, "view_count": 1,
        "thumbnail": None, "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": hidden})
    for i in range(3):
        db.upsert_video(conn, {
            "video_id": f"{cid}-{i}", "channel_id": cid,
            "title": (titles or [f"{cid} topic video {i}"] * 3)[i],
            "description": "", "published_at": iso(last_upload_days + i * 20), "duration_seconds": 600,
            "view_count": 1_000, "like_count": 0, "comment_count": 0, "thumbnail": None,
            "tags": "[]", "default_language": "en",
            "embedding": (vec if vec is not None else _near()).tobytes(),
            "updated_at": NOW.isoformat(), "category_id": None, "region": None, "is_short": 0,
            "topic_categories": None, "live_content": None})


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("channel_stats_history", "video_niches", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    _channel(conn, "UCme", 1_000, 2)
    _channel(conn, "UCpeer", 1_200, 5)            # passes
    _channel(conn, "UCpeer2", 800, 10)            # passes, grows faster
    _channel(conn, "UCbig", 50_000, 1)            # too big
    _channel(conn, "UCtiny", 100, 1)              # too small
    _channel(conn, "UCsleepy", 1_000, 90)         # inactive
    _channel(conn, "UCother", 1_000, 1, vec=_unit(rng.normal(size=db.EMBEDDING_DIM)))   # off-topic
    for cid, first, last in (("UCpeer2", 700, 800), ("UCpeer", 1_190, 1_200)):
        conn.execute("INSERT INTO channel_stats_history (channel_id, captured_at, subscriber_count) "
                     "VALUES (?,?,?), (?,?,?)", (cid, iso(20), first, cid, iso(1), last))
    conn.commit()
    conn.close()


def test_only_similar_channels_of_your_size_and_active_are_kept():
    r = CO.collab_candidates("UCme")
    ids = [c["channelId"] for c in r["candidates"]]
    assert set(ids) == {"UCpeer", "UCpeer2"}
    assert r["excluded"]["off-topic"] == 1 and r["excluded"]["too-big"] == 1
    assert r["excluded"]["too-small"] == 1
    assert r["excluded"]["inactive"] == 1 and r["subscribers"] == 1_000


def test_every_candidate_carries_the_numbers_that_picked_it():
    c = next(x for x in CO.collab_candidates("UCme")["candidates"] if x["channelId"] == "UCpeer2")
    assert c["sizeRatio"] == 0.8 and c["daysSinceUpload"] <= 11 and c["growth30dPct"] == 14.3
    assert c["similarity"] > 0.5 and "templateRisk" in c


def test_a_templated_channel_is_left_out(monkeypatch):
    real = CO.TRA.template_risk
    monkeypatch.setattr(CO.TRA, "template_risk", lambda cid, *a, **k: (
        {"found": True, "level": "high"} if cid == "UCpeer" else real(cid)))
    r = CO.collab_candidates("UCme")
    assert "UCpeer" not in [c["channelId"] for c in r["candidates"]]
    assert r["excluded"]["templated"] == 1


def test_the_band_can_be_widened():
    ids = [c["channelId"] for c in CO.collab_candidates("UCme", max_ratio=100)["candidates"]]
    assert "UCbig" in ids
    with pytest.raises(ValueError):
        CO.collab_candidates("UCme", min_ratio=3, max_ratio=2)


def test_limit_and_missing_or_hidden_channels():
    assert len(CO.collab_candidates("UCme", limit=1)["candidates"]) == 1
    assert CO.collab_candidates("UCnope")["found"] is False
    conn = db.get_conn()
    conn.execute("UPDATE channels SET hidden_subs = 1 WHERE channel_id = 'UCme'")
    conn.commit()
    conn.close()
    r = CO.collab_candidates("UCme")
    assert r["candidates"] == [] and "hides" in r["hint"]


def test_http_and_mcp_doors():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    c = TestClient(api.app)
    r = c.get("/api/channels/UCme/collabs")
    assert r.status_code == 200 and len(r.json()["candidates"]) >= 2
    assert c.get("/api/channels/UCme/collabs", params={"min_ratio": 0}).status_code == 400
    assert len(srv.collab_candidates("UCme")["candidates"]) >= 2
    assert "error" in srv.collab_candidates("UCme", min_ratio=5)

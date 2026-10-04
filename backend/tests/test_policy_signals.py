"""Tests for application/policy_signals.py (plan 22) and its HTTP/MCP doors.
Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_policy_signals.py
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
from application import policy_signals as PS  # noqa: E402

NOW = datetime.now(timezone.utc)
CH_SHOCK = "UC" + "psshock".ljust(22, "0")
CH_AIDOC = "UC" + "psaidoc".ljust(22, "0")


def setup_module(_=None):
    db.init_db()


def _channel(conn, cid, labels=None):
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count, llm_labels) "
                 "VALUES (?,?,?,?)", (cid, cid, 1000, json.dumps(labels) if labels else None))


def _videos(conn, cid, titles, topics=None, synthetic=None):
    for i, t in enumerate(titles):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, duration_seconds, "
                     "topic_categories, contains_synthetic_media) VALUES (?,?,?,?,?,?,?)",
                     (f"{cid}-{i}", cid, t, (NOW - timedelta(days=3 * i)).isoformat(), 600,
                      json.dumps(topics) if topics else None, synthetic))
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?, 'psniche')",
                     (f"{cid}-{i}",))


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("video_niches", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    _channel(conn, CH_SHOCK)
    _videos(conn, CH_SHOCK, [f"SHOCKING twist number {i} 😱" if i % 3 else f"calm video {i}"
                             for i in range(12)])
    _channel(conn, CH_AIDOC, labels={"is_faceless": True})
    _videos(conn, CH_AIDOC, [f"Your symptoms explained, part {i}" for i in range(12)],
            topics=["Health"], synthetic=1)
    conn.commit()
    conn.close()


def test_shock_titles_flag_the_unsatisfying_category():
    r = PS.channel_policy_signals(CH_SHOCK)
    assert r["found"] and r["categories"]["unsatisfying"]["level"] == "high"
    assert r["categories"]["ai_persona_sensitive"]["level"] == "none"
    assert r["quotaUsed"] == 0


def test_an_ai_health_channel_is_watched_never_high():
    r = PS.channel_policy_signals(CH_AIDOC)
    cat = r["categories"]["ai_persona_sensitive"]
    assert cat["level"] == "watch" and cat["value"] == 1.0
    signals = {x["signal"]: x for x in cat["reasons"]}
    assert signals["sensitiveShare"]["topic"] == "health" and "faceless" in signals


def test_unknown_channel():
    assert PS.channel_policy_signals("UCnope")["found"] is False


def test_niche_counts_channels_per_category():
    r = PS.niche_policy_signals("psniche")
    assert r["channels"] == 2
    assert r["categories"]["unsatisfying"]["high"] == 1
    assert r["categories"]["ai_persona_sensitive"]["watch"] == 1
    assert r["categories"]["ai_persona_sensitive"]["shareFlagged"] == 0.5
    assert PS.niche_policy_signals("nope")["found"] is False


def test_http_and_mcp_doors():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    client = TestClient(api.app)
    assert client.get(f"/api/channels/{CH_SHOCK}/policy-signals").json()["found"] is True
    assert client.get("/api/niches/psniche/policy-signals").json()["channels"] == 2
    assert srv.policy_signals(CH_AIDOC)["categories"]["ai_persona_sensitive"]["level"] == "watch"
    assert srv.niche_policy_signals("psniche")["found"] is True

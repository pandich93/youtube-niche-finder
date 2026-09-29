"""Tests for application/saturation.py (plan 08) and where its answer shows
up: niche_overview / niche_overview_from_channel (saturation_v2, whatever
the requested period), the all-niches list and the cluster map. Throwaway
schema (tests/schema_scope.py); no network, zero quota.
Run with pytest, or directly: python3 tests/test_saturation.py
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
from application import niche_clusters as NCL  # noqa: E402
from application import saturation as SAT  # noqa: E402
from application import search as Q  # noqa: E402

NOW = datetime.now(timezone.utc)
BUSY, THIN = "busy-niche", "thin-niche"


def iso(days):
    return (NOW - timedelta(days=days)).isoformat()


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("niche_clusters", "video_stats_history", "video_niches", "videos", "channels",
              "niches"):
        conn.execute(f"DELETE FROM {t}")
    for slug in (BUSY, THIN):
        conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", (slug, slug, slug))
    # busy: 6 channels, 30 videos in the last 30 days, 30 in the 90 before;
    # two channels were created 10 days ago.
    for c in range(6):
        created = iso(10) if c < 2 else iso(900)
        conn.execute("INSERT INTO channels (channel_id, title, subscriber_count, published_at) "
                     "VALUES (?,?,?,?)", (f"UCbusy{c}", f"Busy {c}", 5000, created))
    n = 0
    for days in [4 + i for i in range(25)] + [5, 6, 7, 8, 9] \
            + [31 + 3 * i for i in range(30)]:
        vid = f"busy{n}"
        conn.execute(
            "INSERT INTO videos (video_id, channel_id, title, published_at, first_seen_at, "
            "view_count, duration_seconds, is_short) VALUES (?,?,?,?,?,?,?,?)",
            (vid, f"UCbusy{n % 6}", f"video {n}", iso(days), iso(days - 1), 10_000, 600, 0))
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (vid, BUSY))
        n += 1
    # thin: five videos only
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                 ("UCthin", "Thin", 100))
    for i in range(5):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
                     "duration_seconds, is_short) VALUES (?,?,?,?,?,?,?)",
                     (f"thin{i}", "UCthin", "t", iso(5 + i), 100, 600, 0))
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)",
                     (f"thin{i}", THIN))
    conn.commit()
    conn.close()


def test_niche_saturation_reads_the_last_120_days():
    out = SAT.niche_saturation(BUSY)
    assert out["counts"] == {"recent": 30, "base": 30}
    assert out["status"] != "insufficient-data"
    assert out["signals"]["entrants"]["recent"] == 2
    assert out["signals"]["coverage"]["caughtYoungShare"] == 1.0
    assert len(out["reasons"]) >= 3


def test_thin_niche_says_insufficient_data():
    out = SAT.niche_saturation(THIN)
    assert out["status"] == "insufficient-data" and out["counts"]["recent"] == 5


def test_niche_overview_carries_saturation_v2_whatever_the_period():
    week = Q.niche_overview(BUSY, period="7d")
    full = Q.niche_overview(BUSY, period="all")
    assert week["saturation_v2"]["counts"] == full["saturation_v2"]["counts"] \
        == {"recent": 30, "base": 30}
    assert "saturation_hint" in week                    # the old hint stays


def test_niche_overview_from_channel_carries_saturation_v2(monkeypatch):
    monkeypatch.setattr(Q, "similar_channels", lambda cid, **kw: {"similar": [
        {"channelId": f"UCbusy{i}"} for i in range(1, 6)]})
    out = Q.niche_overview_from_channel("UCbusy0")
    assert out["saturation_v2"]["counts"] == {"recent": 30, "base": 30}


def test_all_niches_in_one_pass():
    out = SAT.all_niches_saturation()
    by = {n["niche"]: n for n in out["niches"]}
    assert by[BUSY]["counts"] == {"recent": 30, "base": 30}
    assert by[THIN]["status"] == "insufficient-data"


def test_cluster_map_shows_saturation_but_keeps_its_order():
    conn = db.get_conn()
    for cid, outlier, chans in (("c-low", 1.0, ["UCthin"]),
                                ("c-high", 5.0, [f"UCbusy{i}" for i in range(6)])):
        conn.execute(
            "INSERT INTO niche_clusters (cluster_id, name, channel_count, median_outlier_score, "
            "competition_count, channel_ids, created_at) VALUES (?,?,?,?,?,?,?)",
            (cid, cid, len(chans), outlier, 0, json.dumps(chans), db.now_iso()))
    conn.commit()
    conn.close()

    out = NCL.niche_map()

    assert [c["clusterId"] for c in out["clusters"]] == ["c-high", "c-low"]
    by = {c["clusterId"]: c["saturation"] for c in out["clusters"]}
    assert by["c-high"]["counts"] == {"recent": 30, "base": 30}
    assert by["c-low"]["status"] == "insufficient-data"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

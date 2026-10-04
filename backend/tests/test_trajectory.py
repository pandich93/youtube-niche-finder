"""Tests for application/trajectory.py (plan 20): views by age from the
worker's snapshots, the channel's expected curve next to it, and marks for
title/thumbnail swaps. Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_trajectory.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import trajectory as TJ  # noqa: E402

NOW = datetime.now(timezone.utc)
CH = "UC" + "traj".ljust(22, "0")


def setup_module(_=None):
    db.init_db()


def _iso(dt):
    return dt.isoformat()


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("video_changes", "video_stats_history", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                 (CH, "Traj channel", 5000))
    # 5 older uploads at 10,000 views give the channel a baseline
    for i in range(5):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
                     "duration_seconds, is_short) VALUES (?,?,?,?,?,600,0)",
                     (f"tb{i}", CH, f"base {i}", _iso(NOW - timedelta(days=200 - i * 10)), 10_000))
    pub = NOW - timedelta(days=3)
    conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
                 "duration_seconds, is_short) VALUES (?,?,?,?,?,600,0)",
                 ("tv", CH, "the video", _iso(pub), 9_000))
    for h, v in ((2, 300), (12, 2_000), (24, 4_000), (48, 7_000), (72, 9_000)):
        conn.execute("INSERT INTO video_stats_history (video_id, captured_at, view_count) "
                     "VALUES (?,?,?)", ("tv", _iso(pub + timedelta(hours=h)), v))
    conn.execute("INSERT INTO video_changes (video_id, changed_at, field, old_value, new_value) "
                 "VALUES (?,?,?,?,?)", ("tv", _iso(pub + timedelta(hours=30)), "title", "a", "b"))
    conn.commit()
    conn.close()


def test_points_are_views_by_age_in_hours():
    v = TJ.video_trajectory(["tv"])["videos"][0]
    assert [p["ageHours"] for p in v["points"]] == [2.0, 12.0, 24.0, 48.0, 72.0]
    assert [p["views"] for p in v["points"]] == [300, 2_000, 4_000, 7_000, 9_000]
    assert v["observedFromHours"] == 2.0
    assert v["title"] == "the video" and v["channelTitle"] == "Traj channel"


def test_expected_curve_is_the_channel_baseline_times_maturity():
    v = TJ.video_trajectory(["tv"])["videos"][0]
    assert v["baselineMedianViews"] == 10_000
    exp = {p["ageHours"]: p["views"] for p in v["expected"]}
    assert exp[24.0] < exp[72.0] <= 10_000          # rises towards the 30-day baseline
    assert max(p["ageHours"] for p in v["expected"]) >= 72.0


def test_a_title_swap_is_marked_at_its_age():
    v = TJ.video_trajectory(["tv"])["videos"][0]
    assert {"ageHours": 30.0, "kind": "title"} in [{k: m[k] for k in ("ageHours", "kind")}
                                                   for m in v["marks"]]


def test_unknown_and_duplicate_ids_and_the_cap():
    out = TJ.video_trajectory(["tv", "tv", "nope"])
    assert [v["videoId"] for v in out["videos"]] == ["tv"]
    assert out["missing"] == ["nope"]
    with pytest.raises(ValueError):
        TJ.video_trajectory([f"x{i}" for i in range(TJ.MAX_VIDEOS + 1)])
    with pytest.raises(ValueError):
        TJ.video_trajectory([])


def test_a_video_without_snapshots_says_so():
    v = TJ.video_trajectory(["tb0"])["videos"][0]
    assert v["points"] == [] and v["observedFromHours"] is None


def test_http_and_mcp_doors():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    r = TestClient(api.app).get("/api/videos/trajectory?ids=tv,nope")
    assert r.status_code == 200 and r.json()["missing"] == ["nope"]
    assert srv.video_trajectory(["tv"])["videos"][0]["videoId"] == "tv"


def test_load_window_by_video_ids_keeps_the_channel_baseline():
    # review: trajectory and repeatability need a few videos' scores, not
    # every video of their channels; the baseline still comes from the channel
    from application import discovery as trends
    full = {r["video_id"]: r for r in trends.load_window(period="all", channel_ids=[CH])}
    narrow = trends.load_window(period="all", channel_ids=[CH], video_ids=["tv"])
    assert [r["video_id"] for r in narrow] == ["tv"]
    assert narrow[0]["baselineMedianViews"] == full["tv"]["baselineMedianViews"] == 10_000
    assert narrow[0]["outlierScore"] == full["tv"]["outlierScore"]

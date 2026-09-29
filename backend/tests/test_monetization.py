"""Tests for application/monetization.py (plan 12) and where yppEligibility
shows up: channel_analytics (MCP, dashboard) and inspect_channel (the
extension's channel panel). Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_monetization.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import channel_tracking as T  # noqa: E402
from application import inspection as I  # noqa: E402
from application import monetization as MON  # noqa: E402

NOW = datetime.now(timezone.utc)
CH = "UC" + "yppshorts".ljust(22, "0")
CH_SMALL = "UC" + "yppsmall".ljust(22, "0")


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count, view_count, video_count, "
                 "published_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                 (CH, "Shorts Lab", 2500, 40_000_000, 10, (NOW - timedelta(days=400)).isoformat(),
                  NOW.isoformat()))
    for i in range(4):   # 4 Shorts in the last 90 days, 3M views each
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
                     "duration_seconds, is_short) VALUES (?,?,?,?,?,?,?)",
                     (f"ys{i}", CH, "s", (NOW - timedelta(days=10 + i)).isoformat(),
                      3_000_000, 40, 1))
    conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
                 "duration_seconds, is_short) VALUES (?,?,?,?,?,?,?)",
                 ("yold", CH, "old", (NOW - timedelta(days=200)).isoformat(), 9_000_000, 40, 1))
    # a channel under 500 subscribers: below every YPP tier
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count, view_count, video_count, "
                 "published_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                 (CH_SMALL, "Tiny", 300, 90_000, 6, (NOW - timedelta(days=300)).isoformat(),
                  NOW.isoformat()))
    for i in range(6):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
                     "duration_seconds, is_short) VALUES (?,?,?,?,?,?,?)",
                     (f"ysm{i}", CH_SMALL, "t", (NOW - timedelta(days=5 + i)).isoformat(),
                      15_000 if i == 0 else 1_000, 600, 0))
    conn.commit()
    conn.close()


def test_for_channel_reads_the_last_90_days_of_collected_videos():
    out = MON.for_channel(CH)
    assert out["status"] == "shorts-path-met"
    assert out["tiers"]["full"]["shortsViews90d"]["seenAtLeast"] == 12_000_000


def test_unknown_channel_is_unknown():
    assert MON.for_channel("UCnope")["status"] == "unknown"


def test_channel_analytics_carries_ypp_eligibility():
    out = T.channel_analytics(CH)
    assert out["yppEligibility"]["status"] == "shorts-path-met"


def test_inspect_channel_carries_ypp_eligibility_without_fetching():
    out = I.inspect_channel(None, CH, fetch=False)
    assert out["found"] is True
    assert out["yppEligibility"]["status"] == "shorts-path-met"


def test_statuses_for_many_channels_match_one_by_one():
    got = MON.statuses([CH, CH_SMALL, "UCnope"])
    assert got == {CH: MON.for_channel(CH)["status"],
                   CH_SMALL: MON.for_channel(CH_SMALL)["status"],
                   "UCnope": "unknown"}
    assert got[CH_SMALL] == "below-threshold"


def test_search_outliers_can_keep_only_channels_past_ypp_thresholds():
    from application import search as Q
    everyone = {r["channelId"] for r in Q.search_outliers(period="all", limit=50)}
    assert everyone == {CH, CH_SMALL}
    past = {r["channelId"] for r in
            Q.search_outliers(period="all", limit=50, min_ypp_status="subscribers-met")}
    assert past == {CH}
    shorts = {r["channelId"] for r in
              Q.search_outliers(period="all", limit=50, min_ypp_status="shorts-path-met")}
    assert shorts == {CH}


def test_recent_outlier_channels_can_keep_only_channels_past_ypp_thresholds():
    kw = dict(period="30d", min_multiplier=0, exclude_shorts=False)
    everyone = {c["channelId"] for c in T.recently_added_outlier_channels(**kw)["channels"]}
    past = {c["channelId"] for c in T.recently_added_outlier_channels(
        min_ypp_status="subscribers-met", **kw)["channels"]}
    assert CH_SMALL in everyone
    assert CH_SMALL not in past


def test_unknown_ypp_filter_value_is_rejected():
    from application import search as Q
    with pytest.raises(ValueError):
        Q.search_outliers(period="all", min_ypp_status="monetized")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

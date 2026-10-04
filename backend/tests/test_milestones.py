"""Tests for application/milestones.py (plan 17) and where `milestones` shows
up: channel_analytics (MCP, dashboard) and inspect_channel (the extension's
channel panel). Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_milestones.py
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
from application import milestones as MS  # noqa: E402

NOW = datetime.now(timezone.utc)
CH = "UC" + "milestone".ljust(22, "0")
CH_HIDDEN = "UC" + "mshidden".ljust(22, "0")


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("channel_stats_history", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    for cid, subs, hidden in ((CH, 800, 0), (CH_HIDDEN, 0, 1)):
        conn.execute("INSERT INTO channels (channel_id, title, subscriber_count, view_count, "
                     "video_count, hidden_subs, updated_at) VALUES (?,?,?,?,?,?,?)",
                     (cid, cid, subs, 1000, 10, hidden, NOW.isoformat()))
    for d in range(31):          # +10 subscribers a day for 30 days, ending at 800
        db.record_channel_stats(conn, CH, 500 + 10 * d, 10, 1000,
                                captured_at=(NOW - timedelta(days=30 - d)).isoformat())
    conn.commit()
    conn.close()


def test_forecast_of_the_next_two_milestones():
    r = MS.for_channel(CH)
    assert r["subscribers"] == 800
    assert [f["target"] for f in r["forecasts"]] == [1_000, 10_000]
    f = r["forecasts"][0]
    assert f["pace30PerDay"] == pytest.approx(10.0, abs=0.01)
    assert f["eta30"] == (NOW + timedelta(days=20)).date().isoformat()
    assert r["ypp1000BeforeRules2027"] is True
    assert "not YouTube data" in r["note"]


def test_hidden_subscribers_give_no_forecast():
    r = MS.for_channel(CH_HIDDEN)
    assert r["subscribers"] is None and r["forecasts"] == []


def test_channel_analytics_and_inspect_channel_carry_milestones():
    assert T.channel_analytics(CH)["milestones"]["subscribers"] == 800
    assert I.inspect_channel(None, CH, fetch=False)["milestones"]["subscribers"] == 800

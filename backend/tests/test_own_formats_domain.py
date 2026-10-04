"""Tests for domain/own_formats.py (plan 24): your own channel's Shorts
against long videos from YouTube Analytics rows (day x creatorContentType),
and watch hours toward the YPP bar. Pure; no DB.
Run with pytest, or directly: python3 tests/test_own_formats_domain.py
"""
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pytest  # noqa: E402

from domain import own_formats as F  # noqa: E402

END = date(2026, 9, 30)


def rows(days, kind, views, minutes, subs):
    return [{"day": (END - timedelta(days=d)).isoformat(), "contentType": kind,
             "views": views, "minutes": minutes, "subscribers": subs} for d in range(days)]


def test_format_totals_shares_and_subscribers_per_thousand_views():
    data = rows(90, "SHORTS", 1000, 300, 1) + rows(90, "VIDEO_ON_DEMAND", 200, 1200, 2)
    s = F.summary(data, END, days=90)
    assert s["SHORTS"]["views"] == 90_000 and s["VIDEO_ON_DEMAND"]["views"] == 18_000
    assert s["SHORTS"]["viewShare"] == round(90_000 / 108_000, 3)
    assert s["SHORTS"]["subscribersPer1000Views"] == 1.0
    assert s["VIDEO_ON_DEMAND"]["subscribersPer1000Views"] == 10.0


def test_weekly_series_has_one_point_per_week_and_type():
    data = rows(28, "SHORTS", 100, 10, 0) + rows(28, "VIDEO_ON_DEMAND", 50, 100, 0)
    w = F.weekly(data, END, weeks=4)
    assert [x["views"] for x in w["SHORTS"]] == [700, 700, 700, 700]
    assert len(w["VIDEO_ON_DEMAND"]) == 4


def test_correlation_needs_enough_weeks_and_is_only_a_correlation():
    up = [{"day": (END - timedelta(days=d)).isoformat(), "contentType": k,
           "views": (100 - d) * (2 if k == "SHORTS" else 1), "minutes": 1, "subscribers": 0}
          for d in range(84) for k in ("SHORTS", "VIDEO_ON_DEMAND")]
    c = F.shorts_vs_long(up, END)
    assert c["weeks"] == 12 and c["r"] > 0.9 and c["reading"] == "move-together"
    few = F.shorts_vs_long(up[:20], END)
    assert few["r"] is None and few["reason"] == "few-weeks"


def test_watch_hours_count_long_and_live_but_not_shorts():
    data = (rows(365, "VIDEO_ON_DEMAND", 10, 60, 0) + rows(365, "LIVE_STREAM", 1, 6, 0)
            + rows(365, "SHORTS", 1000, 600, 0))
    h = F.watch_hours(data, END)
    assert h["hours365"] == pytest.approx(365 * 66 / 60, rel=1e-3)
    assert h["shortsViews90d"] == 90_000
    assert h["needed"] == {"2026": 4000, "2027": 8000}
    assert h["paceHoursPerDay"] == pytest.approx(66 / 60, rel=1e-3)
    assert h["eta4000"] and h["eta8000"] and h["eta4000"] < h["eta8000"]


def test_hours_already_over_the_bar_have_no_eta():
    h = F.watch_hours(rows(365, "VIDEO_ON_DEMAND", 10, 1200, 0), END)
    assert h["hours365"] > 4000 and h["eta4000"] is None and h["reached4000"] is True

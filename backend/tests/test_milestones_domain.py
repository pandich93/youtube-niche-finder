"""Tests for domain/milestones.py (plan 17): when a channel reaches its next
subscriber milestone at its recent pace. An estimate from our own snapshots,
not YouTube data. Pure functions; no DB, no network.
Run with pytest, or directly: python3 tests/test_milestones_domain.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import milestones as MS  # noqa: E402

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def daily(start, per_day, days):
    return [(T0 + timedelta(days=d), start + per_day * d) for d in range(days + 1)]


def test_next_targets_skip_what_is_already_reached():
    assert MS.next_targets(950) == [1_000, 10_000]
    assert MS.next_targets(1_000) == [10_000, 100_000]
    assert MS.next_targets(50, n=1) == [100]


def test_forecast_at_a_steady_pace():
    f = MS.forecast(daily(500, 10, 30), 1_000)
    assert f["current"] == 800 and f["reached"] is False
    assert f["pace30PerDay"] == 10.0
    assert f["eta30"] == (T0 + timedelta(days=50)).date().isoformat()   # 200 more at 10/day
    assert f["sample"] == 31


def test_reached_target_has_no_eta():
    f = MS.forecast(daily(900, 10, 20), 1_000)
    assert f["reached"] is True and f["eta30"] is None


def test_too_few_snapshots_cannot_be_estimated():
    f = MS.forecast(daily(500, 10, 3), 1_000)
    assert f["eta30"] is None and f["reason"] == "few-snapshots"


def test_a_short_span_cannot_be_estimated():
    pts = [(T0 + timedelta(hours=h), 500 + h) for h in range(10)]
    assert MS.forecast(pts, 1_000)["reason"] == "short-history"


def test_flat_or_falling_pace_cannot_be_estimated():
    assert MS.forecast(daily(500, 0, 20), 1_000)["reason"] == "no-growth"
    assert MS.forecast(daily(500, -2, 20), 1_000)["reason"] == "no-growth"


def test_pace_over_90_days_uses_the_longer_window():
    pts = daily(100, 1, 60) + [(T0 + timedelta(days=60 + d), 160 + 10 * d) for d in range(1, 31)]
    f = MS.forecast(pts, 1_000)
    assert f["pace30PerDay"] == 10.0
    assert f["pace90PerDay"] == round((460 - 100) / 90, 2)


def test_crossed_milestones_between_two_values():
    assert MS.crossed(990, 1_010) == [1_000]
    assert MS.crossed(9_000, 120_000) == [10_000, 100_000]
    assert MS.crossed(1_000, 1_200) == []
    assert MS.crossed(None, 1_200) == []

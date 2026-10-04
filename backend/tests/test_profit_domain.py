"""Tests for domain/profit.py (plan 30): pure arithmetic, no database.
Run with pytest, or directly: python3 tests/test_profit_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import profit as P  # noqa: E402

PROFILE = {"per_video_usd": 10.0, "per_minute_usd": 2.0, "monthly_usd": 30.0}
RPM = {"low": 1.0, "mid": 2.0, "high": 4.0}


def test_a_video_costs_its_fixed_price_plus_its_minutes():
    assert P.video_cost(PROFILE, 600) == 30.0          # 10 + 2 x 10 min
    assert P.video_cost(PROFILE, None) == 10.0
    assert P.video_cost(PROFILE, 90) == 13.0


def test_revenue_and_profit_are_ranges_like_the_rpm():
    r = P.per_video(PROFILE, RPM, 20_000, 600)
    assert r["revenue"] == {"low": 20.0, "mid": 40.0, "high": 80.0}
    assert r["cost"] == 30.0 and r["profit"] == {"low": -10.0, "mid": 10.0, "high": 50.0}
    assert r["verdict"] == "uncertain"


def test_the_verdict_is_only_sure_when_the_whole_range_agrees():
    assert P.per_video(PROFILE, RPM, 100_000, 600)["verdict"] == "profitable"
    assert P.per_video(PROFILE, RPM, 1_000, 600)["verdict"] == "loss"


def test_break_even_views_at_each_rpm_low_rpm_needs_the_most():
    b = P.per_video(PROFILE, RPM, 0, 600)["breakEvenViews"]
    assert b == {"low": 30_000, "mid": 15_000, "high": 7_500}
    assert P.break_even_views(5.0, {"low": 0, "mid": 1.0, "high": 2.0})["low"] is None


def test_a_month_adds_the_overhead_to_every_upload():
    m = P.per_month(PROFILE, RPM, 100_000, 4, 600)
    assert m["cost"] == 30.0 + 4 * 30.0 and m["costPerVideo"] == 30.0 and m["overhead"] == 30.0
    assert m["revenue"]["mid"] == 200.0 and m["profit"]["mid"] == 50.0
    assert m["uploads"] == 4 and m["monthlyViews"] == 100_000


def test_a_month_without_uploads_still_pays_the_overhead():
    m = P.per_month(PROFILE, RPM, 0, 0, 0)
    assert m["cost"] == 30.0 and m["verdict"] == "loss"


def test_a_real_rpm_is_one_number_so_the_verdict_is_sure():
    rpm = P.real_rpm(3.0)
    assert rpm == {"low": 3.0, "mid": 3.0, "high": 3.0}
    assert P.per_video(PROFILE, rpm, 20_000, 600)["verdict"] == "profitable"     # 60 - 30


def test_zero_costs_make_any_views_profitable_and_break_even_at_zero():
    zero = {"per_video_usd": 0.0, "per_minute_usd": 0.0, "monthly_usd": 0.0}
    r = P.per_video(zero, RPM, 10, 60)
    assert r["verdict"] == "profitable" and r["breakEvenViews"]["low"] == 0

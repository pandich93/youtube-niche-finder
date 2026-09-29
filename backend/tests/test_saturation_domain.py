"""Tests for domain/saturation.py (plan 08): is a niche growing, holding,
cooling or saturated -- the last 30 days against the 90 before them. Pure
functions over synthetic series; no DB, no network.
Run with pytest, or directly: python3 tests/test_saturation_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import saturation as S  # noqa: E402


def series(recent=30, base_per_30d=30, recent_views=10_000, base_views=10_000,
           recent_channels=10, base_channels=10, young=True, short=False,
           recent_outlier=1.0, base_outlier=1.0):
    """recent: videos in the last 30 days, base_per_30d: videos per 30 days
    in the 90 before; views are the projected-30-day numbers."""
    vids = []
    for i in range(recent):
        vids.append({"channelId": f"r{i % recent_channels}", "ageDays": 4 + (i % 25),
                     "projectedViews": recent_views, "outlierScore": recent_outlier,
                     "isShort": short, "caughtYoung": young})
    for i in range(base_per_30d * 3):
        vids.append({"channelId": f"b{i % base_channels}", "ageDays": 31 + (i % 88),
                     "projectedViews": base_views, "outlierScore": base_outlier,
                     "isShort": short, "caughtYoung": young})
    return vids


def run(vids, channel_ages=None):
    return S.saturation(vids, channel_ages or {})


def codes(res):
    return [r["code"] for r in res["reasons"]]


# ------------------------------------------------------------ status

def test_steady_niche_is_stable_with_at_least_three_reasons():
    res = run(series())
    assert res["status"] == "stable"
    assert len(res["reasons"]) >= 3
    assert res["signals"]["supply"]["ratio"] == 1.0
    assert res["signals"]["demand"]["ratio"] == 1.0


def test_demand_up_with_newcomers_breaking_out_is_growing():
    vids = series(recent_views=20_000, recent_outlier=3.0)
    ages = {f"r{i}": 20 for i in range(10)}          # recent channels are new
    res = run(vids, ages)
    assert res["status"] == "growing"
    assert "demand-up" in codes(res) and "newcomers-break-out" in codes(res)


def test_demand_down_without_more_supply_is_cooling():
    res = run(series(recent_views=5_000))
    assert res["status"] == "cooling"
    assert "demand-down" in codes(res)


def test_more_supply_and_less_demand_is_saturated():
    res = run(series(recent=60, recent_views=5_000))
    assert res["status"] == "saturated"
    assert {"supply-up", "demand-down"} <= set(codes(res))


def test_more_supply_while_newcomers_never_break_out_is_saturated():
    vids = series(recent=60, recent_outlier=1.0)
    ages = {f"r{i}": 40 for i in range(10)}
    res = run(vids, ages)
    assert res["status"] == "saturated"
    assert "newcomers-rarely-break-out" in codes(res)


def test_few_videos_in_either_window_is_insufficient_data_not_a_guess():
    assert run(series(recent=19))["status"] == "insufficient-data"
    assert run(series(base_per_30d=6))["status"] == "insufficient-data"  # 18 in base
    res = run(series(recent=5))
    assert "few-videos" in codes(res) and res["counts"] == {"recent": 5, "base": 90}


def test_empty_input_is_insufficient_data():
    res = run([])
    assert res["status"] == "insufficient-data" and res["counts"] == {"recent": 0, "base": 0}


# ------------------------------------------------------------ signals

def test_supply_compares_against_the_base_average_per_30_days():
    res = run(series(recent=45, base_per_30d=30))
    assert res["signals"]["supply"] == {"ratio": 1.5, "recent": 45, "basePer30d": 30.0}


def test_videos_younger_than_three_days_do_not_count_for_demand():
    vids = series()
    vids += [{"channelId": "x", "ageDays": 1, "projectedViews": 1, "outlierScore": 1,
              "isShort": False, "caughtYoung": True}] * 40
    assert run(vids)["signals"]["demand"]["ratio"] == 1.0


def test_demand_uses_the_dominant_format_only():
    vids = series(recent_views=10_000)
    vids += [{"channelId": "s", "ageDays": 10, "projectedViews": 100, "outlierScore": 1,
              "isShort": True, "caughtYoung": True}] * 5
    res = run(vids)
    assert res["format"] == "long" and res["signals"]["demand"]["ratio"] == 1.0


def test_entrants_count_channels_created_in_each_window():
    ages = {"r0": 10, "r1": 20, "b0": 50, "b1": 400}
    res = run(series(), ages)
    e = res["signals"]["entrants"]
    assert e["recent"] == 2 and e["basePer30d"] == round(1 / 3, 2)
    assert e["unknownAge"] == 20 - 4


def test_newcomer_share_needs_three_young_channels():
    res = run(series(recent_outlier=3.0), {"r0": 30, "r1": 30})
    assert res["signals"]["newcomers"]["share"] is None
    res = run(series(recent_outlier=3.0), {"r0": 30, "r1": 30, "r2": 30})
    assert res["signals"]["newcomers"]["share"] == 1.0


def test_low_capture_share_marks_confidence_low_but_keeps_the_status():
    res = run(series(recent_views=5_000, young=False))
    assert res["status"] == "cooling" and res["confidence"] == "low"
    assert "low-coverage" in codes(res)
    assert run(series())["confidence"] == "normal"


def test_series_has_two_points_for_the_mini_chart():
    res = run(series(recent=45, recent_views=20_000))
    assert res["series"] == {"videos": [30.0, 45], "demand": [10_000, 20_000]}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

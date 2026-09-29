"""Tests for domain/monetization.py (plan 12): which YouTube Partner Program
thresholds a channel visibly meets from public data. Not a monetization status
-- YouTube does not publish one. Pure functions; no DB, no network.
Run with pytest, or directly: python3 tests/test_monetization_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import monetization as MZ  # noqa: E402


def vids(n=0, age=10, views=1000, short=False):
    return [{"ageDays": age, "views": views, "isShort": short} for _ in range(n)]


def test_below_500_subscribers_is_below_every_tier():
    r = MZ.ypp_eligibility(499, False, vids(10))
    assert r["status"] == "below-threshold"
    assert r["tiers"]["expanded"]["subscribers"] is False
    assert r["tiers"]["full"]["subscribers"] is False


def test_500_subscribers_meets_the_expanded_tier_subscriber_bar_only():
    r = MZ.ypp_eligibility(700, False, vids(3))
    assert r["status"] == "subscribers-met"
    assert r["tiers"]["expanded"]["subscribers"] is True
    assert r["tiers"]["expanded"]["uploads90d"] == {"seen": 3, "needed": 3, "met": True}
    assert r["tiers"]["full"]["subscribers"] is False


def test_watch_hours_are_always_reported_as_unknowable():
    r = MZ.ypp_eligibility(5000, False, vids(5))
    assert r["tiers"]["full"]["watchHours"] is None
    assert r["tiers"]["expanded"]["watchHours"] is None
    assert r["status"] == "subscribers-met"


def test_shorts_views_in_90_days_can_prove_the_full_shorts_path():
    r = MZ.ypp_eligibility(2000, False, vids(4, age=30, views=3_000_000, short=True))
    assert r["tiers"]["full"]["shortsViews90d"] == {"seenAtLeast": 12_000_000,
                                                    "needed": 10_000_000, "met": True}
    assert r["status"] == "shorts-path-met"


def test_old_shorts_and_long_videos_do_not_count_toward_shorts_views():
    r = MZ.ypp_eligibility(2000, False, vids(4, age=120, views=5_000_000, short=True)
                           + vids(4, age=10, views=5_000_000, short=False))
    assert r["tiers"]["full"]["shortsViews90d"]["seenAtLeast"] == 0
    assert r["status"] == "subscribers-met"


def test_expanded_shorts_path_needs_three_uploads_too():
    r = MZ.ypp_eligibility(600, False, vids(2, age=5, views=2_000_000, short=True))
    assert r["tiers"]["expanded"]["shortsViews90d"]["met"] is True
    assert r["tiers"]["expanded"]["uploads90d"]["met"] is False
    assert r["status"] == "subscribers-met"
    r = MZ.ypp_eligibility(600, False, vids(3, age=5, views=1_000_000, short=True))
    assert r["status"] == "shorts-path-met"


def test_hidden_or_missing_subscribers_is_unknown():
    assert MZ.ypp_eligibility(None, False, vids(3))["status"] == "unknown"
    assert MZ.ypp_eligibility(50_000, True, vids(3))["status"] == "unknown"


def test_the_answer_says_it_is_not_a_monetization_status():
    r = MZ.ypp_eligibility(5000, False, [])
    assert "not" in r["note"].lower() and "lower bound" in r["note"].lower()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

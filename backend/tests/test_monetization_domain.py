"""Tests for domain/monetization.py (plan 12): which YouTube Partner Program
thresholds a channel visibly meets from public data. Not a monetization status
-- YouTube does not publish one. Pure functions; no DB, no network.
Run with pytest, or directly: python3 tests/test_monetization_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from datetime import date  # noqa: E402

from domain import monetization as MZ  # noqa: E402

# the tests above plan 17 check the 2026 rules; pin the day so they keep
# meaning that after the 2027-02-01 switch
NOW_2026 = date(2026, 10, 4)
NOW_2027 = date(2027, 2, 1)


def vids(n=0, age=10, views=1000, short=False):
    return [{"ageDays": age, "views": views, "isShort": short} for _ in range(n)]


def test_below_500_subscribers_is_below_every_tier():
    r = MZ.ypp_eligibility(499, False, vids(10), on=NOW_2026)
    assert r["status"] == "below-threshold"
    assert r["tiers"]["expanded"]["subscribers"] is False
    assert r["tiers"]["full"]["subscribers"] is False


def test_500_subscribers_meets_the_expanded_tier_subscriber_bar_only():
    r = MZ.ypp_eligibility(700, False, vids(3), on=NOW_2026)
    assert r["status"] == "subscribers-met"
    assert r["tiers"]["expanded"]["subscribers"] is True
    assert r["tiers"]["expanded"]["uploads90d"] == {"seen": 3, "needed": 3, "met": True}
    assert r["tiers"]["full"]["subscribers"] is False


def test_watch_hours_are_always_reported_as_unknowable():
    r = MZ.ypp_eligibility(5000, False, vids(5), on=NOW_2026)
    assert r["tiers"]["full"]["watchHours"] is None
    assert r["tiers"]["expanded"]["watchHours"] is None
    assert r["status"] == "subscribers-met"


def test_shorts_views_in_90_days_can_prove_the_full_shorts_path():
    r = MZ.ypp_eligibility(2000, False, vids(4, age=30, views=3_000_000, short=True), on=NOW_2026)
    assert r["tiers"]["full"]["shortsViews90d"] == {"seenAtLeast": 12_000_000,
                                                    "needed": 10_000_000, "met": True}
    assert r["status"] == "shorts-path-met"


def test_old_shorts_and_long_videos_do_not_count_toward_shorts_views():
    r = MZ.ypp_eligibility(2000, False, vids(4, age=120, views=5_000_000, short=True)
                           + vids(4, age=10, views=5_000_000, short=False), on=NOW_2026)
    assert r["tiers"]["full"]["shortsViews90d"]["seenAtLeast"] == 0
    assert r["status"] == "subscribers-met"


def test_expanded_shorts_path_needs_three_uploads_too():
    r = MZ.ypp_eligibility(600, False, vids(2, age=5, views=2_000_000, short=True), on=NOW_2026)
    assert r["tiers"]["expanded"]["shortsViews90d"]["met"] is True
    assert r["tiers"]["expanded"]["uploads90d"]["met"] is False
    assert r["status"] == "subscribers-met"
    r = MZ.ypp_eligibility(600, False, vids(3, age=5, views=1_000_000, short=True), on=NOW_2026)
    assert r["status"] == "shorts-path-met"


def test_hidden_or_missing_subscribers_is_unknown():
    assert MZ.ypp_eligibility(None, False, vids(3), on=NOW_2026)["status"] == "unknown"
    assert MZ.ypp_eligibility(50_000, True, vids(3), on=NOW_2026)["status"] == "unknown"


def test_the_answer_says_it_is_not_a_monetization_status():
    r = MZ.ypp_eligibility(5000, False, [], on=NOW_2026)
    assert "not" in r["note"].lower() and "lower bound" in r["note"].lower()


def test_meets_orders_statuses_and_never_passes_unknown():
    assert MZ.meets("shorts-path-met", "subscribers-met")
    assert MZ.meets("subscribers-met", "subscribers-met")
    assert not MZ.meets("subscribers-met", "shorts-path-met")
    assert not MZ.meets("below-threshold", "subscribers-met")
    assert not MZ.meets("unknown", "subscribers-met")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


# --------------------------------------------- plan 17: the 2027 thresholds

def test_rules_switch_on_the_first_of_february_2027():
    assert MZ.tiers_on(date(2027, 1, 31))["full"]["watchHours"] == 4_000
    assert MZ.tiers_on(date(2027, 2, 1))["full"]["watchHours"] == 8_000
    assert MZ.tiers_on(date(2027, 2, 1))["full"]["shortsViews90d"] == 20_000_000
    assert MZ.tiers_on(date(2027, 2, 1))["full"]["subscribers"] == 1_000


def test_before_the_switch_the_answer_shows_the_upcoming_rules():
    r = MZ.ypp_eligibility(5000, False, vids(5, views=3_000_000, short=True), on=NOW_2026)
    assert r["rules"] == "2026"
    assert r["tiers"]["full"]["shortsViews90d"]["met"] is True     # 15M >= 10M today
    up = r["upcoming"]
    assert up["effective"] == "2027-02-01"
    assert up["tiers"]["full"]["shortsViews90d"]["met"] is False   # 15M < 20M from February
    assert up["status"] == "shorts-path-met"   # the expanded tier (3M) keeps its 2026 numbers
    assert up["tiers"]["full"]["shortsViews90d"]["needed"] == 20_000_000
    assert up["tiers"]["full"]["watchHoursNeeded"] == 8_000


def test_after_the_switch_the_2027_rules_apply_and_nothing_is_upcoming():
    r = MZ.ypp_eligibility(5000, False, vids(5, views=3_000_000, short=True), on=NOW_2027)
    assert r["rules"] == "2027"
    assert r["tiers"]["full"]["shortsViews90d"]["met"] is False
    assert "upcoming" not in r
    assert r["tiers"]["full"]["watchHoursNeeded"] == 8_000


def test_2027_activity_is_met_by_uploads_we_can_see():
    long2 = vids(2, age=30)
    shorts5 = vids(5, age=30, short=True)
    one_each = vids(1, age=30) + vids(1, age=30, short=True)
    assert MZ.ypp_eligibility(5000, False, long2, on=NOW_2027)["activity"]["met"] is True
    assert MZ.ypp_eligibility(5000, False, shorts5, on=NOW_2027)["activity"]["met"] is True
    # watch hours could still satisfy it, so not seeing enough uploads is unknown, not False
    assert MZ.ypp_eligibility(5000, False, one_each, on=NOW_2027)["activity"]["met"] is None


def test_2027_activity_counts_a_million_shorts_views():
    r = MZ.ypp_eligibility(5000, False, vids(1, views=1_000_000, short=True), on=NOW_2027)
    assert r["activity"]["met"] is True
    assert r["activity"] == {"longUploads90d": 0, "shortsUploads90d": 1,
                             "shortsViews90dAtLeast": 1_000_000, "met": True}


def test_2026_rules_carry_no_activity_requirement():
    assert "activity" not in MZ.ypp_eligibility(5000, False, vids(2), on=NOW_2026)
    assert "activity" in MZ.ypp_eligibility(5000, False, vids(2), on=NOW_2026)["upcoming"]

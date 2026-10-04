"""Tests for domain/collabs.py (plan 31): pure checks, no database.
Run with pytest, or directly: python3 tests/test_collabs_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import collabs as CB  # noqa: E402

OK = {"similarity": 0.7, "subscribers": 1_000, "daysSinceUpload": 3, "templateRisk": "low"}


def test_a_channel_of_your_size_active_and_original_passes():
    assert CB.check(OK, 1_000) is None
    assert CB.check({**OK, "subscribers": 500}, 1_000) is None        # the band is inclusive
    assert CB.check({**OK, "subscribers": 2_000}, 1_000) is None
    assert CB.check({**OK, "templateRisk": None}, 1_000) is None       # not scored is not "high"


def test_each_failed_check_names_itself():
    assert CB.check({**OK, "subscribers": 499}, 1_000) == "too-small"
    assert CB.check({**OK, "subscribers": 2_001}, 1_000) == "too-big"
    assert CB.check({**OK, "daysSinceUpload": 31}, 1_000) == "inactive"
    assert CB.check({**OK, "daysSinceUpload": None}, 1_000) == "inactive"
    assert CB.check({**OK, "templateRisk": "high"}, 1_000) == "templated"
    assert CB.check({**OK, "subscribers": None}, 1_000) == "size-unknown"
    assert CB.check(OK, None) == "size-unknown"
    assert CB.check({**OK, "similarity": 0.49}, 1_000) == "off-topic"
    assert CB.check({**OK, "similarity": None}, 1_000) == "off-topic"


def test_the_band_and_the_activity_window_can_be_moved():
    assert CB.check({**OK, "subscribers": 300}, 1_000, min_ratio=0.25) is None
    assert CB.check({**OK, "subscribers": 5_000}, 1_000, max_ratio=5) is None
    assert CB.check({**OK, "daysSinceUpload": 50}, 1_000, active_days=60) is None


def test_size_ratio():
    assert CB.size_ratio(1_500, 1_000) == 1.5
    assert CB.size_ratio(None, 1_000) is None and CB.size_ratio(5, 0) is None


def test_similar_first_then_growth_among_equally_similar():
    cs = [{"similarity": 0.70, "growth30dPct": 1}, {"similarity": 0.704, "growth30dPct": 9},
          {"similarity": 0.80, "growth30dPct": None}, {"similarity": 0.70, "growth30dPct": 5}]
    assert [(c["similarity"], c["growth30dPct"]) for c in sorted(cs, key=CB.sort_key)] == [
        (0.80, None), (0.704, 9), (0.70, 5), (0.70, 1)]

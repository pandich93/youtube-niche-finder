"""Tests for domain/niche_ranking.py (plan 27): pure scoring, no database.
Run with pytest, or directly: python3 tests/test_niche_ranking_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pytest  # noqa: E402

from domain import niche_ranking as R  # noqa: E402

BEST = {"status": "growing", "demandRatio": 1.4, "supplyRatio": 0.6, "newcomersShare": 0.3,
        "rpmMid": 8.0, "templateShare": 0.0, "policyShare": 0.0}
WORST = {"status": "saturated", "demandRatio": 0.6, "supplyRatio": 1.6, "newcomersShare": 0.0,
         "rpmMid": 1.0, "templateShare": 0.5, "policyShare": 0.7}


def test_weights_add_up_to_one_hundred():
    assert sum(R.WEIGHTS.values()) == 100 and set(R.WEIGHTS) == set(R.LABELS) == set(R.RAMPS)


def test_every_term_is_zero_at_one_end_of_its_ramp_and_a_hundred_at_the_other():
    for key, (lo, hi) in R.RAMPS.items():
        assert R._points(key, lo) == 0 and R._points(key, hi) == 100
        assert R._points(key, lo - 99) == 0 or R._points(key, lo - 99) == 100   # clamped, never outside
        assert 0 <= R._points(key, (lo + hi) / 2) <= 100
    assert R._points("demand", 1.0) == 50     # no change in demand is the middle


def test_the_best_and_worst_niches_hit_the_ends_and_the_bands():
    best, worst = R.score(BEST), R.score(WORST)
    assert best["score"] == 100 and best["band"] == "high" and best["coverage"] == 1.0
    assert worst["score"] == 0 and worst["band"] == "low"


def test_the_breakdown_shows_every_term_and_its_contributions_make_the_score():
    out = R.score({**BEST, "demandRatio": 1.0, "rpmMid": 4.5})
    assert [b["key"] for b in out["breakdown"]] == list(R.WEIGHTS)
    assert out["score"] == pytest.approx(sum(b["contribution"] for b in out["breakdown"]), abs=1)
    demand = out["breakdown"][0]
    assert demand["points"] == 50 and demand["weight"] == 25 and demand["value"] == 1.0


def test_a_small_niche_gets_no_score_but_keeps_its_numbers():
    out = R.score({**BEST, "status": "insufficient-data"})
    assert out["score"] is None and out["band"] is None and out["reason"] == "insufficient-data"
    assert out["breakdown"][0]["points"] == 100


def test_unknown_terms_are_left_out_and_the_rest_re_weighted():
    out = R.score({**BEST, "templateShare": None, "policyShare": None})
    assert out["coverage"] == 0.8 and out["score"] == 100
    assert [b["points"] for b in out["breakdown"][-2:]] == [None, None]


def test_too_little_known_is_few_signals_not_a_low_score():
    out = R.score({"status": "stable", "demandRatio": 1.2, "supplyRatio": None,
                   "newcomersShare": None, "rpmMid": 3.0, "templateShare": None, "policyShare": None})
    assert out["score"] is None and out["reason"] == "few-signals" and out["coverage"] == 0.4


def test_missing_input_keys_count_as_unknown():
    assert R.score({"status": "stable"})["reason"] == "few-signals"


def test_scored_niches_first_best_first_then_the_rest_biggest_first():
    es = [{"score": None, "videos": 10}, {"score": 40, "videos": 5}, {"score": None, "videos": 99},
          {"score": 70, "videos": 1}]
    assert [(e["score"], e["videos"]) for e in sorted(es, key=R.sort_key)] == [
        (70, 1), (40, 5), (None, 99), (None, 10)]

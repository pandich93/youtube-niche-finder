"""Pure-function tests for domain/idea_verdicts.py (stage 17). No DB, no
network. Run: python3 tests/test_idea_verdicts.py (or pytest)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import domain.idea_verdicts as V  # noqa: E402


def test_no_matches_is_free():
    out = V.verdict([])
    assert out["verdict"] == "free"
    assert out["daysSinceLastCoverage"] is None


def test_a_match_younger_than_recent_days_is_recent_even_with_a_high_outlier():
    matches = [{"ageDays": 10, "outlierScore": 9.0}]
    out = V.verdict(matches, recent_days=90)
    assert out["verdict"] == "recent"
    assert out["daysSinceLastCoverage"] == 10


def test_exactly_at_the_recent_days_boundary_counts_as_recent():
    matches = [{"ageDays": 90, "outlierScore": 5.0}]
    out = V.verdict(matches, recent_days=90)
    assert out["verdict"] == "recent"


def test_one_day_past_the_boundary_with_high_outlier_is_proven():
    matches = [{"ageDays": 91, "outlierScore": 5.0}]
    out = V.verdict(matches, recent_days=90, proven_outlier=2.0)
    assert out["verdict"] == "proven"
    assert out["performanceBand"] == "proven"


def test_old_and_low_outlier_is_flopped():
    matches = [{"ageDays": 200, "outlierScore": 0.3}]
    out = V.verdict(matches, recent_days=90, flop_outlier=0.5)
    assert out["verdict"] == "flopped"
    assert out["performanceBand"] == "flop"


def test_old_and_middling_outlier_is_flopped_not_proven():
    # between flop_outlier and proven_outlier -- ambiguous signal, verdict
    # stays conservative (see module docstring)
    matches = [{"ageDays": 200, "outlierScore": 1.2}]
    out = V.verdict(matches, recent_days=90, proven_outlier=2.0, flop_outlier=0.5)
    assert out["verdict"] == "flopped"
    assert out["performanceBand"] == "moderate"


def test_uses_the_most_recent_match_for_days_since_coverage():
    matches = [{"ageDays": 200, "outlierScore": 0.1}, {"ageDays": 120, "outlierScore": 3.0}]
    out = V.verdict(matches, recent_days=90)
    assert out["daysSinceLastCoverage"] == 120


def test_uses_the_best_outlier_across_all_matches_not_just_the_newest():
    matches = [{"ageDays": 200, "outlierScore": 0.1}, {"ageDays": 150, "outlierScore": 5.0}]
    out = V.verdict(matches, recent_days=90, proven_outlier=2.0)
    assert out["verdict"] == "proven"
    assert out["bestOutlierScore"] == 5.0


def test_matches_with_no_outlier_score_at_all_are_flopped_not_proven():
    matches = [{"ageDays": 200, "outlierScore": None}]
    out = V.verdict(matches, recent_days=90)
    assert out["verdict"] == "flopped"
    assert out["bestOutlierScore"] is None
    assert out["performanceBand"] is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)

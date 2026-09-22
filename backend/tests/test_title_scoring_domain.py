"""Pure-function tests for domain/title_scoring.py (stage 09). No DB, no
network. Run: python3 tests/test_title_scoring_domain.py (or pytest)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import domain.title_scoring as TS  # noqa: E402

PATTERNS = [{"keyword": "i tried", "outlierLift": 2.5}, {"keyword": "for 30 days", "outlierLift": 1.8}]


def test_length_within_range_is_flagged_ok():
    signals = TS.deterministic_signals("x" * 50, [])
    assert signals["lengthOk"] is True


def test_length_too_short_or_too_long_is_flagged_not_ok():
    assert TS.deterministic_signals("short", [])["lengthOk"] is False
    assert TS.deterministic_signals("x" * 100, [])["lengthOk"] is False


def test_detects_digits_and_brackets():
    signals = TS.deterministic_signals("Top 10 Tips (2026 Edition)", [])
    assert signals["hasNumber"] is True
    assert signals["hasBrackets"] is True


def test_no_digits_or_brackets_when_absent():
    signals = TS.deterministic_signals("A Simple Guide", [])
    assert signals["hasNumber"] is False
    assert signals["hasBrackets"] is False


def test_matches_patterns_case_insensitively():
    signals = TS.deterministic_signals("I Tried Cold Showers For 30 Days", PATTERNS)
    assert set(signals["matchedPatterns"]) == {"i tried", "for 30 days"}


def test_no_pattern_match_gives_empty_list():
    signals = TS.deterministic_signals("Something Unrelated", PATTERNS)
    assert signals["matchedPatterns"] == []


def test_near_duplicate_flag_uses_the_threshold():
    below = TS.deterministic_signals("t", [], near_duplicate_score=0.85)
    at = TS.deterministic_signals("t", [], near_duplicate_score=0.9)
    above = TS.deterministic_signals("t", [], near_duplicate_score=0.95)
    assert below["isLikelyDuplicate"] is False
    assert at["isLikelyDuplicate"] is True
    assert above["isLikelyDuplicate"] is True


def test_near_duplicate_score_of_none_is_not_treated_as_a_duplicate():
    signals = TS.deterministic_signals("t", [], near_duplicate_score=None)
    assert signals["isLikelyDuplicate"] is False


def test_deterministic_score_rewards_good_length_and_matched_patterns():
    good = TS.deterministic_signals("I Tried Cold Showers For 30 Days And Here's What Happened",
                                    PATTERNS)
    bad = TS.deterministic_signals("x", [])
    assert TS.deterministic_score(good) > TS.deterministic_score(bad)


def test_deterministic_score_penalizes_likely_duplicates():
    dup = TS.deterministic_signals("x" * 50, [], near_duplicate_score=0.95)
    fresh = TS.deterministic_signals("x" * 50, [], near_duplicate_score=0.1)
    assert TS.deterministic_score(dup) < TS.deterministic_score(fresh)


def test_deterministic_score_stays_within_0_100():
    many_patterns = [{"keyword": f"p{i}"} for i in range(20)]
    title = " ".join(f"p{i}" for i in range(20)) + " " * 40
    signals = TS.deterministic_signals(title, many_patterns)
    score = TS.deterministic_score(signals)
    assert 0 <= score <= 100


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

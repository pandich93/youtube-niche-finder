"""Pure-function tests for domain/template_risk.py (plan 01): how much a
channel's recent uploads look like one template repeated -- the pattern
YouTube's "inauthentic content" policy targets. No DB, no network.
Run: python3 tests/test_template_risk_domain.py (or pytest)
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import template_risk as TR  # noqa: E402

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _vecs(rows):
    return [np.array(r, dtype=np.float32) for r in rows]


def _dates(gaps_days):
    out, t = [T0], T0
    for g in gaps_days:
        t += timedelta(days=g)
        out.append(t)
    return [d.isoformat() for d in out]


# ------------------------------------------------------------ title similarity

def test_identical_titles_have_similarity_one_and_orthogonal_zero():
    assert TR.title_self_similarity(_vecs([[1, 0], [1, 0], [1, 0]])) == 1.0
    assert TR.title_self_similarity(_vecs([[1, 0], [0, 1]])) == 0.0


def test_similarity_ignores_vector_length():
    assert TR.title_self_similarity(_vecs([[2, 0], [5, 0]])) == 1.0


def test_similarity_needs_two_vectors():
    assert TR.title_self_similarity(_vecs([[1, 0]])) is None
    assert TR.title_self_similarity([]) is None


# ------------------------------------------------------------ shared skeleton

def test_template_share_finds_a_shared_opening_and_a_shared_tail():
    titles = ["Top 10 facts about Rome", "Top 10 facts about Egypt", "Top 10 facts about Mars",
              "Top 10 facts about Greece", "A completely different story"]
    assert TR.title_template_share(titles) == 0.8
    tails = [f"Track {i} | Afro House 2026" for i in range(6)] + ["Interview with a chef"]
    assert round(TR.title_template_share(tails), 2) == round(6 / 7, 2)


def test_template_share_is_zero_for_varied_titles_and_needs_titles():
    varied = ["How I quit my job", "The war nobody remembers", "Why bridges fall",
              "Cooking with fire", "A tour of Lisbon"]
    assert TR.title_template_share(varied) == 0.0
    assert TR.title_template_share([]) is None


def test_two_matches_are_not_a_template():
    assert TR.title_template_share(["Top 10 a", "Top 10 b", "Something else", "Another one"]) == 0.0


# ------------------------------------------------------------ durations / cadence

def test_duration_uniformity_is_the_coefficient_of_variation():
    assert TR.duration_uniformity([600, 600, 600, 600]) == 0.0
    assert TR.duration_uniformity([100, 300]) == 0.5
    assert TR.duration_uniformity([600]) is None
    assert TR.duration_uniformity([0, 0, 0]) is None


def test_cadence_regularity_is_low_for_a_metronome_and_high_for_bursts():
    assert TR.cadence_regularity(_dates([1, 1, 1, 1, 1])) == 0.0
    assert TR.cadence_regularity(_dates([1, 9, 1, 12, 2])) > 0.9
    assert TR.cadence_regularity(_dates([1, 1])) is None          # only 2 gaps -> too few to compare
    assert TR.cadence_regularity(["bad", None, ""]) is None


# ------------------------------------------------------------ score

CONVEYOR = {"similarity": 0.94, "templateShare": 0.9, "durationCv": 0.03, "cadenceCv": 0.1, "videos": 30}
HUMAN = {"similarity": 0.45, "templateShare": 0.0, "durationCv": 0.9, "cadenceCv": 1.4, "videos": 30}


def test_conveyor_is_high_risk_and_a_varied_channel_is_low():
    hi, lo = TR.template_risk_score(CONVEYOR), TR.template_risk_score(HUMAN)
    assert hi["level"] == "high" and hi["score"] >= 70
    assert lo["level"] == "low" and lo["score"] <= 15
    assert len(hi["reasons"]) >= 3 and lo["reasons"] == []


def test_one_strong_signal_alone_does_not_make_a_channel_high_risk():
    # A streamer/podcast: similar titles but varied lengths, irregular schedule.
    s = TR.template_risk_score({"similarity": 0.84, "templateShare": 0.1, "durationCv": 0.76,
                                "cadenceCv": 1.2, "videos": 15})
    assert s["level"] in ("low", "medium")


def test_too_few_videos_is_insufficient_data_not_a_score():
    s = TR.template_risk_score({**CONVEYOR, "videos": TR.MIN_VIDEOS - 1})
    assert s["level"] == "insufficient-data" and s["score"] is None
    assert str(TR.MIN_VIDEOS) in s["note"]


def test_missing_signals_are_dropped_and_the_rest_reweighted():
    s = TR.template_risk_score({"similarity": 0.94, "templateShare": 0.9, "durationCv": None,
                                "cadenceCv": None, "videos": 30})
    assert s["level"] == "high"
    assert TR.template_risk_score({"similarity": None, "videos": 30})["level"] == "insufficient-data"


def test_score_always_carries_the_heuristic_disclaimer():
    for sig in (CONVEYOR, HUMAN):
        assert "not YouTube's verdict" in TR.template_risk_score(sig)["note"]


def _run_all():
    ns = dict(globals())
    tests = [(n, f) for n, f in ns.items() if n.startswith("test_")]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)

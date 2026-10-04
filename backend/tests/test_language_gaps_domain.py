"""Tests for domain/language_gaps.py (plan 26): pure rules, no database.
Run with pytest, or directly: python3 tests/test_language_gaps_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pytest  # noqa: E402

from domain import language_gaps as L  # noqa: E402


def _n(sim, score=None, ch="c"):
    return {"videoId": f"v{sim}", "channelId": ch, "similarity": sim, "outlierScore": score}


@pytest.mark.parametrize("raw,expected", [
    ("en", "en"), ("en-US", "en"), ("en_GB", "en"), ("EN", "en"), (" ru-RU ", "ru"),
    ("fil", "fil"), ("und", None), ("zxx", None), ("mul", None), ("un", None), ("zx", None),
    ("", None), (None, None), ("123", None), ("x", None),
])
def test_normalize_lang(raw, expected):
    assert L.normalize_lang(raw) == expected


def test_nothing_close_is_open_and_keeps_the_nearest_for_context():
    v = L.verdict([_n(0.5, 9.0), _n(0.4)])
    assert v["verdict"] == "open" and v["matches"] == [] and v["similarCount"] == 0
    assert v["nearest"]["similarity"] == 0.5


def test_no_neighbours_at_all_is_open_without_a_nearest():
    v = L.verdict([])
    assert v["verdict"] == "open" and v["nearest"] is None
    assert L.verdict(None)["verdict"] == "open"


def test_close_but_no_outlier_is_thin():
    v = L.verdict([_n(0.7, 1.1), _n(0.65, None)])
    assert v["verdict"] == "thin" and v["similarCount"] == 2


def test_a_close_outlier_is_covered():
    v = L.verdict([_n(0.7, 1.1), _n(0.64, 3.0)])
    assert v["verdict"] == "covered"
    assert [m["similarity"] for m in v["matches"]] == [0.7, 0.64]


def test_an_outlier_below_the_similarity_floor_does_not_cover():
    assert L.verdict([_n(0.5, 9.0)])["verdict"] == "open"


def test_the_threshold_is_inclusive_and_can_be_moved():
    assert L.verdict([_n(L.MIN_SIMILARITY, 1.0)])["verdict"] == "thin"
    assert L.verdict([_n(0.7, 1.0)], min_similarity=0.8)["verdict"] == "open"


def test_matches_are_capped_but_every_close_video_counts_for_the_verdict():
    ns = [_n(0.9 - i * 0.01, 1.0) for i in range(5)] + [_n(0.65, 5.0)]
    v = L.verdict(ns)
    assert len(v["matches"]) == L.MAX_MATCHES and v["similarCount"] == 6
    assert v["verdict"] == "covered"


def test_demand_counts_each_other_channel_once():
    d = L.demand(8.0, [_n(0.7, 3.0, "a"), _n(0.7, 5.0, "a"), _n(0.7, 2.0, "b"), _n(0.7, 1.0, "c")])
    assert d == {"score": 8.0, "otherChannelsHit": 2}
    assert L.demand(8.0, None) == {"score": 8.0, "otherChannelsHit": 0}


def test_cards_sort_open_then_thin_then_covered_strongest_demand_first():
    def card(v, ch, score):
        return {"verdict": v, "demand": {"otherChannelsHit": ch, "score": score}}
    cards = [card("covered", 5, 50), card("thin", 0, 9), card("open", 0, 4),
             card("open", 2, 3), card("open", 2, 7)]
    got = sorted(cards, key=L.sort_key)
    assert [(c["verdict"], c["demand"]["otherChannelsHit"], c["demand"]["score"]) for c in got] == [
        ("open", 2, 7), ("open", 2, 3), ("open", 0, 4), ("thin", 0, 9), ("covered", 5, 50)]


def test_a_thin_corpus_gets_a_hint_and_a_big_one_does_not():
    assert "12" in L.corpus_hint(12, "ru") and "русском языке" in L.corpus_hint(12, "ru")
    assert L.corpus_hint(L.THIN_CORPUS_VIDEOS, "ru") is None
    assert "«xx»" not in L.corpus_hint(3, "pt") and "«qq»" in L.corpus_hint(3, "qq")

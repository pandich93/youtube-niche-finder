"""Tests for domain/repeatability.py (plan 21): did this format work for
several independent channels, or was it one channel's luck? Pure; no DB.
Run with pytest, or directly: python3 tests/test_repeatability_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import repeatability as R  # noqa: E402


def m(channel, score, vid=None):
    return {"channelId": channel, "outlierScore": score, "videoId": vid or f"{channel}-{score}",
            "title": "t", "similarity": 0.8}


def test_three_independent_channels_over_2x_is_repeatable():
    r = R.verdict([m("a", 3.0), m("b", 2.5), m("c", 2.1), m("d", 0.8), m("e", 1.0)])
    assert r["verdict"] == "repeatable"
    assert r["channels"] == 5 and r["channelsHit"] == 3
    assert r["medianChannelBest"] == 2.1


def test_one_channel_counts_once_however_many_hits_it_has():
    r = R.verdict([m("a", 5.0, "v1"), m("a", 4.0, "v2"), m("a", 3.0, "v3"),
                   m("b", 0.7), m("c", 0.9)])
    assert r["channelsHit"] == 1 and r["channels"] == 3
    assert r["verdict"] == "mixed"


def test_nobody_else_got_an_outlier_is_one_off():
    r = R.verdict([m("a", 0.9), m("b", 1.2), m("c", 0.4), m("d", 1.5), m("e", 0.3)])
    assert r["verdict"] == "one_off" and r["channelsHit"] == 0


def test_few_similar_videos_is_unknown_not_one_off():
    r = R.verdict([m("a", 0.9), m("b", 0.4)])
    assert r["verdict"] == "unknown" and r["reason"] == "few-similar-videos"


def test_examples_are_each_channels_best_strongest_first():
    r = R.verdict([m("a", 2.0, "va1"), m("a", 6.0, "va2"), m("b", 3.0), m("c", 0.5), m("d", 1.0)])
    assert [e["videoId"] for e in r["examples"][:2]] == ["va2", "b-3.0"]


def test_missing_scores_do_not_count_as_hits_or_channels():
    r = R.verdict([m("a", None), m("b", 3.0), m("c", 2.0), m("d", 2.2), m("e", 0.5)])
    assert r["channels"] == 4 and r["channelsHit"] == 3

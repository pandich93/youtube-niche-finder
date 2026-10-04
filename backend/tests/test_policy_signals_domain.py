"""Tests for domain/policy_signals.py (plan 22): signals a channel shows,
from public data, for YouTube's three "inauthentic content" categories --
never a single risk percentage. Pure; no DB.
Run with pytest, or directly: python3 tests/test_policy_signals_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import policy_signals as P  # noqa: E402


def test_shock_markers_in_english_and_russian():
    assert P.shock_markers("You WON'T BELIEVE what happened 😱")
    assert P.shock_markers("ШОК! Вы не поверите, что случилось")
    assert P.shock_markers("The most DISTURBING case ever")
    assert not P.shock_markers("How I built a small AI lab at home")
    assert not P.shock_markers("NASA and the ISS: a short history")   # acronyms are not shouting


def test_shock_share_counts_titles_with_any_marker():
    share, examples = P.shock_share(["SHOCKING truth", "calm title", "вы не поверите", "ok"])
    assert share == 0.5 and examples == ["SHOCKING truth", "вы не поверите"]
    assert P.shock_share([]) == (None, [])


def test_sensitive_topics_from_youtube_topics_and_titles():
    assert P.sensitive_topic("Morning routine", ["https://en.wikipedia.org/wiki/Health"]) == "health"
    assert P.sensitive_topic("Morning routine", ["Lifestyle (sociology)", "Health"]) == "health"
    assert P.sensitive_topic("Morning routine", ["Politics"]) == "politics"
    assert P.sensitive_topic("How to invest in ETFs", []) == "finance"
    assert P.sensitive_topic("Как подать в суд на соседа", []) == "legal"
    assert P.sensitive_topic("Election night explained", []) == "politics"
    assert P.sensitive_topic("Minecraft build tour", []) is None


def _videos(n, title="calm", topics=(), synthetic=None):
    return [{"title": title, "topicCategories": list(topics), "containsSyntheticMedia": synthetic}
            for _ in range(n)]


def test_three_categories_and_no_overall_percentage():
    r = P.signals(template={"level": "high", "score": 80, "reasons": ["titles alike"]},
                  thumb_similarity=0.9, videos=_videos(10), faceless=None)
    assert set(r["categories"]) == {"generic_repetitive", "unsatisfying", "ai_persona_sensitive"}
    assert r["categories"]["generic_repetitive"]["level"] == "high"
    assert "score" not in r and "percent" not in str(r).lower()
    for c in r["categories"].values():
        assert c["policyUrl"].startswith("https://support.google.com/youtube/")


def test_template_reasons_come_through_as_text():
    t = {"level": "medium", "score": 50,
         "reasons": [{"signal": "templateShare", "value": 0.8, "text": "80% of titles reuse an opening"}]}
    r = P.signals(template=t, thumb_similarity=None, videos=_videos(10), faceless=None)
    assert r["categories"]["generic_repetitive"]["reasons"] == [
        {"signal": "templateShare", "value": 0.8, "text": "80% of titles reuse an opening"}]


def test_shock_titles_raise_the_unsatisfying_category():
    vids = _videos(6, title="SHOCKING twist 😱") + _videos(4)
    r = P.signals(template=None, thumb_similarity=None, videos=vids, faceless=None)
    assert r["categories"]["unsatisfying"]["level"] == "high"
    assert r["categories"]["unsatisfying"]["value"] == 0.6


def test_ai_persona_needs_a_sensitive_topic_and_an_ai_sign_and_stays_watch():
    sensitive_ai = _videos(5, title="Doctor explains your symptoms", synthetic=1)
    r = P.signals(template=None, thumb_similarity=None, videos=sensitive_ai, faceless=None)
    # never "high": a persona cannot be seen in public data
    assert r["categories"]["ai_persona_sensitive"]["level"] == "watch"
    no_ai = _videos(5, title="Doctor explains your symptoms", synthetic=0)
    assert P.signals(template=None, thumb_similarity=None, videos=no_ai,
                     faceless=False)["categories"]["ai_persona_sensitive"]["level"] == "none"
    faceless = _videos(5, title="Your tax return, explained")
    assert P.signals(template=None, thumb_similarity=None, videos=faceless,
                     faceless=True)["categories"]["ai_persona_sensitive"]["level"] == "watch"


def test_too_few_videos_is_insufficient_not_none():
    r = P.signals(template={"level": "insufficient-data"}, thumb_similarity=None,
                  videos=_videos(2), faceless=None)
    assert r["categories"]["unsatisfying"]["level"] == "insufficient-data"
    assert r["categories"]["generic_repetitive"]["level"] == "insufficient-data"

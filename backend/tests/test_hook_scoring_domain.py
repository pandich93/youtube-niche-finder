"""Pure-function tests for domain/hook_scoring.py (plan 10): which part of a
transcript counts as the hook, and the rule-based 0-100 score of its text.
No database, no network. Run with pytest, or directly:
python3 tests/test_hook_scoring_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import hook_scoring as HS  # noqa: E402


def _seg(start, words):
    return {"startSec": start, "text": " ".join(f"w{start}x{i}" for i in range(words))}


def _pts(res, name):
    f = res["features"][name]
    return None if f is None else f["points"]


def _penalty(res):
    return sum(p["points"] for p in res["penalties"])


# ------------------------------------------------------------ hook_text

def test_hook_text_takes_segments_before_the_30_second_mark():
    segs = [_seg(s, 3) for s in (0, 5, 12, 20, 28, 33, 41)]
    out = HS.hook_text(segs)
    assert out["mode"] == "timed"
    assert out["words"] == 15          # segments at 0/5/12/20/28
    assert out["startsAtSec"] == 0
    assert out["spanSec"] == 33
    assert out["warning"] is None
    assert "w33x0" not in out["text"] and "w28x0" in out["text"]


def test_hook_text_warns_when_the_transcript_starts_late():
    out = HS.hook_text([_seg(65, 4), _seg(80, 4), _seg(100, 4)])
    assert out["startsAtSec"] == 65
    assert out["warning"]
    assert out["mode"] == "timed"
    assert out["words"] == 8           # 65 and 80 are inside 65..95


def test_hook_text_untimed_takes_the_first_75_words():
    out = HS.hook_text([{"startSec": None, "text": " ".join(["word"] * 200)}])
    assert out["mode"] == "untimed"
    assert out["words"] == 75
    assert out["spanSec"] is None
    assert out["startsAtSec"] is None


def test_hook_text_caps_a_single_timed_segment_at_110_words():
    out = HS.hook_text([{"startSec": 0, "text": " ".join(["word"] * 300)}])
    assert out["mode"] == "timed"
    assert out["words"] == HS.MAX_TIMED_WORDS == 110
    assert out["spanSec"] is None      # a cut text no longer matches its time span


def test_hook_text_of_nothing_is_empty():
    out = HS.hook_text([])
    assert out["words"] == 0 and out["text"] == ""


def test_score_of_empty_text_is_insufficient_data():
    for empty in ("", "   ", None):
        res = HS.score_hook(empty)
        assert res["score"] is None
        assert res["level"] == "insufficient-data"
        assert res["tips"] == []


# ------------------------------------------------- normalisation, language

def test_normalize_and_detect_language():
    assert HS.normalize("  Ёлка   It’s  ") == "елка it's"
    assert HS.detect_language("Как заработать на мойке?") == "ru"
    assert HS.detect_language("How to earn from a car wash") == "en"
    assert HS.detect_language("Как это работает with a car wash") == "ru"


# ------------------------------------------------------------ false positives

def test_a_year_or_a_timecode_is_not_a_number():
    for text in ("In 2024 nothing changed for the owners.",
                 "At 3:45 we switch to the second location.",
                 "В 2019 году всё поменялось."):
        res = HS.score_hook(text)
        assert res["features"]["number"]["hit"] is False, text
        assert _pts(res, "number") == 0


def test_a_bare_number_scores_less_than_a_number_with_a_unit():
    assert _pts(HS.score_hook("I visited 7 places."), "number") == 8
    assert _pts(HS.score_hook("I visited 7 places in 30 days."), "number") == 20


def test_subscribers_count_is_a_number_not_a_subscribe_penalty():
    res = HS.score_hook("У меня 1000 подписчиков.")
    assert _pts(res, "number") == 20
    assert _penalty(res) == 0
    res = HS.score_hook("Подпишись на канал.")
    assert _penalty(res) == -5


def test_you_know_filler_is_not_a_second_person_address():
    assert _pts(HS.score_hook("It was, you know, really hard."), "secondPerson") == 0
    res = HS.score_hook("Did you know that most car washes fail?")
    assert _pts(res, "secondPerson") == 6


def test_greeting_costs_eight_and_a_question_still_counts():
    res = HS.score_hook("Hey, did you know that most car washes fail in a year?")
    assert _penalty(res) == -8
    assert _pts(res, "question") == 15


def test_filler_phrases_are_capped_at_minus_ten():
    text = ("Welcome back to my channel. My name is Ben. Before we start, don't forget to "
            "subscribe and hit the bell.")
    res = HS.score_hook(text)
    filler = [p for p in res["penalties"] if p["id"] == "filler"]
    assert len(filler) == 1 and filler[0]["points"] == -10


def test_repeating_one_intrigue_word_counts_once():
    assert _pts(HS.score_hook("Secret secret secret."), "intrigue") == 10
    assert _pts(HS.score_hook("A secret and a myth."), "intrigue") == 20


def test_russian_question_without_punctuation_is_still_a_question():
    res = HS.score_hook("а вы когда-нибудь задумывались как устроена мойка")
    assert res["punctuated"] is False
    assert _pts(res, "question") == 15
    assert res["features"]["firstSentence"] is None


def test_a_question_mark_without_addressing_the_viewer_is_worth_less():
    res = HS.score_hook("The car wash closed last year. Was it the price?")
    assert _pts(res, "question") == 8


def test_pace_only_when_the_span_is_known():
    text = ("I spent $10,000 on a car wash startup, and nobody tells you this. "
            "Have you ever wondered how much these owners really earn? "
            "By the end of this video, you'll know the three mistakes that cost me 6 months.")
    assert HS.score_hook(text)["features"]["pace"] is None
    assert _pts(HS.score_hook(text, span_sec=30), "pace") == 0     # ~1.3 words/s
    assert _pts(HS.score_hook(text, span_sec=15), "pace") == 5     # ~2.7 words/s


# ------------------------------------------------------------ levels, tips

def test_level_boundaries():
    assert HS.level_for(34) == "weak"
    assert HS.level_for(35) == "ok"
    assert HS.level_for(64) == "ok"
    assert HS.level_for(65) == "strong"
    assert HS.level_for(0) == "weak" and HS.level_for(100) == "strong"


def test_tips_are_at_most_three_and_follow_the_language_of_the_text():
    en = HS.score_hook("Hello everyone. The history of the car wash goes back a long time ago.")
    assert 1 <= len(en["tips"]) <= 3
    assert all(t["text"] and t["id"] for t in en["tips"])
    assert not any(ch in "абвгдежзиклмнопрстуфхцчшщыэюя" for t in en["tips"] for ch in t["text"].lower())
    ru = HS.score_hook("Автомойки появились в России давно и с тех пор их стало больше.")
    assert 1 <= len(ru["tips"]) <= 3
    assert any(ch in "абвгдежзиклмнопрстуфхцчшщыэюя" for t in ru["tips"] for ch in t["text"].lower())
    assert ru["language"] == "ru" and en["language"] == "en"


def test_tips_do_not_claim_causality():
    for text in ("The history of the car wash goes back a long time.",
                 "Hey guys, welcome back to my channel."):
        for t in HS.score_hook(text)["tips"]:
            assert "will make" not in t["text"].lower() and "guarantee" not in t["text"].lower()


def test_result_carries_the_disclaimer():
    assert "not" in HS.score_hook("Any text here.")["note"]
    assert HS.score_hook("Any text here.")["note"] == HS.DISCLAIMER


# --------------------------------------------- synthetic examples (real values)

EXAMPLES = [
    ("I spent $10,000 on a car wash startup, and nobody tells you this. Have you ever wondered "
     "how much these owners really earn? By the end of this video, you'll know the three "
     "mistakes that cost me 6 months.", "strong", 100),
    ("Я потратил 300 тысяч рублей на мойку самообслуживания, и никто не говорит, чем это "
     "закончилось. Ты когда-нибудь задумывался, сколько на самом деле зарабатывают владельцы? "
     "В конце видео ты узнаешь три ошибки, которые стоили мне полгода.", "strong", 91),
    ("Hey guys, welcome back to my channel. Before we get started, don't forget to like and "
     "subscribe. Today we're going to talk about car washes and some things about them.",
     "weak", 0),
    ("Всем привет, с вами Иван, добро пожаловать на мой канал. Сегодня мы поговорим про мойки "
     "самообслуживания. Не забудьте подписаться и поставить лайк.", "weak", 0),
    ("In this video I'll show you how to pick a location for your first self-serve car wash. "
     "You'll learn what to check before signing a lease.", "ok", 37),
    ("have you ever wondered why most car washes fail in the first year i lost 40000 dollars "
     "finding out and here is the one thing nobody tells you", "strong", 74),
    ("I gave 100 people $1,000 each, but there's a catch. Only one of them will keep it. "
     "Here's what happened.", "ok", 55),
    ("The history of the car wash goes back to the early nineteen hundreds when the first "
     "automated systems were installed in Detroit.", "weak", 5),
    ("Почему все мойки самообслуживания закрываются через год? Сейчас разберём три причины.",
     "ok", 60),
    ("Автомойки самообслуживания появились в России в начале двухтысячных годов и с тех пор "
     "их стало заметно больше.", "weak", 5),
]


def test_synthetic_examples_land_in_the_expected_level_and_score():
    for i, (text, level, score) in enumerate(EXAMPLES, 1):
        res = HS.score_hook(text)
        assert res["level"] == level, (i, res["score"])
        assert res["score"] == score, (i, res["score"])


def test_unpunctuated_autosubtitles_example():
    res = HS.score_hook(EXAMPLES[5][0])
    assert res["punctuated"] is False
    assert res["features"]["firstSentence"] is None


# ------------------------------------------------------------ aggregate_hooks

def _res(**hits):
    """A minimal score_hook-shaped result with the given features hit."""
    names = ("question", "number", "promise", "intrigue", "secondPerson", "firstSentence", "pace")
    feats = {n: ({"points": 1, "max": 1, "hit": bool(hits.get(n)), "evidence": []}
                 if n != "pace" else None) for n in names}
    return {"score": hits.get("score", 50), "features": feats}


def test_aggregate_finds_features_more_common_in_outliers():
    outliers = [_res(number=i < 9, question=i < 5) for i in range(10)]
    regular = [_res(number=i < 3, question=i < 4) for i in range(10)]
    agg = HS.aggregate_hooks(outliers, regular)
    assert agg["reliable"] is True
    assert agg["features"]["number"]["diffPp"] == 60
    assert agg["features"]["number"]["outlierRate"] == 0.9
    assert "number" in agg["moreCommonInOutliers"]
    assert agg["features"]["question"]["diffPp"] == 10
    assert "question" not in agg["moreCommonInOutliers"]     # 10 pp is below MIN_DIFF_PP
    assert agg["outliers"]["n"] == 10 and agg["regular"]["n"] == 10


def test_aggregate_with_too_few_videos_is_not_reliable():
    agg = HS.aggregate_hooks([_res(score=80)] * 9, [_res(score=20)] * 10)
    assert agg["reliable"] is False
    assert agg["outliers"]["meanScore"] == 80 and agg["regular"]["meanScore"] == 20


def test_aggregate_skips_results_without_a_score():
    empty = {"score": None, "features": {}}
    agg = HS.aggregate_hooks([empty, _res()], [])
    assert agg["outliers"]["n"] == 1
    assert agg["regular"]["n"] == 0 and agg["regular"]["meanScore"] is None


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

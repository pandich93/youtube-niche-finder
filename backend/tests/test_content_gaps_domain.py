"""Tests for domain/content_gaps.py (plan 03): picking viewer questions and
requests out of comments without an LLM, grouping near-duplicates, and scoring
demand against coverage. Pure functions -- no DB, no network.
Run with pytest, or directly: python3 tests/test_content_gaps_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402

from domain import content_gaps as G  # noqa: E402


def texts(comments):
    return [q["text"] for q in G.extract_questions(comments)]


def c(text, likes=0):
    return {"text": text, "likeCount": likes, "author": "someone"}


# ------------------------------------------------------------ extract_questions

def test_keeps_english_questions_and_requests():
    out = texts([
        c("How do you keep the sourdough starter alive in winter?"),
        c("Please make a video about gluten free bread"),
        c("Can you do a tutorial on laminated dough"),
    ])
    assert len(out) == 3


def test_keeps_russian_questions_and_requests():
    out = texts([
        c("А как хранить закваску зимой, если дома холодно?"),
        c("Сделайте видео про хлеб без глютена пожалуйста"),
        c("Расскажи подробнее про слоёное тесто и масло"),
    ])
    assert len(out) == 3


def test_drops_praise_spam_and_short_noise():
    out = texts([
        c("Great video, thanks!"),
        c("First!"),
        c("Who's watching in 2026?"),
        c("Кто смотрит в 2026?"),
        c("Check out my channel for more bread videos?"),
        c("why?"),
        c("Отличное видео, спасибо!"),
    ])
    assert out == []


def test_drops_links_and_timestamp_comments():
    out = texts([
        c("How is this different from https://example.com/bread recipe?"),
        c("At 3:45 how did you shape it so fast?"),
    ])
    assert out == []


def test_bare_question_word_without_question_mark_is_not_enough():
    # "как" / "how" alone is too broad: "как же круто" is praise, not a question.
    assert texts([c("Как же круто у тебя получилось всё это")]) == []


def test_cleans_html_and_keeps_likes_but_not_author():
    out = G.extract_questions([c("How do you proof&nbsp;dough <br>in a cold kitchen?", 12)])
    assert out == [{"text": "How do you proof dough in a cold kitchen?", "likeCount": 12}]


def test_deduplicates_identical_questions_keeping_the_most_liked():
    out = G.extract_questions([
        c("How do you proof dough in a cold kitchen?", 2),
        c("how do you proof dough in a cold kitchen?", 9),
    ])
    assert len(out) == 1 and out[0]["likeCount"] == 9


def test_missing_text_and_likes_are_tolerated():
    assert G.extract_questions([{"text": None}, {}, {"text": "How do you proof dough at home?"}]) \
        == [{"text": "How do you proof dough at home?", "likeCount": 0}]


# ------------------------------------------------------------ cluster_questions

def test_clusters_by_vectors_above_threshold():
    items = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    vecs = [np.array([1.0, 0.0]), np.array([0.95, 0.05]), np.array([0.0, 1.0])]
    groups = G.cluster_questions(items, vecs, threshold=0.8)
    assert sorted(sorted(g) for g in groups) == [[0, 1], [2]]


def test_clusters_by_normalised_text_without_vectors():
    items = [{"text": "How do you proof dough?"}, {"text": "how do you proof dough"},
             {"text": "What flour is best?"}]
    groups = G.cluster_questions(items, None)
    assert sorted(sorted(g) for g in groups) == [[0, 1], [2]]


# ------------------------------------------------------------ scoring

def test_demand_grows_with_likes_and_sources():
    one = G.demand([{"likeCount": 0}], sources=1)
    liked = G.demand([{"likeCount": 50}], sources=1)
    spread = G.demand([{"likeCount": 0}, {"likeCount": 0}], sources=2)
    assert liked > one and spread > one


def test_gap_status_thresholds():
    assert G.gap_status(None) == "free"
    assert G.gap_status(0.2) == "free"
    assert G.gap_status(G.PARTIAL_AT) == "partial"
    assert G.gap_status(G.COVERED_AT) == "covered"


def test_gap_score_falls_with_coverage():
    assert G.gap_score(3.0, None) > G.gap_score(3.0, 0.6) > G.gap_score(3.0, 0.9)
    assert G.gap_score(3.0, 1.0) == 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

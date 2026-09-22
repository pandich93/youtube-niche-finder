"""Pure-function tests for domain/keywords.py's literal-tag aggregation
(feeds application.discovery.top_tags_by_category). No DB, no network.

Run: python3 tests/test_keywords_domain.py (or pytest tests/test_keywords_domain.py)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import domain.keywords as K  # noqa: E402


def test_literal_tags_lowercases_and_trims():
    row = {"tags": '["Faceless YouTube", " automation "]'}
    assert K.literal_tags_for_video(row) == {"faceless youtube", "automation"}


def test_literal_tags_dedupes_case_insensitively():
    row = {"tags": '["AI", "ai", " ai "]'}
    assert K.literal_tags_for_video(row) == {"ai"}


def test_literal_tags_ignores_blank_and_non_string_entries():
    row = {"tags": '["real tag", "", "   ", 42]'}
    assert K.literal_tags_for_video(row) == {"real tag"}


def test_literal_tags_handles_missing_or_malformed_tags():
    assert K.literal_tags_for_video({"tags": None}) == set()
    assert K.literal_tags_for_video({"tags": "not json"}) == set()
    assert K.literal_tags_for_video({}) == set()


def test_literal_tags_does_not_split_multi_word_tags():
    # unlike phrases_for_video, a whole tag is the unit -- no n-gramming
    row = {"tags": '["faceless channel automation"]'}
    assert K.literal_tags_for_video(row) == {"faceless channel automation"}


def test_aggregate_uses_custom_phrase_fn_instead_of_ngrams():
    rows = [
        {"video_id": "v1", "title": "irrelevant", "tags": '["Faceless Automation"]',
         "views": 100, "outlier": 1.0},
        {"video_id": "v2", "title": "irrelevant", "tags": '["faceless automation"]',
         "views": 200, "outlier": 5.0},
    ]
    stats, total, base_rate = K.aggregate(rows, phrase_fn=K.literal_tags_for_video)
    assert total == 2
    # a bare n-gram pass would also produce "faceless" and "automation" as
    # separate phrases -- the custom phrase_fn must suppress that
    assert set(stats.keys()) == {"faceless automation"}
    assert stats["faceless automation"]["videos"] == 2
    assert stats["faceless automation"]["views"] == 300


# ---------------------------------------------- semantic merging (stage 10)

def _unit(x, y):
    import math
    n = math.hypot(x, y)
    return (x / n, y / n)


def test_merge_semantic_synonyms_merges_near_duplicate_phrases():
    stats = {
        "cold shower": {"videos": 5, "views": 500, "view_list": [100] * 5, "outliers": [],
                        "hits": 1, "examples": [], "video_ids": {"v1"}},
        "cold showers": {"videos": 3, "views": 300, "view_list": [100] * 3, "outliers": [],
                         "hits": 0, "examples": [], "video_ids": {"v2"}},
        "unrelated topic": {"videos": 10, "views": 1000, "view_list": [], "outliers": [],
                            "hits": 0, "examples": [], "video_ids": {"v3"}},
    }
    vectors = {
        "cold shower": _unit(1.0, 0.01),
        "cold showers": _unit(1.0, 0.0),
        "unrelated topic": _unit(0.0, 1.0),
    }

    merged = K.merge_semantic_synonyms(stats, vectors, similarity_threshold=0.9)

    assert "unrelated topic" in merged
    assert len(merged) == 2  # the two "cold shower(s)" phrases merged into one
    canon = next(k for k in merged if k != "unrelated topic")
    assert merged[canon]["videos"] == 8  # 5 + 3
    assert merged[canon]["views"] == 800
    assert set(merged[canon]["mergedFrom"]) == {"cold shower", "cold showers"}
    # canonical name defaults to the more frequent member
    assert canon == "cold shower"


def test_merge_semantic_synonyms_leaves_dissimilar_phrases_unmerged():
    stats = {
        "topic a": {"videos": 1, "views": 10, "view_list": [], "outliers": [], "hits": 0,
                   "examples": [], "video_ids": set()},
        "topic b": {"videos": 1, "views": 10, "view_list": [], "outliers": [], "hits": 0,
                   "examples": [], "video_ids": set()},
    }
    vectors = {"topic a": _unit(1.0, 0.0), "topic b": _unit(0.0, 1.0)}

    merged = K.merge_semantic_synonyms(stats, vectors, similarity_threshold=0.9)

    assert set(merged.keys()) == {"topic a", "topic b"}
    assert merged["topic a"]["mergedFrom"] == ["topic a"]


def test_merge_semantic_synonyms_default_mode_is_untouched():
    # aggregate() itself never calls merge_semantic_synonyms -- the default
    # (ngram) path is byte-identical to before this stage existed.
    rows = [{"title": "Cold Shower Challenge", "tags": "[]", "views": 100, "outlier": 1.0,
            "video_id": "v1"}]
    stats, total, base = K.aggregate(rows)
    assert "cold shower" in stats or "cold shower challenge" in stats
    assert "mergedFrom" not in next(iter(stats.values()))


def test_realign_prev_stats_sums_members_under_the_canonical_name():
    prev_stats = {
        "cold shower": {"videos": 2, "views": 200, "view_list": [], "outliers": [], "hits": 0,
                        "examples": [], "video_ids": {"p1"}},
        "cold showers": {"videos": 1, "views": 100, "view_list": [], "outliers": [], "hits": 0,
                         "examples": [], "video_ids": {"p2"}},
    }
    cluster_map = {"cold shower": ["cold shower", "cold showers"]}

    out = K.realign_prev_stats(prev_stats, cluster_map)

    assert out["cold shower"]["videos"] == 3
    assert out["cold shower"]["views"] == 300


def test_realign_prev_stats_skips_a_canonical_name_absent_last_period():
    cluster_map = {"brand new phrase": ["brand new phrase"]}
    out = K.realign_prev_stats({}, cluster_map)
    assert out == {}


def _run_all():
    ns = dict(globals())
    tests = [(name, fn) for name, fn in ns.items() if name.startswith("test_")]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # pragma: no cover
            print(f"ERROR {name}: {e!r}")
    print(f"\n{passed}/{len(tests)} прошло")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)

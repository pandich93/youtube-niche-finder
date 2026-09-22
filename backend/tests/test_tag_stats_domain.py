"""Pure-function tests for domain/tag_stats.py. No DB, no network.

Run: python3 tests/test_tag_stats_domain.py (or pytest tests/test_tag_stats_domain.py)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import domain.tag_stats as TS  # noqa: E402


def _pop(*rows):
    """rows: (video_id, views, outlier)."""
    return [{"video_id": vid, "views": views, "outlier": outlier} for vid, views, outlier in rows]


def _tags(*rows):
    """rows: (video_id, tag)."""
    return [{"video_id": vid, "tag": tag} for vid, tag in rows]


def test_lift_is_relative_to_the_whole_niche_not_just_tagged_videos():
    # 4 videos total, 2 are hits (outlier >= 3.0) -- base rate 50%.
    # Only 2 of the 4 are tagged "fear"; both happen to be hits -- lift should
    # be computed against the 50% base over all 4, not just the 2 tagged ones.
    pop = _pop(
        ("v1", 1000, 4.0),  # hit, tagged
        ("v2", 1000, 3.5),  # hit, tagged
        ("v3", 500, 1.0),   # not a hit, untagged
        ("v4", 500, 0.5),   # not a hit, untagged
    )
    tags = _tags(("v1", "fear"), ("v2", "fear"))

    result = TS.compute(pop, tags, outlier_threshold=3.0)

    assert result["totalVideos"] == 4
    assert result["baseRate"] == 50.0
    fear = next(t for t in result["tags"] if t["tag"] == "fear")
    assert fear["videos"] == 2
    assert fear["hits"] == 2
    assert fear["hitRate"] == 100.0
    assert fear["lift"] == 2.0  # 100% / 50%


def test_untagged_video_still_counts_toward_base_rate():
    pop = _pop(("v1", 100, 5.0), ("v2", 100, 0.0))
    tags = _tags(("v1", "fear"))

    result = TS.compute(pop, tags, outlier_threshold=3.0)

    assert result["totalVideos"] == 2
    assert result["baseRate"] == 50.0  # v2 counts even though it has no tag


def test_tag_on_video_outside_the_population_is_ignored():
    pop = _pop(("v1", 100, 1.0))
    tags = _tags(("v1", "fear"), ("v-not-in-niche", "fear"))

    result = TS.compute(pop, tags)

    fear = next(t for t in result["tags"] if t["tag"] == "fear")
    assert fear["videos"] == 1


def test_min_videos_filters_out_rare_tags():
    pop = _pop(("v1", 100, 1.0), ("v2", 100, 1.0), ("v3", 100, 1.0))
    tags = _tags(("v1", "rare"), ("v2", "common"), ("v3", "common"))

    result = TS.compute(pop, tags, min_videos=2)

    tag_names = {t["tag"] for t in result["tags"]}
    assert tag_names == {"common"}


def test_median_views_and_outlier_are_computed_per_tag():
    pop = _pop(("v1", 100, 1.0), ("v2", 300, 3.0), ("v3", 200, 2.0))
    tags = _tags(("v1", "x"), ("v2", "x"), ("v3", "x"))

    result = TS.compute(pop, tags, min_videos=1)

    x = next(t for t in result["tags"] if t["tag"] == "x")
    assert x["medianViews"] == 200
    assert x["medianOutlier"] == 2.0


def test_empty_population_returns_zero_base_rate_without_dividing_by_zero():
    result = TS.compute([], [])
    assert result == {"tags": [], "totalVideos": 0, "baseRate": 0.0}


def test_results_are_sorted_by_lift_then_video_count_descending():
    pop = _pop(
        ("v1", 100, 5.0), ("v2", 100, 5.0), ("v3", 100, 5.0),
        ("v4", 100, 0.0), ("v5", 100, 0.0),
    )
    tags = _tags(
        ("v1", "high-lift-few"), ("v2", "low-lift-many"), ("v3", "low-lift-many"),
        ("v4", "low-lift-many"), ("v5", "low-lift-many"),
    )

    result = TS.compute(pop, tags, outlier_threshold=3.0, min_videos=1)

    order = [t["tag"] for t in result["tags"]]
    assert order[0] == "high-lift-few"  # lift 100% / 60% > low-lift-many's


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

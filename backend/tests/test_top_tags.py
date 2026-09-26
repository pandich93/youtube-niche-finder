"""Tests for application/discovery.py's top_tags_by_category. sqlite double
for infrastructure.postgres (real discovery.py SQL runs against it
unmodified), no YouTube key.

Run: python3 tests/test_top_tags.py (or pytest tests/test_top_tags.py)
"""
import os
import sys
import sqlite3
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from module_doubles import ModuleDoubles  # noqa: E402

NOW = datetime.now(timezone.utc)
iso = lambda dt: dt.isoformat()

SCHEMA = """
CREATE TABLE channels (
  channel_id TEXT PRIMARY KEY, title TEXT, custom_url TEXT, country TEXT,
  subscriber_count INTEGER, video_count INTEGER, view_count INTEGER
);
CREATE TABLE videos (
  video_id TEXT PRIMARY KEY, channel_id TEXT, title TEXT, tags TEXT,
  published_at TEXT, first_seen_at TEXT, view_count INTEGER, like_count INTEGER,
  comment_count INTEGER, duration_seconds INTEGER, thumbnail TEXT,
  category_id TEXT, region TEXT, default_language TEXT, is_short INTEGER,
  contains_synthetic_media INTEGER, embedding BLOB, updated_at TEXT
);
CREATE TABLE video_niches (video_id TEXT, niche_slug TEXT, PRIMARY KEY(video_id, niche_slug));
CREATE TABLE video_stats_history (video_id TEXT, captured_at TEXT, view_count INTEGER);
"""

RAW = None


class _Conn:
    def execute(self, sql, params=()):
        return RAW.execute(sql, params)

    def commit(self):
        RAW.commit()

    def close(self):
        pass


def reset():
    global RAW
    RAW = sqlite3.connect(":memory:")
    RAW.row_factory = sqlite3.Row
    RAW.executescript(SCHEMA)


reset()

fake_db = types.ModuleType("infrastructure.postgres")
fake_db.get_conn = lambda: _Conn()
fake_db.now_iso = lambda: iso(datetime.now(timezone.utc))

DOUBLES = ModuleDoubles({"infrastructure.postgres": fake_db})
trends, = DOUBLES.load("application.discovery")


def _add_video(vid, category_id, tags, views, ch_view_count=40_000_000,
               ch_video_count=80, days_ago=10):
    ch = f"UC{vid}0000000000000"
    RAW.execute(
        "INSERT OR REPLACE INTO channels (channel_id, title, custom_url,"
        " subscriber_count, video_count, view_count) VALUES (?,?,?,?,?,?)",
        (ch, f"Channel {vid}", f"@{vid}", 50_000, ch_video_count, ch_view_count))
    RAW.execute(
        "INSERT INTO videos (video_id, channel_id, title, tags, published_at,"
        " view_count, duration_seconds, is_short, category_id)"
        " VALUES (?,?,?,?,?,?,?,0,?)",
        (vid, ch, f"video {vid}", __import__("json").dumps(tags),
         iso(NOW - timedelta(days=days_ago)), views, 600, category_id))
    RAW.commit()


def seed_default():
    """category 20 (4 videos): 'speedrun' tagged on 3 -- 2 of those are
    outliers (views >> the 500k channel-average baseline); 'extra' and
    'vlog' each appear on only 1 video. category 27 (2 videos): 'study tips'
    on both, but 2 < the default min_videos=3, so the category has no
    qualifying tag at all. category 10 (3 videos): 'remix' on all 3, none
    outliers."""
    reset()
    _add_video("v1", "20", ["Speedrun", "Extra"], 2_000_000)
    _add_video("v2", "20", ["speedrun"], 1_800_000)
    _add_video("v3", "20", ["speedrun"], 40_000)
    _add_video("v4", "20", ["vlog"], 30_000)
    _add_video("v5", "27", ["study tips"], 2_000_000)
    _add_video("v6", "27", ["study tips"], 1_900_000)
    _add_video("v7", "10", ["remix"], 500_000)
    _add_video("v8", "10", ["remix"], 600_000)
    _add_video("v9", "10", ["remix"], 700_000)


def test_groups_by_category_and_omits_categories_with_no_qualifying_tag():
    seed_default()
    res = trends.top_tags_by_category(period="all")
    ids = [c["categoryId"] for c in res["categories"]]
    assert ids == ["20", "10"]  # sorted by videosAnalysed desc (4, 3); 27 omitted


def test_tag_below_min_videos_is_excluded_from_its_category():
    seed_default()
    res = trends.top_tags_by_category(period="all")
    cat20 = next(c for c in res["categories"] if c["categoryId"] == "20")
    assert cat20["videosAnalysed"] == 4
    assert [t["tag"] for t in cat20["tags"]] == ["speedrun"]
    assert cat20["tags"][0]["videos"] == 3


def test_tag_is_kept_whole_not_split_into_words():
    seed_default()
    res = trends.top_tags_by_category(period="all")
    cat20 = next(c for c in res["categories"] if c["categoryId"] == "20")
    tags = [t["tag"] for t in cat20["tags"]]
    assert "speedrun" in tags
    assert "extra" not in tags and "vlog" not in tags  # below min_videos, not word-split


def test_outlier_lift_reflects_performance_correlation():
    seed_default()
    res = trends.top_tags_by_category(period="all")
    cat20 = next(c for c in res["categories"] if c["categoryId"] == "20")
    speedrun = cat20["tags"][0]
    # hit rate for 'speedrun' (2 outliers / 3 videos) vs category base rate
    # (2 outliers / 4 videos) -> lift = (2/3) / (2/4) = 1.33
    assert speedrun["outlierLift"] == 1.33


def test_examples_reference_real_videos():
    seed_default()
    res = trends.top_tags_by_category(period="all")
    cat10 = next(c for c in res["categories"] if c["categoryId"] == "10")
    remix = cat10["tags"][0]
    assert remix["tag"] == "remix" and remix["videos"] == 3
    example_ids = {e["videoId"] for e in remix["examples"]}
    assert example_ids <= {"v7", "v8", "v9"} and example_ids


def test_lower_min_videos_surfaces_the_previously_omitted_category():
    seed_default()
    res = trends.top_tags_by_category(period="all", min_videos=2)
    ids = {c["categoryId"] for c in res["categories"]}
    assert "27" in ids
    cat27 = next(c for c in res["categories"] if c["categoryId"] == "27")
    assert cat27["tags"][0]["tag"] == "study tips"
    assert cat27["tags"][0]["videos"] == 2


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
    with DOUBLES.active():
        ok = _run_all()
    sys.exit(0 if ok else 1)

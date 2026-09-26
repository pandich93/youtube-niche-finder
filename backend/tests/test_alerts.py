"""Tests for application/alerts.py -- the worker-side event scanner (plan
item 8.9). sqlite double for infrastructure.postgres (discovery.py's real SQL
runs unmodified against it), no YouTube key, no network.

Run: python3 tests/test_alerts.py (or pytest tests/test_alerts.py)
"""
import os
import sqlite3
import sys
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
  subscriber_count INTEGER, video_count INTEGER, view_count INTEGER,
  llm_labels TEXT, llm_labeled_at TEXT
);
CREATE TABLE videos (
  video_id TEXT PRIMARY KEY, channel_id TEXT, title TEXT, tags TEXT,
  published_at TEXT, first_seen_at TEXT, view_count INTEGER, like_count INTEGER,
  comment_count INTEGER, duration_seconds INTEGER, thumbnail TEXT,
  category_id TEXT, region TEXT, default_language TEXT, is_short INTEGER,
  contains_synthetic_media INTEGER, embedding BLOB, updated_at TEXT
);
CREATE TABLE tracked_channels (
  channel_id TEXT PRIMARY KEY, note TEXT, added_at TEXT, last_refreshed_at TEXT,
  active INTEGER DEFAULT 1
);
CREATE TABLE video_stats_history (video_id TEXT, captured_at TEXT, view_count INTEGER);
CREATE TABLE channel_stats_history (channel_id TEXT, captured_at TEXT, subscriber_count INTEGER,
  video_count INTEGER, view_count INTEGER);
CREATE TABLE video_changes (
  video_id TEXT, changed_at TEXT, field TEXT, old_value TEXT, new_value TEXT,
  PRIMARY KEY (video_id, changed_at, field)
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ref_id TEXT, payload TEXT,
  created_at TEXT, seen_at TEXT
);
"""

RAW = None
CH_AVG = "UCavgchannel0000000001"   # baseline: 40M views / 80 videos = 500k avg
CH_OUTLIER = "UCoutlierchannel0001"
CH_ACCEL = "UCaccelchannel00001"
CH_TITLE = "UCtitlechannel00001"
CH_SILENCE = "UCsilencechannel001"


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

# Real `infrastructure` package imports fine on its own -- only the leaf
# `infrastructure.postgres` submodule is replaced, exactly like
# tests/test_inspection.py and tests/test_metadata_review.py do. The double
# lives in a private module world (tests/module_doubles.py), so it never
# leaks into the Postgres-backed files that share this pytest process.
fake_db = types.ModuleType("infrastructure.postgres")
fake_db.get_conn = lambda: _Conn()
fake_db.now_iso = lambda: iso(datetime.now(timezone.utc))

DOUBLES = ModuleDoubles({"infrastructure.postgres": fake_db})
AL, = DOUBLES.load("application.alerts")


def _channel(ch, subs=50_000, videos=80, views=40_000_000):
    RAW.execute(
        "INSERT OR REPLACE INTO channels (channel_id, title, custom_url, subscriber_count,"
        " video_count, view_count) VALUES (?,?,?,?,?,?)",
        (ch, ch, "@" + ch.lower(), subs, videos, views))


def _track(ch):
    RAW.execute(
        "INSERT OR REPLACE INTO tracked_channels (channel_id, added_at, active) "
        "VALUES (?,?,1)", (ch, iso(NOW)))


def _video(vid, ch, title, published_days_ago, views, duration=600):
    RAW.execute(
        "INSERT INTO videos (video_id, channel_id, title, tags, published_at,"
        " view_count, duration_seconds, is_short) VALUES (?,?,?,?,?,?,?,0)",
        (vid, ch, title, "[]", iso(NOW - timedelta(days=published_days_ago)), views, duration))


def seed():
    """Four tracked channels, each set up to trip exactly one detector, plus
    a shared-average baseline channel so the outlier/nexlev fallback has a
    real, non-huge comparison point."""
    for ch in (CH_AVG, CH_OUTLIER, CH_ACCEL, CH_TITLE, CH_SILENCE):
        _channel(ch)
        _track(ch)

    # 1. outlier: one video, 4x the channel-average views -- with no prior
    #    upload on this channel, the per-history baseline is None, so
    #    discovery.py's Nexlev channel-average fallback decides (see the
    #    metadata_review fixture notes for why this is deterministic).
    _video("voutlier1", CH_OUTLIER, "Огромный выброс", 5, 2_000_000)

    # 2. acceleration: view history that clearly speeds up in the last 24h
    #    vs. the 24h before that (worked out by hand against
    #    domain.metrics.vph_from_history/acceleration).
    _video("vaccel1", CH_ACCEL, "Ускоряется", 3, 2_100)
    for days_ago, views in ((3, 1000), (2, 1100), (1, 1300), (0, 2100)):
        RAW.execute("INSERT INTO video_stats_history (video_id, captured_at, view_count) "
                    "VALUES (?,?,?)", ("vaccel1", iso(NOW - timedelta(days=days_ago)), views))

    # 3. title change: one retitling event to pick up from video_changes.
    _video("vtitle1", CH_TITLE, "Новое название", 4, 10_000)
    RAW.execute("INSERT INTO video_changes (video_id, changed_at, field, old_value, new_value) "
               "VALUES (?,?,?,?,?)",
               ("vtitle1", iso(NOW - timedelta(days=1)), "title", "Старое название", "Новое название"))

    # 4. silence break: an old upload, then a new one after a 60-day gap.
    _video("vsilence_old", CH_SILENCE, "Старое видео", 70, 5_000)
    _video("vsilence_new", CH_SILENCE, "Вернулся после паузы", 0, 5_000)

    RAW.commit()


def test_scan_emits_exactly_one_event_of_each_kind():
    reset(); seed()
    res = AL.scan(period="180d")
    assert res["emitted"] == {"outlier": 1, "acceleration": 1, "title_change": 1, "silence_break": 1}, res


def test_scan_again_produces_no_duplicates():
    reset(); seed()
    AL.scan(period="180d")
    res2 = AL.scan(period="180d")
    assert res2["emitted"] == {"outlier": 0, "acceleration": 0, "title_change": 0, "silence_break": 0}, res2
    # exactly 4 rows total in the events table, not 8
    events = AL.list_events(limit=100)
    assert len(events) == 4


def test_scan_with_no_tracked_channels_is_a_noop():
    reset()
    res = AL.scan(period="180d")
    assert res["emitted"] == {"outlier": 0, "acceleration": 0, "title_change": 0, "silence_break": 0}
    assert "hint" in res


def test_list_events_and_mark_seen_roundtrip():
    reset(); seed()
    AL.scan(period="180d")
    assert AL.unseen_count() == 4
    unseen = AL.list_events(unseen_only=True)
    assert len(unseen) == 4
    AL.mark_seen(ids=[unseen[0]["id"]])
    assert AL.unseen_count() == 3
    AL.mark_seen(all_unseen=True)
    assert AL.unseen_count() == 0


def test_events_carry_readable_payload():
    reset(); seed()
    AL.scan(period="180d")
    by_kind = {e["kind"]: e for e in AL.list_events(limit=100)}
    assert by_kind["outlier"]["payload"]["videoId"] == "voutlier1"
    assert by_kind["acceleration"]["payload"]["videoId"] == "vaccel1"
    assert by_kind["acceleration"]["payload"]["acceleration"] >= 2.0
    assert by_kind["title_change"]["payload"]["oldTitle"] == "Старое название"
    assert by_kind["silence_break"]["payload"]["videoId"] == "vsilence_new"
    assert by_kind["silence_break"]["payload"]["gapDays"] >= 14


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

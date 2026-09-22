"""Tests for infrastructure/youtube/rss.py -- pure XML parsing, no network,
no key. Run: python3 tests/test_rss.py (or pytest tests/test_rss.py)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import infrastructure.youtube.rss as rss  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "channel_feed.xml")


def _read_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


def test_parse_feed_extracts_entries_newest_first():
    entries = rss.parse_feed(_read_fixture())
    assert len(entries) == 3
    assert entries[0]["videoId"] == "vidNEW222222"
    assert entries[0]["title"] == "Самое новое видео"
    assert entries[0]["channelId"] == "UCabc0000000000000000001"
    assert entries[-1]["videoId"] == "vidOLD000000"


def test_parse_feed_empty_or_garbage_returns_empty_list():
    assert rss.parse_feed("") == []
    assert rss.parse_feed("not xml at all") == []
    assert rss.parse_feed("<feed></feed>") == []


def test_new_video_ids_filters_known_and_orders_oldest_first(monkeypatch):
    monkeypatch.setattr(rss, "fetch_channel_feed", lambda channel_id, timeout=10.0: _read_fixture())
    fresh = rss.new_video_ids("UCabc0000000000000000001", known_ids={"vidOLD000000"})
    # oldest-of-the-fresh-ones first, so a caller storing sequentially keeps order
    assert fresh == ["vidNEW111111", "vidNEW222222"]


def test_new_video_ids_all_known_returns_empty(monkeypatch):
    monkeypatch.setattr(rss, "fetch_channel_feed", lambda channel_id, timeout=10.0: _read_fixture())
    fresh = rss.new_video_ids("UCabc0000000000000000001",
                              known_ids={"vidOLD000000", "vidNEW111111", "vidNEW222222"})
    assert fresh == []


def _run_all():
    ns = dict(globals())
    tests = [(name, fn) for name, fn in ns.items() if name.startswith("test_")]
    passed = 0
    for name, fn in tests:
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(_FakeMonkeypatch())
            else:
                fn()
            print(f"  ok  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # pragma: no cover
            print(f"ERROR {name}: {e!r}")
    print(f"\n{passed}/{len(tests)} прошло")
    return passed == len(tests)


class _FakeMonkeypatch:
    def setattr(self, obj, name, value):
        setattr(obj, name, value)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)

"""Tests for application/library.py (swipe file, plan item 8.6).

sqlite double, no key, no Postgres -- same pattern as test_inspection.py.
Run: python3 tests/test_library.py (or pytest tests/test_library.py)
"""
import os
import sys
import sqlite3
import types
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

SCHEMA = """
CREATE TABLE saved_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT, ref_id TEXT, folder TEXT, note TEXT, payload TEXT, created_at TEXT
);
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
fake_db.now_iso = lambda: datetime.now(timezone.utc).isoformat()
sys.modules["infrastructure.postgres"] = fake_db
sys.modules.setdefault("infrastructure", types.ModuleType("infrastructure"))

import application.library as L  # noqa: E402


def test_save_and_list_roundtrip():
    reset()
    saved = L.save_item("video", "vid1", payload={"views": 100}, note="занятная тема")
    assert saved["id"] and saved["kind"] == "video" and saved["folder"] == "default"
    items = L.list_items()
    assert len(items) == 1
    assert items[0]["refId"] == "vid1"
    assert items[0]["payload"] == {"views": 100}
    assert items[0]["note"] == "занятная тема"


def test_save_rejects_bad_kind():
    reset()
    try:
        L.save_item("banana", "x")
        assert False, "should have raised"
    except ValueError:
        pass


def test_save_requires_ref_id():
    reset()
    try:
        L.save_item("video", "")
        assert False, "should have raised"
    except ValueError:
        pass


def test_filter_by_kind_and_folder():
    reset()
    L.save_item("video", "v1", folder="titles-to-steal")
    L.save_item("channel", "c1", folder="titles-to-steal")
    L.save_item("video", "v2", folder="default")
    assert len(L.list_items(kind="video")) == 2
    assert len(L.list_items(folder="titles-to-steal")) == 2
    assert len(L.list_items(kind="channel", folder="titles-to-steal")) == 1


def test_list_folders_counts():
    reset()
    L.save_item("video", "v1", folder="a")
    L.save_item("video", "v2", folder="a")
    L.save_item("video", "v3", folder="b")
    folders = {f["folder"]: f["count"] for f in L.list_folders()}
    assert folders == {"a": 2, "b": 1}


def test_delete_removes_item():
    reset()
    saved = L.save_item("video", "v1")
    assert len(L.list_items()) == 1
    L.delete_item(saved["id"])
    assert len(L.list_items()) == 0


def test_is_saved():
    reset()
    assert L.is_saved("video", "v1") is False
    L.save_item("video", "v1")
    assert L.is_saved("video", "v1") is True
    assert L.is_saved("channel", "v1") is False


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

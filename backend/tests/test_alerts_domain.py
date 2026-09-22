"""Pure-function tests for domain/alerts.py -- no DB, no mocking (mirrors
tests/test_metadata_domain.py). Run: python3 tests/test_alerts_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import alerts as A  # noqa: E402


def test_outlier_detected_at_or_above_threshold():
    rows = [
        {"video_id": "v1", "title": "t1", "channel_id": "c1", "view_count": 100, "outlier_score": 3.0},
        {"video_id": "v2", "title": "t2", "channel_id": "c1", "view_count": 50, "outlier_score": 2.9},
        {"video_id": "v3", "title": "t3", "channel_id": "c1", "view_count": 10, "outlier_score": None},
    ]
    out = A.detect_outliers(rows, threshold=3.0)
    assert [e["refId"] for e in out] == ["v1"]
    assert out[0]["kind"] == "outlier"
    assert out[0]["payload"]["outlierScore"] == 3.0


def test_acceleration_ref_folds_in_snapshot_time_so_it_can_repeat():
    rows = [
        {"video_id": "v1", "title": "t1", "channel_id": "c1", "acceleration": 2.5,
         "vph24h": 500, "captured_at": "2026-08-01T00:00:00Z"},
        {"video_id": "v1", "title": "t1", "channel_id": "c1", "acceleration": 2.1,
         "vph24h": 300, "captured_at": "2026-08-02T00:00:00Z"},
        {"video_id": "v2", "title": "t2", "channel_id": "c1", "acceleration": 1.0,
         "vph24h": 100, "captured_at": "2026-08-01T00:00:00Z"},
    ]
    out = A.detect_acceleration(rows, threshold=2.0)
    refs = {e["refId"] for e in out}
    assert refs == {"v1:2026-08-01T00:00:00Z", "v1:2026-08-02T00:00:00Z"}
    assert all(e["kind"] == "acceleration" for e in out)


def test_title_change_one_candidate_per_row_ref_folds_in_changed_at():
    rows = [
        {"video_id": "v1", "channel_id": "c1", "changed_at": "2026-08-01T00:00:00Z",
         "old_value": "Старое", "new_value": "Новое"},
        {"video_id": "v1", "channel_id": "c1", "changed_at": "2026-08-05T00:00:00Z",
         "old_value": "Новое", "new_value": "Ещё новее"},
    ]
    out = A.detect_title_changes(rows)
    assert len(out) == 2
    assert out[0]["refId"] == "v1:2026-08-01T00:00:00Z"
    assert out[0]["payload"]["oldTitle"] == "Старое"
    assert out[1]["payload"]["newTitle"] == "Ещё новее"


def test_silence_break_needs_a_real_gap_after_a_prior_upload():
    uploads = {
        "c1": [
            ("2026-01-01T00:00:00Z", "old1", "Первое"),
            ("2026-01-03T00:00:00Z", "old2", "Второе -- всего 2 дня, не тишина"),
            ("2026-02-01T00:00:00Z", "back1", "Вернулся после паузы"),
        ],
    }
    out = A.detect_silence_breaks(uploads, silence_days=14)
    assert len(out) == 1
    assert out[0]["refId"] == "back1"
    assert out[0]["kind"] == "silence_break"
    assert out[0]["payload"]["gapDays"] >= 14


def test_silence_break_first_upload_ever_is_not_an_event():
    uploads = {"c1": [("2026-01-01T00:00:00Z", "only1", "Единственное видео")]}
    out = A.detect_silence_breaks(uploads, silence_days=14)
    assert out == []


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

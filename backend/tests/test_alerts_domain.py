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


T0 = "2026-09-01T00:00:00+00:00"
T_PLUS_2H = "2026-09-01T02:00:00+00:00"
T_PLUS_7H = "2026-09-01T07:00:00+00:00"


def test_gone_first_miss_is_only_a_candidate():
    st = A.gone_transition(None, missing=True, now_iso=T0)
    assert st == {"first_missing_at": T0, "last_missing_at": T0,
                  "miss_count": 1, "confirmed_at": None}


def test_gone_second_miss_too_soon_is_not_confirmed():
    st = A.gone_transition(None, missing=True, now_iso=T0)
    st = A.gone_transition(st, missing=True, now_iso=T_PLUS_2H)
    assert st["miss_count"] == 2
    assert st["confirmed_at"] is None


def test_gone_second_miss_after_min_hours_is_confirmed():
    st = A.gone_transition(None, missing=True, now_iso=T0)
    st = A.gone_transition(st, missing=True, now_iso=T_PLUS_7H)
    assert st["confirmed_at"] == T_PLUS_7H
    assert st["first_missing_at"] == T0


def test_gone_long_wait_after_a_single_miss_still_needs_a_second_miss():
    # One miss, however old, is one observation -- a flaky call a week ago
    # must not confirm anything on its own.
    st = A.gone_transition(None, missing=True, now_iso=T0)
    assert st["confirmed_at"] is None


def test_gone_confirmation_is_sticky_on_later_misses():
    st = A.gone_transition(None, missing=True, now_iso=T0)
    st = A.gone_transition(st, missing=True, now_iso=T_PLUS_7H)
    st = A.gone_transition(st, missing=True, now_iso="2026-09-02T00:00:00+00:00")
    assert st["confirmed_at"] == T_PLUS_7H
    assert st["miss_count"] == 3


def test_gone_reappearing_clears_the_state():
    st = A.gone_transition(None, missing=True, now_iso=T0)
    assert A.gone_transition(st, missing=False, now_iso=T_PLUS_2H) is None
    assert A.gone_transition(None, missing=False, now_iso=T0) is None


def test_detect_gone_ref_folds_in_first_missing_at_so_a_second_disappearance_alerts_again():
    rows = [
        {"kind": "channel", "ref_id": "UCgone", "first_missing_at": T0,
         "confirmed_at": T_PLUS_7H, "title": "Gone Channel", "subscriber_count": 12000,
         "view_count": 3_000_000, "video_count": 40, "last_seen_at": "2026-08-31T00:00:00+00:00"},
        {"kind": "video", "ref_id": "vgone", "first_missing_at": T0,
         "confirmed_at": T_PLUS_7H, "title": "Gone Video", "channel_id": "UCx",
         "view_count": 90_000, "outlier_score": 5.5},
    ]
    out = A.detect_gone(rows)
    assert [(e["kind"], e["refId"]) for e in out] == [
        ("channel_gone", f"UCgone:{T0}"), ("video_gone", f"vgone:{T0}")]
    ch, vid = out[0]["payload"], out[1]["payload"]
    assert ch["channelId"] == "UCgone" and ch["subscribers"] == 12000
    assert ch["goneSince"] == T0 and ch["lastSeenAt"] == "2026-08-31T00:00:00+00:00"
    assert vid["videoId"] == "vgone" and vid["channelId"] == "UCx"
    assert vid["outlierScore"] == 5.5 and vid["views"] == 90_000


def test_detect_gone_skips_unconfirmed_rows():
    rows = [{"kind": "channel", "ref_id": "UCmaybe", "first_missing_at": T0,
             "confirmed_at": None}]
    assert A.detect_gone(rows) == []


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


# ------------------------------------------------- plan 17: milestones

def test_milestone_fires_when_subscribers_cross_a_round_number():
    evs = A.detect_milestones([{"channel_id": "UCa", "title": "A", "previous": 980,
                                "current": 1_004}])
    assert evs == [{"kind": "milestone", "refId": "UCa:1000",
                    "payload": {"channelId": "UCa", "title": "A", "milestone": 1_000,
                                "subscribers": 1_004}}]


def test_milestone_needs_two_snapshots_and_a_crossing():
    assert A.detect_milestones([{"channel_id": "UCa", "title": "A", "previous": None,
                                 "current": 5_000}]) == []
    assert A.detect_milestones([{"channel_id": "UCa", "title": "A", "previous": 1_100,
                                 "current": 1_200}]) == []


def test_milestone_needs_snapshots_close_in_time():
    old = {"channel_id": "UCa", "title": "A", "previous": 900, "current": 1_050,
           "previousAt": "2026-05-01T00:00:00+00:00", "currentAt": "2026-10-01T00:00:00+00:00"}
    assert A.detect_milestones([old]) == []
    recent = {**old, "previousAt": "2026-09-30T00:00:00+00:00"}
    assert [e["refId"] for e in A.detect_milestones([recent])] == ["UCa:1000"]

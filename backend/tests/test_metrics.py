"""Pure-function tests for domain/metrics.py:period_baseline_median (stage 14).
No DB, no network.

Run: python3 tests/test_metrics.py (or pytest tests/test_metrics.py)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import domain.metrics as M  # noqa: E402

TARGET_DATE = "2026-06-15T00:00:00Z"


def _v(days_offset, views, is_short=False):
    from datetime import datetime, timedelta, timezone
    dt = datetime(2026, 6, 15, tzinfo=timezone.utc) + timedelta(days=days_offset)
    return {"published_at": dt.isoformat(), "view_count": views, "is_short": is_short}


def test_uses_window_median_when_enough_videos_in_window():
    siblings = [_v(-10, 100), _v(-5, 200), _v(0, 300), _v(5, 400), _v(9, 500),
               _v(-100, 999999)]  # far outside window, must not affect the result
    out = M.period_baseline_median(siblings, TARGET_DATE, target_is_short=False,
                                   window_days=15, min_videos=5)
    assert out == 300.0


def test_boundary_exactly_window_days_is_included():
    siblings = [_v(-15, 100), _v(-14, 200), _v(0, 300), _v(14, 400), _v(15, 500)]
    out = M.period_baseline_median(siblings, TARGET_DATE, target_is_short=False,
                                   window_days=15, min_videos=5)
    assert out == 300.0  # all 5 inside [-15, +15], including both boundary points


def test_one_day_beyond_boundary_is_excluded_and_falls_back_if_too_few_remain():
    siblings = [_v(-16, 100), _v(-15, 200), _v(0, 300), _v(15, 400)]
    # only 3 of 4 are within +/-15d -- below min_videos=5, so falls back to the
    # whole-channel (same-format) median of all 4: sorted [100,200,300,400] -> 250
    out = M.period_baseline_median(siblings, TARGET_DATE, target_is_short=False,
                                   window_days=15, min_videos=5)
    assert out == 250.0


def test_falls_back_to_whole_channel_median_when_window_has_too_few():
    siblings = [_v(-1, 100), _v(1, 200)]  # only 2 in-window, min_videos=5
    out = M.period_baseline_median(siblings, TARGET_DATE, target_is_short=False,
                                   window_days=15, min_videos=5)
    assert out == 150.0  # whole-channel median of the 2 available


def test_shorts_and_long_form_baselines_never_mix():
    siblings = [
        _v(-1, 100, is_short=True), _v(-2, 200, is_short=True),
        _v(-3, 300, is_short=True), _v(-4, 400, is_short=True),
        _v(-5, 500, is_short=True),
        _v(0, 999999, is_short=False),  # would blow up a Shorts baseline if mixed in
    ]
    out = M.period_baseline_median(siblings, TARGET_DATE, target_is_short=True,
                                   window_days=15, min_videos=5)
    assert out == 300.0


def test_none_when_no_video_of_that_format_exists():
    siblings = [_v(0, 100, is_short=False)]
    out = M.period_baseline_median(siblings, TARGET_DATE, target_is_short=True,
                                   window_days=15, min_videos=5)
    assert out is None


def test_empty_channel_videos_returns_none():
    assert M.period_baseline_median([], TARGET_DATE, target_is_short=False) is None


def test_unparseable_target_date_falls_back_to_whole_channel_median():
    siblings = [_v(-1, 100), _v(1, 200)]
    out = M.period_baseline_median(siblings, "not-a-date", target_is_short=False,
                                   window_days=15, min_videos=5)
    assert out == 150.0


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


# ------------------------------------------- plan 18: view counting changed
# YouTube counts a public view from the first frame since 2026-08-24, so a
# snapshot pair across that date mixes two counting rules.

def _at(day, hour=0):
    from datetime import datetime, timezone
    return datetime(2026, 8, day, hour, tzinfo=timezone.utc)


def test_vph_ignores_snapshots_from_before_the_view_count_change():
    rows = [(_at(23, 0), 1000), (_at(24, 12), 50000), (_at(25, 12), 52400)]
    # without the epoch cut the 24h window would reach back to the 23rd;
    # only the two snapshots after the change are paired: 2400 views / 24h
    assert M.vph_from_history(rows, 48) == 100.0


def test_vph_needs_two_snapshots_after_the_change():
    rows = [(_at(22), 1000), (_at(23), 2000), (_at(25), 9000)]
    assert M.vph_from_history(rows, 24) is None


def test_vph_of_a_history_entirely_before_the_change_is_unchanged():
    rows = [(_at(20), 1000), (_at(21), 2200), (_at(22), 4600)]
    assert M.vph_from_history(rows, 24) == 100.0


def test_vph_accepts_naive_timestamps_as_utc():
    from datetime import datetime
    rows = [(datetime(2026, 8, 23), 1000), (datetime(2026, 8, 25), 2000),
            (datetime(2026, 8, 26), 4400)]
    assert M.vph_from_history(rows, 24) == 100.0


def test_crosses_view_count_change():
    assert M.crosses_view_count_change([_at(20), _at(25)]) is True
    assert M.crosses_view_count_change([_at(20), _at(23)]) is False
    assert M.crosses_view_count_change([_at(24), _at(28)]) is False
    assert M.crosses_view_count_change([]) is False

"""Pure-function tests for domain/packaging.py (plan 05) -- no DB, no network.
Run: python3 tests/test_packaging_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import packaging as PK  # noqa: E402


def test_hamming_counts_differing_bits():
    assert PK.hamming("0000000000000000", "0000000000000000") == 0
    assert PK.hamming("0000000000000000", "000000000000000f") == 4
    assert PK.hamming("ffffffffffffffff", "0000000000000000") == 64


def test_hamming_of_missing_or_garbage_hash_is_none():
    assert PK.hamming(None, "0000000000000000") is None
    assert PK.hamming("zz", "0000000000000000") is None


def test_first_fingerprint_is_a_baseline_not_a_change():
    assert PK.thumbnail_changed(None, "0123456789abcdef") is False


def test_small_jpeg_noise_is_not_a_change_but_a_new_image_is():
    old = "0000000000000000"
    assert PK.thumbnail_changed(old, "0000000000000007") is False            # 3 bits
    assert PK.thumbnail_changed(old, "00000000000fffff") is True             # 20 bits
    assert PK.thumbnail_changed(old, "000000000000ffff", threshold=16) is False


def _h(hours, views):
    return (f"2026-09-10T{hours:02d}:00:00+00:00", views)


def test_change_effect_compares_views_per_hour_before_and_after():
    history = [_h(0, 1000), _h(10, 2000), _h(12, 2200), _h(22, 6200)]
    eff = PK.change_effect(history, "2026-09-10T12:00:00+00:00", window_hours=48)
    assert eff["enoughData"] is True
    assert eff["vphBefore"] == 100.0      # 1000 -> 2200 over 12h
    assert eff["vphAfter"] == 400.0       # 2200 -> 6200 over 10h
    assert eff["ratio"] == 4.0


def test_change_effect_needs_two_points_on_each_side():
    history = [_h(0, 1000), _h(12, 2200)]            # nothing after the change
    eff = PK.change_effect(history, "2026-09-10T12:00:00+00:00")
    assert eff["enoughData"] is False
    assert eff["vphAfter"] is None and eff["ratio"] is None


def test_change_effect_ignores_points_outside_the_window():
    history = [_h(0, 1000), _h(10, 1100), _h(12, 1200), _h(14, 1400)]
    eff = PK.change_effect(history, "2026-09-10T12:00:00+00:00", window_hours=1)
    assert eff["enoughData"] is False


def test_change_effect_zero_before_rate_has_no_ratio():
    history = [_h(0, 1000), _h(12, 1000), _h(20, 1800)]
    eff = PK.change_effect(history, "2026-09-10T12:00:00+00:00")
    assert eff["vphBefore"] == 0.0 and eff["vphAfter"] == 100.0
    assert eff["ratio"] is None


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

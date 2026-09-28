"""Pure-function tests for the RPM range in domain/metrics.py (plan 06): one
RPM number per niche implied a precision nobody has -- published estimates
for the same niche disagree by up to 7x -- so every estimate now also comes
as low / mid / high. No DB, no network.
Run: python3 tests/test_rpm_range.py (or pytest tests/test_rpm_range.py)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import domain.metrics as M  # noqa: E402


def test_mid_is_the_existing_effective_rpm_so_filters_do_not_move():
    for niche in ("tech", "finance", "gaming", "default"):
        r = M.rpm_range(niche)
        assert r["mid"] == M.rpm_effective(niche), niche


def test_range_is_mid_divided_and_multiplied_by_the_spread():
    r = M.rpm_range("tech")                       # 12.0 * 0.70 = 8.4
    assert r["mid"] == 8.4
    assert r["low"] == round(8.4 / M.RPM_SPREAD, 2)
    assert r["high"] == round(8.4 * M.RPM_SPREAD, 2)
    assert r["low"] < r["mid"] < r["high"]


def test_range_says_how_sure_it_is_and_why():
    r = M.rpm_range("education")
    assert r["confidence"] == "low"
    assert "7x" in r["basis"]


def test_unknown_or_empty_niche_uses_the_default_bucket():
    assert M.rpm_range("no-such-niche") == M.rpm_range("default")
    assert M.rpm_range(None) == M.rpm_range("default")


def test_revenue_niche_keeps_old_fields_and_adds_the_monthly_range():
    rv = M.revenue_niche(100_000, "tech")
    assert rv["rpm_base"] == 12.0 and rv["rpm_effective"] == 8.4
    assert rv["monthly_usd"] == 840.0
    assert rv["monthly_usd_low"] == 420.0 and rv["monthly_usd_high"] == 1680.0
    assert rv["rpm_range"] == M.rpm_range("tech")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)

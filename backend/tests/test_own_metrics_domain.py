"""Tests for domain/own_metrics.py (plan 14): real RPM, calibration against
our estimated range, and your videos against a niche. Pure; no DB, no network.
Run with pytest, or directly: python3 tests/test_own_metrics_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import own_metrics as OM  # noqa: E402

RNG = {"low": 1.0, "mid": 2.0, "high": 4.0}


def test_rpm_is_revenue_per_thousand_views():
    assert OM.rpm(25.0, 10_000) == 2.5
    assert OM.rpm(None, 10_000) is None and OM.rpm(5.0, 0) is None


def test_calibration_places_the_real_rpm_against_the_range():
    assert OM.calibrate(0.5, RNG)["position"] == "below"
    assert OM.calibrate(3.0, RNG) == {"realRpm": 3.0, "estimate": RNG, "position": "inside",
                                       "vsMid": 1.5}
    assert OM.calibrate(9.0, RNG)["position"] == "above"
    assert OM.calibrate(None, RNG)["position"] == "unknown"


def test_versus_niche_compares_medians_and_retention():
    own = [{"views": 100, "averageViewPercentage": 40.0}, {"views": 300, "averageViewPercentage": 50.0},
           {"views": 500, "averageViewPercentage": None}]
    out = OM.versus_niche(own, [100, 200, 400])
    assert out["ownMedianViews"] == 300 and out["nicheMedianViews"] == 200
    assert out["ratio"] == 1.5 and out["shareAboveNicheMedian"] == round(2 / 3, 2)
    assert out["ownMedianRetentionPct"] == 45.0


def test_versus_niche_without_data_is_empty_not_a_crash():
    out = OM.versus_niche([], [])
    assert out["ratio"] is None and out["ownMedianViews"] is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

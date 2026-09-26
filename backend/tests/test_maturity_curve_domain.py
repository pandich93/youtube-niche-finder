"""Tests for domain/metrics.py's maturity-curve fitting and the active-curve
switch. Pure functions over synthetic snapshot histories -- no database.
Run with pytest, or directly: python3 tests/test_maturity_curve_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pytest                       # noqa: E402

from domain import metrics as M      # noqa: E402

DAILY = [0.5] + list(range(1, 32))   # a snapshot a day from ~12h after publication


def _history(ages=DAILY, scale=1000.0, curve=None, noise=0.0, seed=0):
    """Views that follow `curve` (the shipped one by default) exactly, plus
    an optional deterministic +-noise share."""
    out = []
    for i, a in enumerate(ages):
        wobble = 1 + (noise if (i + seed) % 2 else -noise)
        out.append((a, scale * M.maturity(a, curve) * wobble))
    return out


def _corpus(n=40, **kw):
    return {f"v{i}": _history(scale=1000.0 * (i + 1), seed=i, **kw) for i in range(n)}


@pytest.fixture(autouse=True)
def _manual_curve():
    M.set_maturity_curve(None)
    yield
    M.set_maturity_curve(None)


# ------------------------------------------------------------------ fitting

def test_recovers_the_curve_the_views_were_generated_from():
    out = M.fit_maturity_curve(_corpus())
    assert out["calibrated"] is True
    assert out["videosUsed"] == 40
    for age in (1, 2, 3, 5, 7, 14, 21):
        assert out["curve"][age] == pytest.approx(M.maturity(age), abs=0.01), age
    assert out["curve"][30] == 1.0
    # day 0 is never observed: the shipped floor, capped at the fitted day 1
    assert out["curve"][0] == min(M.MATURITY_CURVE[0], out["curve"][1])


def test_snapshots_between_whole_days_are_interpolated():
    # snapshots every ~1.3 days never land on a whole day
    ages = [0.4 + 1.3 * k for k in range(25)]
    corpus = {f"v{i}": _history(ages=ages, scale=500.0 * (i + 1)) for i in range(40)}
    out = M.fit_maturity_curve(corpus)
    assert out["calibrated"] is True
    for age in (1, 3, 7, 21):
        # linear interpolation over a curve that is itself piecewise linear
        assert out["curve"][age] == pytest.approx(M.maturity(age), abs=0.02), age


def test_only_videos_watched_from_publication_count():
    late = {f"late{i}": _history(ages=list(range(3, 32))) for i in range(40)}
    out = M.fit_maturity_curve(late)
    assert out["calibrated"] is False
    assert out["videosUsed"] == 0
    assert "curve" not in out
    assert out["reason"]


def test_videos_without_a_28_day_snapshot_are_skipped():
    young = {f"y{i}": _history(ages=[0.5] + list(range(1, 22))) for i in range(40)}
    assert M.fit_maturity_curve(young)["videosUsed"] == 0


def test_too_few_videos_is_not_calibrated_and_says_how_many():
    out = M.fit_maturity_curve(_corpus(n=12), min_videos=30, min_samples_per_age=5)
    assert out["calibrated"] is False
    assert out["videosUsed"] == 12
    assert "12" in out["reason"] and "30" in out["reason"]


def test_days_inside_a_long_gap_are_left_out_and_reported():
    corpus = _corpus(n=40)
    # 35 videos have no snapshot between day 1 and day 5 (worker was down):
    # a 4-day gap is too long to interpolate across
    for i, vid in enumerate(corpus):
        if i >= 5:
            corpus[vid] = [p for p in corpus[vid] if not 1 < p[0] < 5]
    out = M.fit_maturity_curve(corpus)
    assert out["calibrated"] is False
    assert out["missingAges"] == [2, 3]
    assert out["samplesPerAge"][2] == 5
    assert "curve" not in out


def test_noisy_curve_is_made_monotonic_and_capped():
    out = M.fit_maturity_curve(_corpus(noise=0.08), min_samples_per_age=10)
    curve = out["curve"]
    values = [curve[a] for a in sorted(curve)]
    assert values == sorted(values)
    assert max(values) == 1.0 and curve[30] == 1.0


def test_views_past_day_30_are_interpolated_back_to_day_30():
    # every video doubles its views between day 30 and day 31; the base must
    # be the day-30 value, so day 30 itself is exactly 1.0 before capping
    corpus = {}
    for i in range(40):
        h = _history(scale=1000.0)
        h = [(a, v * (2 if a > 30 else 1)) for a, v in h]
        corpus[f"v{i}"] = h
    out = M.fit_maturity_curve(corpus)
    assert out["curve"][21] == pytest.approx(M.maturity(21), abs=0.01)


# ------------------------------------------------------------ active curve

def test_set_maturity_curve_switches_what_maturity_uses():
    manual = M.maturity(3)
    M.set_maturity_curve({0: 0.5, 3: 0.9, 30: 1.0})
    assert M.maturity(3) == pytest.approx(0.9)
    assert M.projected_30d_views(900, 3) == pytest.approx(1000)
    M.set_maturity_curve(None)
    assert M.maturity(3) == manual


def test_explicit_curve_argument_still_wins():
    M.set_maturity_curve({0: 0.5, 3: 0.9, 30: 1.0})
    assert M.maturity(3, M.MATURITY_CURVE) == pytest.approx(0.52)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

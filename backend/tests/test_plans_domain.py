"""Tariff catalog and rules (domain/plans.py): pure, no database.
Run with pytest, or directly: python3 tests/test_plans_domain.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import plans as DP  # noqa: E402

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def test_missing_keys_take_catalog_defaults(monkeypatch):
    monkeypatch.setenv("NF_USER_DAILY_UNITS", "500")
    monkeypatch.setenv("NF_USER_DAILY_SEARCH_CALLS", "0")       # 0 in .env = no limit
    monkeypatch.delenv("NF_USER_DAILY_LLM_USD", raising=False)
    monkeypatch.delenv("NF_USER_MAX_OWN_CHANNELS", raising=False)
    lim = DP.resolve_limits({"tracked_channels": 10})
    assert lim["yt_units"] == 500
    assert lim["search_calls"] is None
    assert lim["llm_usd"] == 0.25
    assert lim["tracked_channels"] == 10 and lim["own_channels"] == 5
    assert lim["saved_items"] is None
    assert set(lim) == set(DP.LIMITS)


def test_explicit_values_win_over_defaults(monkeypatch):
    monkeypatch.setenv("NF_USER_DAILY_UNITS", "500")
    assert DP.resolve_limits({"yt_units": None})["yt_units"] is None
    assert DP.resolve_limits({"yt_units": 0})["yt_units"] == 0


def test_features_default_on():
    f = DP.resolve_features({"export": False})
    assert f["export"] is False and f["llm"] is True
    assert set(f) == set(DP.FEATURES)


def test_validate_reports_each_bad_field():
    errors = DP.validate({"yt_units": -1, "topics": 2.5, "nope": 1, "drafts": "10",
                          "llm_usd": 0.5, "saved_items": None},
                         {"llm": "yes", "ghost": True, "export": False})
    joined = " | ".join(errors)
    assert "limits.yt_units" in joined and "limits.topics" in joined
    assert "limits.nope" in joined and "limits.drafts" in joined
    assert "features.llm" in joined and "features.ghost" in joined
    assert "llm_usd" not in joined and "saved_items" not in joined and "features.export" not in joined


def test_validate_accepts_empty():
    assert DP.validate({}, {}) == []
    assert DP.validate([], {}) == ["limits: ожидается объект"]


def test_expired():
    assert DP.expired((NOW - timedelta(seconds=1)).isoformat(), NOW)
    assert not DP.expired((NOW + timedelta(days=1)).isoformat(), NOW)
    assert not DP.expired(None, NOW)
    assert DP.expired("not a date", NOW)


def test_has_room():
    assert DP.has_room(None, 10_000)
    assert not DP.has_room(0, 0)
    assert DP.has_room(3, 2) and not DP.has_room(3, 3)


def test_errors_are_not_value_errors():
    assert not issubclass(DP.PlanLimitReached, ValueError)
    assert not issubclass(DP.FeatureOff, ValueError)
    e = DP.PlanLimitReached("tracked_channels", 10)
    assert e.key == "tracked_channels" and e.max == 10 and "10" in str(e)
    assert DP.FeatureOff("export").key == "export"


def test_validate_rejects_non_finite_floats():
    errors = DP.validate({"yt_units": float("inf"), "llm_usd": float("nan")}, {})
    assert len(errors) == 2
    assert any("yt_units" in e for e in errors)
    assert any("llm_usd" in e for e in errors)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

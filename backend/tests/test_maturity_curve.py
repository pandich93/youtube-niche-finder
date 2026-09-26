"""Tests for application/maturity_curve.py: the worker fits the curve from
video_stats_history and stores it in meta only when it passes the checks,
every process loads the stored curve into domain.metrics, and /api/health +
db_stats report which curve is in use and why. Synthetic snapshot histories,
no network. Same throwaway-schema setup as test_unit_quota.py.
Run with pytest, or directly: python3 tests/test_maturity_curve.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import channel_tracking as T  # noqa: E402
from application import maturity_curve as MC  # noqa: E402
from application import search as query  # noqa: E402
from domain import metrics as M  # noqa: E402

client = TestClient(api.app)

# a curve clearly different from the shipped one, so "applied" is observable
SLOW = {0: 0.10, 1: 0.20, 2: 0.30, 3: 0.40, 5: 0.50, 7: 0.60, 14: 0.80,
        21: 0.90, 30: 1.00}


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MATURITY_CURVE_AUTO", raising=False)
    conn = db.get_conn()
    for t in ("video_stats_history", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM meta WHERE key LIKE 'maturity_curve%'")
    conn.commit()
    conn.close()
    MC.reset_cache()
    yield
    MC.reset_cache()


def _seed(n_videos, curve=SLOW, first_age=0.5, last_age=31):
    """n videos published 40 days ago, snapshotted daily from `first_age`."""
    published = datetime.now(timezone.utc) - timedelta(days=40)
    conn = db.get_conn()
    db.upsert_channel(conn, {
        "channel_id": "UCcurve", "title": "c", "custom_url": None, "country": None,
        "description": "", "default_language": None, "subscriber_count": 1,
        "video_count": n_videos, "view_count": 1, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    })
    ages = [first_age] + [a for a in range(1, last_age + 1) if a > first_age]
    for i in range(n_videos):
        vid = f"vcurve{i}"
        db.upsert_video(conn, {
            "video_id": vid, "channel_id": "UCcurve", "title": vid, "description": "",
            "published_at": published.isoformat(), "duration_seconds": 600,
            "view_count": 0, "like_count": 0, "comment_count": 0, "thumbnail": None,
            "tags": "[]", "default_language": "en", "embedding": None,
            "updated_at": published.isoformat(), "category_id": None, "region": None,
            "is_short": 0, "topic_categories": None, "live_content": None,
        })
        for a in ages:
            db.record_video_stats(
                conn, vid, int(10000 * (i + 1) * M.maturity(a, curve)), 0, 0,
                captured_at=(published + timedelta(days=a)).isoformat())
    conn.commit()
    conn.close()


def _conn():
    return db.get_conn()


# ------------------------------------------------------------ apply + load

def test_enough_history_is_fitted_stored_and_loaded():
    _seed(35)
    out = MC.apply_calibration()
    assert out["applied"] is True and out["videosUsed"] == 35

    conn = _conn()
    MC.ensure_loaded(conn)
    conn.close()
    assert M.maturity(3) == pytest.approx(0.40, abs=0.01)
    assert M.maturity(7) == pytest.approx(0.60, abs=0.01)
    M.set_maturity_curve(None)


def test_not_enough_history_keeps_the_manual_curve_and_records_why():
    _seed(10)
    out = MC.apply_calibration()
    assert out["applied"] is False
    conn = _conn()
    MC.ensure_loaded(conn)
    st = MC.status(conn)
    conn.close()
    assert M.active_maturity_curve() == M.MATURITY_CURVE
    assert st["source"] == "manual"
    assert "10" in st["reason"]
    assert st["checkedAt"]


def test_a_failed_recheck_does_not_drop_a_good_stored_curve():
    _seed(35)
    MC.apply_calibration()
    conn = _conn()
    conn.execute("DELETE FROM video_stats_history")
    conn.commit()
    conn.close()
    assert MC.apply_calibration()["applied"] is False
    conn = _conn()
    MC.ensure_loaded(conn, force=True)
    st = MC.status(conn)
    conn.close()
    assert st["source"] == "calibrated" and st["videosUsed"] == 35
    assert st["reason"]  # the latest check is still reported
    M.set_maturity_curve(None)


def test_auto_off_switch_ignores_the_stored_curve(monkeypatch):
    _seed(35)
    MC.apply_calibration()
    monkeypatch.setenv("MATURITY_CURVE_AUTO", "0")
    conn = _conn()
    MC.ensure_loaded(conn, force=True)
    st = MC.status(conn)
    conn.close()
    assert M.active_maturity_curve() == M.MATURITY_CURVE
    assert st["source"] == "manual" and "MATURITY_CURVE_AUTO" in st["reason"]


def test_auto_off_switch_does_not_store_a_new_curve(monkeypatch):
    _seed(35)
    monkeypatch.setenv("MATURITY_CURVE_AUTO", "0")
    assert MC.apply_calibration()["applied"] is False
    conn = _conn()
    assert db.get_meta(conn, "maturity_curve") is None
    conn.close()


def test_a_corrupt_stored_curve_falls_back_to_manual():
    conn = _conn()
    db.set_meta(conn, "maturity_curve", "{not json")
    conn.commit()
    MC.ensure_loaded(conn, force=True)
    conn.close()
    assert M.active_maturity_curve() == M.MATURITY_CURVE


def test_loading_is_cached_until_forced():
    _seed(35)
    MC.apply_calibration()
    conn = _conn()
    MC.ensure_loaded(conn)                    # first call reads meta
    db.set_meta(conn, "maturity_curve", json.dumps({"curve": {"0": 0.9, "30": 1.0}}))
    conn.commit()
    MC.ensure_loaded(conn)                    # within the TTL: no re-read
    assert M.maturity(3) == pytest.approx(0.40, abs=0.01)
    MC.ensure_loaded(conn, force=True)
    conn.close()
    assert M.maturity(3) == pytest.approx(0.9 + 0.1 * 3 / 30)
    M.set_maturity_curve(None)


# --------------------------------------------------------------- exposure

def test_calibrate_tool_reports_the_new_fields_and_keeps_old_keys():
    _seed(10)
    out = T.calibrate_maturity_curve(min_videos=30)
    assert out["calibrated"] is False
    assert out["videosWithFullHistory"] == 10 and out["needed"] == 30
    assert out["hint"] and "samplesPerAge" in out and "missingAges" in out


def test_health_and_db_stats_report_the_curve_in_use():
    _seed(35)
    MC.apply_calibration()
    body = client.get("/api/health").json()["maturityCurve"]
    assert body["source"] == "calibrated" and body["videosUsed"] == 35
    assert body["calibratedAt"]
    stats = query.db_stats()["maturity_curve"]
    assert stats["source"] == "calibrated" and stats["videos_used"] == 35
    M.set_maturity_curve(None)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

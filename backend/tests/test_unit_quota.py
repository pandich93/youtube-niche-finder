"""Tests for the shared 10,000-units/day YouTube quota counter: every request
infrastructure/youtube/client.py::_get sends is added to a per-Pacific-day
counter in `meta`, and /api/health + db_stats() expose it. requests.get is
monkeypatched -- no network, no YouTube key. Same throwaway-schema setup as
test_tags_http.py. Run with pytest, or directly: python3 tests/test_unit_quota.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import collecting  # noqa: E402
from application import search as query  # noqa: E402

client = TestClient(api.app)


def setup_module(_=None):
    db.init_db()


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _fake_get(responses):
    """requests.get stand-in that returns `responses` in order."""
    queue = list(responses)

    def get(url, params=None, timeout=None):
        return queue.pop(0)
    return get


def _units_today():
    conn = db.get_conn()
    try:
        return collecting.units_today(conn)
    finally:
        conn.close()


# ------------------------------------------------------------------ storage

def test_incr_meta_creates_the_key_then_adds_to_it():
    conn = db.get_conn()
    db.incr_meta(conn, "test_incr_counter", 3)
    db.incr_meta(conn, "test_incr_counter", 2)
    conn.commit()
    assert int(db.get_meta(conn, "test_incr_counter")) == 5
    conn.close()


# ------------------------------------------------------------ client._get

def test_successful_request_adds_its_cost(monkeypatch):
    monkeypatch.setattr(yt.requests, "get", _fake_get([_Resp(200, {"items": []})]))
    before = _units_today()
    assert yt._get("videos", "KEY", id="x") == {"items": []}
    assert _units_today() == before + yt.COST["videos"]


def test_every_retry_is_counted(monkeypatch):
    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    monkeypatch.setattr(yt.requests, "get", _fake_get(
        [_Resp(500), _Resp(503), _Resp(200, {"items": []})]))
    before = _units_today()
    yt._get("channels", "KEY", id="x")
    assert _units_today() == before + 3 * yt.COST["channels"]


def test_quota_exceeded_request_is_still_counted(monkeypatch):
    monkeypatch.setattr(yt.requests, "get", _fake_get(
        [_Resp(403, text='{"error": {"errors": [{"reason": "quotaExceeded"}]}}')]))
    before = _units_today()
    with pytest.raises(yt.QuotaExceeded):
        yt._get("search", "KEY", q="x")
    assert _units_today() == before + yt.COST["search"]


def test_broken_counter_never_breaks_the_request(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "incr_meta", boom)
    monkeypatch.setattr(yt.requests, "get", _fake_get([_Resp(200, {"items": [1]})]))
    assert yt._get("videos", "KEY", id="x") == {"items": [1]}

    monkeypatch.setattr(yt.requests, "get", _fake_get([_Resp(404, text="nope")]))
    with pytest.raises(RuntimeError, match="YouTube API error 404"):
        yt._get("videos", "KEY", id="x")


# ------------------------------------------------------------------ exposure

def test_health_exposes_unit_quota():
    body = client.get("/api/health").json()
    uq = body["unitQuota"]
    assert uq["dailyLimit"] == yt.DAILY_UNIT_LIMIT
    assert uq["unitsToday"] == _units_today()
    assert uq["unitsLeft"] == max(0, yt.DAILY_UNIT_LIMIT - uq["unitsToday"])
    assert "searchQuota" in body  # the search.list block is untouched


def test_db_stats_exposes_unit_quota():
    s = query.db_stats()
    uq = s["unit_quota"]
    assert uq["daily_limit"] == yt.DAILY_UNIT_LIMIT
    assert uq["units_today"] == _units_today()
    assert uq["units_left_today"] == max(0, yt.DAILY_UNIT_LIMIT - uq["units_today"])
    assert "search_quota" in s


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

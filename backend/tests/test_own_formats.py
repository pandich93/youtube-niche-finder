"""Tests for application/own_formats.py (plan 24): a year of your channel's
YouTube Analytics rows by day and format, stored on sync and read back as
Shorts-against-long and watch hours. Every Google call is monkeypatched.
Run with pytest, or directly: python3 tests/test_own_formats.py
"""
import os
import sys
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import own_channels as OWN  # noqa: E402
from application import own_formats as OF  # noqa: E402
from infrastructure.youtube import analytics as YA  # noqa: E402
from infrastructure.youtube import oauth as OA  # noqa: E402

CH = "UC" + "ownformats".ljust(22, "0")
TODAY = date(2026, 10, 4)
END = TODAY - timedelta(days=OWN.LAG_DAYS)
CALLS = []


def setup_module(_=None):
    db.init_db()


def report(access_token, channel_id, start, end, metrics, **kw):
    CALLS.append(kw.get("dimensions"))
    if kw.get("dimensions") == "day,creatorContentType":
        out = []
        d = date.fromisoformat(start)
        while d <= date.fromisoformat(end):
            out.append({"day": d.isoformat(), "creatorContentType": "SHORTS", "views": 1000,
                        "estimatedMinutesWatched": 300, "subscribersGained": 1})
            out.append({"day": d.isoformat(), "creatorContentType": "VIDEO_ON_DEMAND",
                        "views": 200, "estimatedMinutesWatched": 600, "subscribersGained": 2})
            d += timedelta(days=1)
        return out
    return [{"video": "v1", "views": 10, "estimatedMinutesWatched": 5, "averageViewDuration": 30,
             "averageViewPercentage": 50.0, "subscribersGained": 0}]


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    CALLS.clear()
    conn = db.get_conn()
    for t in ("own_channel_daily", "own_channels", "own_video_metrics", "own_oauth_pending"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    monkeypatch.setenv("OWN_TOKENS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OWN_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("OWN_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(OA, "exchange_code", lambda *a, **k: {
        "access_token": "at", "refresh_token": "r", "scope": " ".join(OA.SCOPES)})
    monkeypatch.setattr(OA, "refresh_access_token", lambda *a, **k: "at")
    monkeypatch.setattr(OA, "revoke", lambda *a, **k: True)
    monkeypatch.setattr(YA, "mine_channel", lambda at: {
        "channelId": CH, "title": "Mine", "publishedAt": "2024-01-01T00:00:00Z"})
    monkeypatch.setattr(YA, "report", report)


def connect():
    url = OWN.start_connect()["authUrl"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    return OWN.finish_connect(state, "c0de", today=TODAY)


def test_sync_stores_a_year_by_day_and_format_and_reads_it_back():
    connect()
    assert "day,creatorContentType" in CALLS
    f = OF.formats(CH)
    assert f["available"] and f["through"] == END.isoformat()
    assert f["last90"]["SHORTS"]["views"] == 90_000
    assert f["last90"]["VIDEO_ON_DEMAND"]["subscribersPer1000Views"] == 10.0
    assert f["watchHours"]["hours365"] == pytest.approx(365 * 600 / 60, rel=1e-3)
    assert f["shortsVsLong"]["reason"] == "no-variation"     # flat fake data


def test_a_query_youtube_does_not_support_keeps_the_rest_of_the_sync(monkeypatch):
    def unsupported(access_token, channel_id, start, end, metrics, **kw):
        if kw.get("dimensions") == "day,creatorContentType":
            raise YA.AnalyticsError("The query is not supported.", status=400)
        return report(access_token, channel_id, start, end, metrics, **kw)
    monkeypatch.setattr(YA, "report", unsupported)
    out = connect()
    assert out["synced"]["videos"] == 1
    res = OWN.sync(today=TODAY)["channels"][0]
    assert res["error"] is None and res["formatDays"] is None and "not supported" in res["formatsError"]
    assert OF.formats(CH)["available"] is False


def test_only_the_owner_reads_it_and_disconnect_deletes_it():
    connect()
    with pytest.raises(OWN.NotConnected):
        OF.formats(CH, user_id=2)
    OWN.disconnect(CH)
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM own_channel_daily").fetchone()[0]
    conn.close()
    assert n == 0


def test_http_and_mcp_doors():
    connect()
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    r = TestClient(api.app).get(f"/api/own/channels/{CH}/formats")
    assert r.status_code == 200 and r.json()["available"] is True
    assert TestClient(api.app).get("/api/own/channels/UCnope/formats").status_code == 404
    assert srv.own_channel_formats(CH)["available"] is True

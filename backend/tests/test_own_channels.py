"""Tests for application/own_channels.py (plan 14): connecting your own channel
through OAuth, syncing its YouTube Analytics numbers, comparing them with a
niche and with our RPM estimate, and disconnecting. Throwaway schema; every
Google call is monkeypatched -- no network, no Google account.
Run with pytest, or directly: python3 tests/test_own_channels.py
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import own_channels as OWN  # noqa: E402
from infrastructure.youtube import analytics as YA  # noqa: E402
from infrastructure.youtube import oauth as OA  # noqa: E402

REFRESH = "1//refresh-token-that-must-never-leak"
CH = "UC" + "ownchannel".ljust(22, "0")
NICHE = "own-niche"
TODAY = date(2026, 9, 29)


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    conn = db.get_conn()
    for t in ("own_channels", "own_video_metrics", "own_oauth_pending", "video_niches", "videos",
              "channels", "niches", "drafts"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", (NICHE, "q", "Q"))
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                 ("UCother", "Other", 9000))
    for i, views in enumerate((1000, 2000, 4000)):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
                     "duration_seconds, is_short) VALUES (?,?,?,?,?,?,?)",
                     (f"nv{i}", "UCother", "n", "2026-08-01T00:00:00+00:00", views, 600, 0))
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (f"nv{i}", NICHE))
    conn.commit()
    conn.close()
    monkeypatch.setenv("OWN_TOKENS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OWN_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("OWN_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(OA, "exchange_code", lambda *a, **k: {
        "access_token": "at", "refresh_token": REFRESH, "scope": " ".join(OA.SCOPES)})
    monkeypatch.setattr(OA, "refresh_access_token", lambda *a, **k: "at")
    monkeypatch.setattr(YA, "mine_channel", lambda at: {
        "channelId": CH, "title": "My Channel", "publishedAt": "2025-01-01T00:00:00Z"})
    monkeypatch.setattr(YA, "report", fake_report)


def fake_report(access_token, channel_id, start, end, metrics, **kw):
    assert access_token == "at" and channel_id == CH
    rows = [{"video": "own1", "views": 3000, "estimatedMinutesWatched": 9000,
             "averageViewDuration": 180, "averageViewPercentage": 45.0, "subscribersGained": 10,
             "estimatedRevenue": 6.0, "cpm": 5.0, "playbackBasedCpm": 3.0},
            {"video": "own2", "views": 1000, "estimatedMinutesWatched": 2000,
             "averageViewDuration": 120, "averageViewPercentage": 35.0, "subscribersGained": 2,
             "estimatedRevenue": 2.0, "cpm": 4.0, "playbackBasedCpm": 2.0}]
    return [{k: v for k, v in r.items() if k == "video" or k in metrics} for r in rows]


def connect():
    url = OWN.start_connect()["authUrl"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    return OWN.finish_connect(state, "c0de", today=TODAY)


def dump_db():
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM own_channels").fetchall()]
    conn.close()
    return rows


# ------------------------------------------------------------ status / connect

def test_status_lists_what_is_missing(monkeypatch):
    monkeypatch.delenv("OWN_OAUTH_CLIENT_SECRET")
    monkeypatch.setenv("OWN_TOKENS_KEY", "garbage")
    st = OWN.status()
    assert st["configured"] is False
    assert set(st["missing"]) == {"OWN_OAUTH_CLIENT_SECRET", "OWN_TOKENS_KEY"}
    with pytest.raises(OWN.NotConfigured):
        OWN.start_connect()


def test_connect_stores_the_channel_with_an_encrypted_token_and_syncs():
    out = connect()
    assert out["channelId"] == CH and out["title"] == "My Channel" and out["synced"]["videos"] == 2
    rows = dump_db()
    assert len(rows) == 1 and rows[0]["user_id"] == 1
    assert REFRESH.encode() not in bytes(rows[0]["token_enc"])
    assert REFRESH not in json.dumps(OWN.list_channels(), default=str)
    assert REFRESH not in json.dumps(OWN.status(), default=str)


def test_state_is_single_use_and_expires():
    url = OWN.start_connect()["authUrl"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    OWN.finish_connect(state, "c0de", today=TODAY)
    with pytest.raises(OWN.ConnectError):
        OWN.finish_connect(state, "c0de", today=TODAY)          # replayed
    url = OWN.start_connect()["authUrl"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    conn = db.get_conn()
    conn.execute("UPDATE own_oauth_pending SET created_at = ?",
                 ((datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),))
    conn.commit()
    conn.close()
    with pytest.raises(OWN.ConnectError):
        OWN.finish_connect(state, "c0de", today=TODAY)          # too old
    with pytest.raises(OWN.ConnectError):
        OWN.finish_connect("forged", "c0de", today=TODAY)


def test_no_refresh_token_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(OA, "exchange_code", lambda *a, **k: {"access_token": "at"})
    with pytest.raises(OWN.ConnectError) as e:
        connect()
    assert "myaccount.google.com/permissions" in str(e.value)


# ------------------------------------------------------------ sync

def test_sync_stores_28_day_and_lifetime_windows():
    connect()
    conn = db.get_conn()
    rows = conn.execute("SELECT window_name, start_date, end_date, COUNT(*) AS n FROM own_video_metrics "
                        "GROUP BY 1, 2, 3 ORDER BY 1").fetchall()
    conn.close()
    got = {r["window_name"]: (r["start_date"], r["end_date"], r["n"]) for r in rows}
    end = (TODAY - timedelta(days=OWN.LAG_DAYS)).isoformat()
    assert got["28d"] == ((TODAY - timedelta(days=OWN.LAG_DAYS + 27)).isoformat(), end, 2)
    assert got["lifetime"] == ("2025-01-01", end, 2)


def test_sync_without_monetary_access_keeps_the_rest(monkeypatch):
    def no_money(access_token, channel_id, start, end, metrics, **kw):
        if "estimatedRevenue" in metrics:
            raise YA.AnalyticsError("403", reason="forbidden", status=403)
        return fake_report(access_token, channel_id, start, end, metrics, **kw)
    monkeypatch.setattr(YA, "report", no_money)
    out = connect()
    assert out["synced"]["monetary"] is False and out["synced"]["videos"] == 2
    assert OWN.list_channels()["channels"][0]["last28d"]["revenue"] is None


def test_quota_exceeded_is_recorded_and_kept_for_tomorrow(monkeypatch):
    connect()

    def quota(*a, **k):
        raise YA.QuotaExceeded("quota", reason="quotaExceeded", status=403)
    monkeypatch.setattr(YA, "report", quota)
    out = OWN.sync(today=TODAY)
    assert out["channels"][0]["error"] == "quota-exceeded"
    ch = OWN.list_channels()["channels"][0]
    assert ch["lastError"] == "quota-exceeded" and ch["last28d"]["views"] == 4000   # old data kept


def test_list_channels_summarises_the_last_28_days():
    connect()
    ch = OWN.list_channels()["channels"][0]
    assert ch["last28d"] == {"videos": 2, "views": 4000, "revenue": 8.0, "rpm": 2.0,
                             "medianRetentionPct": 40.0}
    assert "token" not in json.dumps(ch).lower()


# ------------------------------------------------------------ comparisons

def test_rpm_calibration_uses_the_real_28_day_rpm():
    connect()
    out = OWN.rpm_calibration()["channels"][0]
    assert out["realRpm"] == 2.0 and out["position"] in ("below", "inside", "above")
    assert set(out["estimate"]) >= {"low", "mid", "high"}


def test_own_vs_niche_compares_lifetime_views_with_the_niche():
    connect()
    out = OWN.own_vs_niche(CH, NICHE)
    assert out["ownMedianViews"] == 2000 and out["nicheMedianViews"] == 2000
    assert out["ownMedianRetentionPct"] == 40.0
    assert out["note"] and "CTR" in out["note"]


def test_own_vs_niche_refuses_a_channel_you_did_not_connect():
    with pytest.raises(OWN.NotConnected):
        OWN.own_vs_niche("UCsomeoneelse", NICHE)


def test_draft_outcomes_carry_your_real_numbers():
    connect()
    from application import metadata_review as MR
    conn = db.get_conn()
    conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count) "
                 "VALUES (?,?,?,?,?)", ("own1", CH, "mine", "2026-08-01T00:00:00+00:00", 3100))
    conn.execute("INSERT INTO drafts (title, created_at, video_id) VALUES (?,?,?)",
                 ("draft", db.now_iso(), "own1"))
    conn.commit()
    conn.close()
    out = [o for o in MR.draft_outcomes(min_age_days=1) if o["videoId"] == "own1"][0]
    assert out["ownMetrics"]["averageViewPercentage"] == 45.0 and out["ownMetrics"]["rpm"] == 2.0


# ------------------------------------------------------------ disconnect

def test_disconnect_revokes_and_forgets_everything(monkeypatch):
    connect()
    revoked = []
    monkeypatch.setattr(OA, "revoke", lambda token: revoked.append(token) or True)
    out = OWN.disconnect(CH)
    assert out == {"channelId": CH, "revoked": True, "deleted": True}
    assert revoked == [REFRESH]
    assert dump_db() == []
    conn = db.get_conn()
    assert conn.execute("SELECT COUNT(*) FROM own_video_metrics").fetchone()[0] == 0
    conn.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_the_consent_and_the_token_exchange_use_the_same_redirect(monkeypatch):
    seen = {}

    def exchange(cid, secret, code, verifier, redirect_uri):
        seen["redirect"] = redirect_uri
        return {"access_token": "at", "refresh_token": REFRESH, "scope": " ".join(OA.SCOPES)}
    monkeypatch.setattr(OA, "exchange_code", exchange)
    out = OWN.start_connect(redirect_uri="http://localhost:8080/api/own/oauth/callback")
    q = parse_qs(urlsplit(out["authUrl"]).query)
    assert q["redirect_uri"] == ["http://localhost:8080/api/own/oauth/callback"]
    OWN.finish_connect(q["state"][0], "c0de", today=TODAY)
    assert seen["redirect"] == "http://localhost:8080/api/own/oauth/callback"


def test_the_configured_redirect_wins(monkeypatch):
    monkeypatch.setenv("OWN_OAUTH_REDIRECT_URI", "http://127.0.0.1:9000/api/own/oauth/callback")
    out = OWN.start_connect(redirect_uri="http://localhost:8080/api/own/oauth/callback")
    assert out["redirectUri"] == "http://127.0.0.1:9000/api/own/oauth/callback"

"""Sessions (multi-user plan P1-04): a sliding idle expiry written at most once
per NF_SESSION_TOUCH_MINUTES, an absolute limit from the sign-in, and the
profile's list of your own sessions -- end one, or all but this one.
Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_sessions.py
"""
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
from application import auth as A  # noqa: E402

PW = "correct horse battery"


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setenv("NF_MULTI_USER", "1")
    conn = db.get_conn()
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM api_tokens")
    conn.execute("DELETE FROM login_attempts")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()


def _ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _set(token, **cols):
    conn = db.get_conn()
    for col, val in cols.items():
        conn.execute(f"UPDATE sessions SET {col} = ? WHERE token_hash = ?", (val, A._token_hash(token)))
    conn.commit()
    conn.close()


def _row(token):
    conn = db.get_conn()
    row = dict(conn.execute("SELECT * FROM sessions WHERE token_hash = ?",
                            (A._token_hash(token),)).fetchone())
    conn.close()
    return row


def _ts(iso):
    return datetime.fromisoformat(iso)


def _token(email="ann@example.com"):
    A.create_user(email, PW)
    return A.login(email, PW)["token"]


# ------------------------------------------------------------ lifetime

def test_a_new_session_has_the_idle_expiry():
    token = _token()
    left = _ts(_row(token)["expires_at"]) - datetime.now(timezone.utc)
    assert timedelta(days=A.SESSION_IDLE_DAYS) - left < timedelta(minutes=1)


def test_activity_extends_the_session_but_not_on_every_request():
    token = _token()
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    # just touched: a request reads, it does not write
    _set(token, expires_at=soon, last_seen_at=_ago(minutes=1), ip="203.0.113.7",
         user_agent="Firefox/1")
    assert A.user_for_token(token, client_ip="203.0.113.7", user_agent="Firefox/1") is not None
    assert _row(token)["expires_at"] == soon
    # past the touch interval: the idle expiry slides forward
    _set(token, last_seen_at=_ago(minutes=A.SESSION_TOUCH_MINUTES + 1))
    assert A.user_for_token(token, client_ip="203.0.113.7", user_agent="Firefox/1") is not None
    row = _row(token)
    left = _ts(row["expires_at"]) - datetime.now(timezone.utc)
    assert timedelta(days=A.SESSION_IDLE_DAYS) - left < timedelta(minutes=1)
    assert _ts(row["last_seen_at"]) > datetime.now(timezone.utc) - timedelta(minutes=1)
    assert row["ip"] == "203.0.113.7" and row["user_agent"] == "Firefox/1"


def test_the_extension_never_passes_the_absolute_limit():
    token = _token()
    created = _ago(days=A.SESSION_MAX_DAYS - 2)
    _set(token, created_at=created, last_seen_at=_ago(days=1))
    assert A.user_for_token(token) is not None
    assert _ts(_row(token)["expires_at"]) <= _ts(created) + timedelta(days=A.SESSION_MAX_DAYS)


def test_past_the_absolute_limit_an_active_session_ends():
    token = _token()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    _set(token, created_at=_ago(days=A.SESSION_MAX_DAYS, minutes=1), expires_at=future,
         last_seen_at=_ago(minutes=1))
    assert A.user_for_token(token) is None


def test_lowering_the_limit_applies_to_existing_sessions(monkeypatch):
    token = _token()
    _set(token, created_at=_ago(days=10))
    assert A.user_for_token(token) is not None
    monkeypatch.setattr(A, "SESSION_MAX_DAYS", 7)
    assert A.user_for_token(token) is None


def test_an_idle_session_ends():
    token = _token()
    _set(token, expires_at=_ago(minutes=1))
    assert A.user_for_token(token) is None


def test_the_user_agent_is_trimmed_to_one_short_line():
    token = _token()
    _set(token, user_agent=None, last_seen_at=_ago(minutes=1))
    A.user_for_token(token, user_agent="Mozilla/5.0\n" + "x" * 500)
    ua = _row(token)["user_agent"]
    assert ua.startswith("Mozilla/5.0 x") and len(ua) == 200 and "\n" not in ua


def test_a_new_sign_in_purges_sessions_past_the_limit():
    old = _token()
    _set(old, created_at=_ago(days=A.SESSION_MAX_DAYS + 1))
    A.login("ann@example.com", PW)
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM sessions WHERE token_hash = ?",
                     (A._token_hash(old),)).fetchone()["n"]
    conn.close()
    assert n == 0


# ------------------------------------------------------------ HTTP

def client(ua="test-browser/1.0"):
    return TestClient(api.app, headers={"X-NF-Client": "tests", "User-Agent": ua})


def _signed_in(email="ann@example.com", ua="test-browser/1.0", new=True):
    uid = A.create_user(email, PW) if new else A.user_id_for_email(email)
    c = client(ua)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return uid, c


def test_an_expired_session_is_401():
    _, c = _signed_in()
    assert c.get("/api/stats").status_code == 200
    conn = db.get_conn()
    conn.execute("UPDATE sessions SET expires_at = ?", (_ago(minutes=1),))
    conn.commit()
    conn.close()
    assert c.get("/api/stats").status_code == 401


def test_a_session_past_the_absolute_limit_is_401():
    _, c = _signed_in()
    conn = db.get_conn()
    conn.execute("UPDATE sessions SET created_at = ?", (_ago(days=A.SESSION_MAX_DAYS + 1),))
    conn.commit()
    conn.close()
    assert c.get("/api/stats").status_code == 401


def test_the_cookie_lasts_until_the_absolute_limit():
    A.create_user("ann@example.com", PW)
    resp = client().post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    assert f"max-age={A.SESSION_MAX_DAYS * 86400}" in resp.headers["set-cookie"].lower()


def test_the_list_shows_only_your_sessions_and_no_secrets():
    _, phone = _signed_in(ua="Mozilla/5.0 (Android) Chrome/120")
    _, laptop = _signed_in(new=False, ua="Mozilla/5.0 (Windows) Firefox/121")
    _, bob = _signed_in("bob@example.com")
    resp = laptop.get("/api/auth/sessions")
    assert resp.status_code == 200
    d = resp.json()
    assert d["idleDays"] == A.SESSION_IDLE_DAYS and d["maxDays"] == A.SESSION_MAX_DAYS
    sessions = d["sessions"]
    assert len(sessions) == 2                       # not bob's
    assert [s["current"] for s in sessions] == [True, False]
    assert sessions[0]["userAgent"] == "Mozilla/5.0 (Windows) Firefox/121"
    assert sessions[1]["userAgent"] == "Mozilla/5.0 (Android) Chrome/120"
    assert all(s["ip"] == "testclient" for s in sessions)
    conn = db.get_conn()
    secrets = [r["token_hash"] for r in conn.execute("SELECT token_hash FROM sessions").fetchall()]
    conn.close()
    cookies = [c.cookies.get("nf_session") for c in (phone, laptop, bob)]
    assert not any(x in resp.text for x in secrets + cookies)
    assert {"token", "tokenHash", "token_hash"}.isdisjoint(sessions[0])


def test_sign_out_on_other_devices_keeps_this_one():
    _, phone = _signed_in()
    _, laptop = _signed_in(new=False)
    _, bob = _signed_in("bob@example.com")
    resp = laptop.post("/api/auth/sessions/end-others")
    assert resp.status_code == 200 and resp.json() == {"ended": 1}
    assert laptop.get("/api/stats").status_code == 200
    assert phone.get("/api/stats").status_code == 401
    assert bob.get("/api/stats").status_code == 200       # someone else's stay


def test_ending_one_session_signs_only_it_out():
    _, phone = _signed_in()
    _, laptop = _signed_in(new=False)
    other = [s for s in laptop.get("/api/auth/sessions").json()["sessions"] if not s["current"]][0]
    resp = laptop.delete(f"/api/auth/sessions/{other['id']}")
    assert resp.status_code == 200 and resp.json() == {"ended": other["id"], "current": False}
    assert phone.get("/api/stats").status_code == 401
    assert laptop.get("/api/stats").status_code == 200


def test_ending_the_current_session_signs_this_browser_out():
    _, c = _signed_in()
    me = c.get("/api/auth/sessions").json()["sessions"][0]
    resp = c.delete(f"/api/auth/sessions/{me['id']}")
    assert resp.status_code == 200 and resp.json()["current"] is True
    assert c.get("/api/stats").status_code == 401


def test_you_cannot_end_someone_elses_session():
    _, ann = _signed_in()
    _, bob = _signed_in("bob@example.com")
    bobs = bob.get("/api/auth/sessions").json()["sessions"][0]
    assert ann.delete(f"/api/auth/sessions/{bobs['id']}").status_code == 404
    assert bob.get("/api/stats").status_code == 200


def test_an_api_token_cannot_see_or_end_sessions():
    _, c = _signed_in()
    token = c.post("/api/auth/tokens", json={"name": "ext"}).json()["token"]
    ext = TestClient(api.app, headers={"X-NF-Client": "extension", "Authorization": f"Bearer {token}"})
    assert ext.get("/api/auth/sessions").status_code == 403
    assert ext.post("/api/auth/sessions/end-others").status_code == 403
    me = c.get("/api/auth/sessions").json()["sessions"][0]
    assert ext.delete(f"/api/auth/sessions/{me['id']}").status_code == 403
    assert c.get("/api/stats").status_code == 200


def test_sessions_need_accounts(monkeypatch):
    monkeypatch.delenv("NF_MULTI_USER", raising=False)
    assert client().get("/api/auth/sessions").status_code == 409


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

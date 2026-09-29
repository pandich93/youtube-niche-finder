"""HTTP sign-in (plan 15, sub-stages 5.1-5.3): with NF_MULTI_USER off nothing
changes; with it on, /api answers 401 without a session, the login sets an
HttpOnly SameSite=Strict cookie, and the few public routes stay open.
Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_http_auth.py
"""
import os
import sys

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
    conn = db.get_conn()
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM api_tokens")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()


@pytest.fixture
def multi(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")


def client():
    return TestClient(api.app, headers={"X-NF-Client": "tests"})


# ------------------------------------------------------------ single user

def test_single_user_mode_needs_no_login(monkeypatch):
    monkeypatch.delenv("NF_MULTI_USER", raising=False)
    c = client()
    assert c.get("/api/stats").status_code == 200
    me = c.get("/api/auth/me").json()
    assert me == {"multiUser": False, "user": {"id": 1, "email": "local", "isAdmin": True}}


# ------------------------------------------------------------ multi user

def test_multi_user_mode_answers_401_without_a_session(multi):
    c = client()
    resp = c.get("/api/stats")
    assert resp.status_code == 401 and "sign in" in resp.json()["detail"].lower()
    assert c.get("/api/auth/me").json() == {"multiUser": True, "user": None, "quota": None}


def test_public_routes_stay_open(multi):
    c = client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/").status_code == 200                           # the dashboard itself
    resp = c.get("/api/own/oauth/callback?error=access_denied", follow_redirects=False)
    assert resp.status_code == 303                                  # the state names the user


def test_login_sets_a_safe_cookie_and_opens_the_api(multi):
    uid = A.create_user("ann@example.com", PW)
    c = client()
    resp = c.post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    assert resp.status_code == 200 and resp.json()["user"]["id"] == uid
    cookie = resp.headers["set-cookie"].lower()
    assert "nf_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "token" not in resp.json()                               # only in the cookie
    assert c.get("/api/stats").status_code == 200
    assert c.get("/api/auth/me").json()["user"]["email"] == "ann@example.com"


def test_a_wrong_password_is_401_with_a_generic_message(multi):
    A.create_user("ann@example.com", PW)
    resp = client().post("/api/auth/login", json={"email": "ann@example.com", "password": "nope nope nope"})
    assert resp.status_code == 401 and resp.json()["detail"] == "wrong email or password"


def test_logout_ends_the_session(multi):
    A.create_user("ann@example.com", PW)
    c = client()
    c.post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/stats").status_code == 401


def test_a_forged_cookie_is_401(multi):
    c = client()
    c.cookies.set("nf_session", "forged-token")
    assert c.get("/api/stats").status_code == 401


def test_own_channels_are_read_as_the_signed_in_user(multi, monkeypatch):
    uid = A.create_user("ann@example.com", PW)
    seen = {}
    from application import own_channels as own
    monkeypatch.setattr(own, "list_channels", lambda user_id=1: seen.setdefault("uid", user_id) and {})
    c = client()
    c.post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    c.get("/api/own/channels")
    assert seen["uid"] == uid


def test_secure_cookie_can_be_forced_for_https(multi, monkeypatch):
    monkeypatch.setenv("NF_COOKIE_SECURE", "1")
    A.create_user("ann@example.com", PW)
    resp = client().post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    assert "secure" in resp.headers["set-cookie"].lower()


# ------------------------------------------------------------ API tokens (5.7)

def _signed_in(email="ann@example.com"):
    uid = A.create_user(email, PW)
    c = client()
    c.post("/api/auth/login", json={"email": email, "password": PW})
    return uid, c


def test_a_bearer_token_works_like_a_sign_in(multi):
    uid, c = _signed_in()
    token = c.post("/api/auth/tokens", json={"name": "extension"}).json()["token"]
    ext = TestClient(api.app, headers={"X-NF-Client": "extension", "Authorization": f"Bearer {token}"})
    assert ext.get("/api/stats").status_code == 200
    assert ext.get("/api/auth/me").json()["user"]["id"] == uid
    bad = TestClient(api.app, headers={"X-NF-Client": "extension", "Authorization": "Bearer nf_forged"})
    assert bad.get("/api/stats").status_code == 401


def test_a_token_cannot_manage_tokens(multi):
    _, c = _signed_in()
    token = c.post("/api/auth/tokens", json={}).json()["token"]
    ext = TestClient(api.app, headers={"X-NF-Client": "extension", "Authorization": f"Bearer {token}"})
    assert ext.get("/api/auth/tokens").status_code == 403
    assert ext.post("/api/auth/tokens", json={}).status_code == 403


def test_tokens_are_listed_without_the_secret_and_can_be_revoked(multi):
    _, c = _signed_in()
    made = c.post("/api/auth/tokens", json={"name": "ext"}).json()
    listed = c.get("/api/auth/tokens").json()["tokens"]
    assert [t["id"] for t in listed] == [made["id"]] and "token" not in listed[0]
    assert c.delete(f"/api/auth/tokens/{made['id']}").status_code == 200
    ext = TestClient(api.app, headers={"X-NF-Client": "extension",
                                       "Authorization": f"Bearer {made['token']}"})
    assert ext.get("/api/stats").status_code == 401


def test_you_cannot_revoke_someone_elses_token(multi):
    _, a = _signed_in("a@example.com")
    _, b = _signed_in("b@example.com")
    tid = a.post("/api/auth/tokens", json={}).json()["id"]
    assert b.delete(f"/api/auth/tokens/{tid}").status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

"""Multi-user plan P1-03: API tokens expire and do only what they were made
for. A new token lives 90 days unless asked otherwise (1..365, or 0 for no
expiry); scopes read / write / mcp decide GET routes, changing routes and MCP
over HTTP. An expired token is no token. Tokens made before this keep working
as before: no expiry, every scope. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_token_scopes.py
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
    for t in ("sessions", "api_tokens", "login_attempts", "saved_items"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()


def _user():
    return A.create_user("tok@example.com", PW)


def _bearer(token):
    return TestClient(api.app, headers={"X-NF-Client": "tests", "Authorization": f"Bearer {token}"})


def _days_left(iso):
    return (datetime.fromisoformat(iso) - datetime.now(timezone.utc)).days


# ------------------------------------------------------------ lifetime

def test_a_new_token_lives_ninety_days_by_default():
    made = A.create_api_token(_user())
    assert 89 <= _days_left(made["expiresAt"]) <= 90 and made["scopes"] == ["read", "write", "mcp"]


def test_lifetime_can_be_chosen_or_turned_off_within_limits():
    uid = _user()
    assert 6 <= _days_left(A.create_api_token(uid, expires_in_days=7)["expiresAt"]) <= 7
    assert A.create_api_token(uid, expires_in_days=0)["expiresAt"] is None
    for bad in (-1, 366, "soon"):
        with pytest.raises(A.AuthError):
            A.create_api_token(uid, expires_in_days=bad)


def _expire(token_id):
    conn = db.get_conn()
    conn.execute("UPDATE api_tokens SET expires_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), token_id))
    conn.commit()
    conn.close()


def test_an_expired_token_is_no_token_and_is_listed_as_expired():
    uid = _user()
    made = A.create_api_token(uid, "old")
    assert A.user_for_api_token(made["token"])["id"] == uid
    _expire(made["id"])
    assert A.user_for_api_token(made["token"]) is None
    assert _bearer(made["token"]).get("/api/stats").status_code == 401
    assert A.list_api_tokens(uid)[0]["expired"] is True


def test_tokens_from_before_keep_working_with_every_scope():
    uid = _user()
    made = A.create_api_token(uid, "legacy")
    conn = db.get_conn()     # what the migration leaves an older row with
    conn.execute("UPDATE api_tokens SET expires_at = NULL, scopes = DEFAULT WHERE id = ?", (made["id"],))
    conn.commit()
    conn.close()
    user = A.user_for_api_token(made["token"])
    assert user["scopes"] == ["read", "write", "mcp"]
    listed = A.list_api_tokens(uid)[0]
    assert listed["expiresAt"] is None and listed["expired"] is False


# -------------------------------------------------------------- scopes

def test_scopes_are_checked_and_kept_in_a_fixed_order():
    uid = _user()
    assert A.create_api_token(uid, scopes="mcp, read")["scopes"] == ["read", "mcp"]
    for bad in ([], "admin", ["read", "root"]):
        with pytest.raises(A.AuthError):
            A.create_api_token(uid, scopes=bad)


def test_a_read_only_token_reads_but_cannot_change_anything():
    token = A.create_api_token(_user(), scopes=["read"])["token"]
    c = _bearer(token)
    assert c.get("/api/stats").status_code == 200
    r = c.post("/api/saved", json={"kind": "video", "refId": "x"})
    assert r.status_code == 403 and r.json()["error"] == "TokenScope" and r.json()["scope"] == "write"


def test_a_write_only_token_cannot_read():
    token = A.create_api_token(_user(), scopes=["write"])["token"]
    r = _bearer(token).get("/api/stats")
    assert r.status_code == 403 and r.json()["scope"] == "read"


def test_the_dashboard_makes_tokens_with_lifetime_and_scopes():
    _user()
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": "tok@example.com", "password": PW}).status_code == 200
    made = c.post("/api/auth/tokens", json={"name": "ext", "expiresInDays": 30,
                                            "scopes": ["read", "write"]}).json()
    assert made["scopes"] == ["read", "write"] and 29 <= _days_left(made["expiresAt"]) <= 30
    listed = c.get("/api/auth/tokens").json()["tokens"][0]
    assert listed["scopes"] == ["read", "write"] and listed["expired"] is False
    assert c.post("/api/auth/tokens", json={"scopes": ["nope"]}).status_code == 400
    assert c.post("/api/auth/tokens", json={"expiresInDays": 999}).status_code == 400


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

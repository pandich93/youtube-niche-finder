"""Multi-user plan P2-05: an admin's read-only support view. Only an admin,
only from a dashboard session (never an API token), never on an admin or a
blocked account, never nested, always with a reason. While it lasts the
requests see the account's rows (_uid and Row-Level Security), every change
is refused but leaving, nobody's YouTube or LLM budget is spent; it ends by
itself when the time is up, and the start, the end and the expiry are in the
action log. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_impersonation.py
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
import infrastructure.youtube.client as yt  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import impersonation as IMP  # noqa: E402
from application import library as L  # noqa: E402
from application import llm_gateway as gw  # noqa: E402
from infrastructure import quota_owner  # noqa: E402
from infrastructure.llm import factory  # noqa: E402

PW = "correct horse battery"
ADMIN, ANN, BOSS = "imp-admin@example.com", "imp-ann@example.com", "imp-boss@example.com"


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setenv("NF_MULTI_USER", "1")
    conn = db.get_conn()
    for table in ("sessions", "api_tokens", "login_attempts", "saved_items", "audit_log"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()


def _users():
    admin = AUTH.create_user(ADMIN, PW, is_admin=True)
    ann = AUTH.create_user(ANN, PW)
    return admin, ann


def _client(email=ADMIN):
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def _view(c, uid, reason="ticket #42: the alerts screen is empty"):
    return c.post(f"/api/admin/users/{uid}/impersonate", json={"reason": reason})


def _save(uid, ref):
    conn = db.get_conn()
    conn.execute("INSERT INTO saved_items (user_id, kind, ref_id, created_at) VALUES (?, 'video', ?, ?)",
                 (uid, ref, db.now_iso()))
    conn.commit()
    conn.close()


def _log(event=None):
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM audit_log WHERE event LIKE 'impersonation%' ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows if event is None or r["event"] == event]


# ------------------------------------------------------------ who may start

def test_an_admin_views_an_account_and_me_says_so():
    admin, ann = _users()
    c = _client()
    r = _view(c, ann)
    assert r.status_code == 200, r.text
    assert r.json()["userId"] == ann and r.json()["email"] == ANN
    me = c.get("/api/auth/me").json()
    assert me["user"] == {"id": ann, "email": ANN, "isAdmin": False}
    imp = me["impersonation"]
    assert imp["readOnly"] is True and imp["admin"] == {"id": admin, "email": ADMIN}
    left = datetime.fromisoformat(imp["expiresAt"]) - datetime.now(timezone.utc)
    assert timedelta(minutes=29) < left <= timedelta(minutes=30)


def test_the_view_length_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("NF_IMPERSONATION_MINUTES", "5")
    _admin, ann = _users()
    exp = datetime.fromisoformat(_view(_client(), ann).json()["expiresAt"])
    assert exp - datetime.now(timezone.utc) <= timedelta(minutes=5)


def test_a_reason_is_required():
    _admin, ann = _users()
    c = _client()
    assert _view(c, ann, reason="   ").status_code == 400
    assert c.post(f"/api/admin/users/{ann}/impersonate", json={}).status_code == 400
    assert c.get("/api/auth/me").json()["impersonation"] is None


def test_only_an_admin_may_start():
    _admin, ann = _users()
    bob = AUTH.create_user("imp-bob@example.com", PW)
    assert _view(_client(ANN), bob).status_code == 403


def test_an_api_token_never_starts_a_view_even_an_admins():
    admin, ann = _users()
    token = AUTH.create_api_token(admin, "t")["token"]
    c = TestClient(api.app, headers={"X-NF-Client": "tests", "Authorization": f"Bearer {token}"})
    assert _view(c, ann).status_code == 403
    assert _log() == []


def test_not_under_an_admin_a_blocked_account_or_oneself():
    admin, ann = _users()
    boss = AUTH.create_user(BOSS, PW, is_admin=True)
    c = _client()
    assert _view(c, boss).status_code == 400
    assert _view(c, admin).status_code == 400
    AUTH.set_disabled(ann, True)
    assert _view(c, ann).status_code == 400
    assert _view(c, 999999).status_code == 404
    assert _log() == []


def test_no_nesting():
    admin, ann = _users()
    bob = AUTH.create_user("imp-bob@example.com", PW)
    c = _client()
    assert _view(c, ann).status_code == 200
    # through HTTP the second start is a write in a read-only view
    assert _view(c, bob).status_code == 403
    sid = c.get("/api/auth/me")  # still Ann
    assert sid.json()["user"]["id"] == ann
    conn = db.get_conn()
    session_id = conn.execute("SELECT id FROM sessions WHERE user_id = ?", (admin,)).fetchone()["id"]
    conn.close()
    with pytest.raises(IMP.ImpersonationError):
        IMP.start(admin, session_id, bob, "second")
    assert len(_log("impersonation_start")) == 1


def test_single_user_mode_has_no_views(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "0")
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/admin/users/2/impersonate", json={"reason": "x"}).status_code == 409
    assert c.post("/api/auth/impersonation/end").json() == {"ended": False}


# ------------------------------------------------------------ read-only

def test_every_change_is_refused_in_a_view():
    admin, ann = _users()
    _save(ann, "imp-ann-1")
    c = _client()
    _view(c, ann)
    for method, path, body in (("POST", "/api/saved", {"kind": "video", "refId": "x"}),
                               ("PUT", "/api/me/locale", {"locale": "en"}),
                               ("DELETE", "/api/saved/1", None),
                               ("PATCH", "/api/saved/1", {}),
                               ("POST", "/api/auth/password", {"currentPassword": PW, "password": "x" * 20})):
        r = c.request(method, path, json=body)
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["error"] == "ReadOnlyView"
    # reading works, and nothing of Ann's was touched
    assert [i["refId"] for i in c.get("/api/saved").json()["items"]] == ["imp-ann-1"]


def test_admin_and_profile_security_screens_are_not_open_in_a_view():
    _admin, ann = _users()
    c = _client()
    _view(c, ann)
    assert c.get("/api/admin/users").status_code == 403       # Ann is no admin
    assert c.get("/api/admin/audit").status_code == 403
    assert c.get("/api/auth/sessions").status_code == 403     # her addresses and browsers
    assert c.get("/api/auth/tokens").status_code == 403


# ------------------------------------------------------------ whose rows

def test_requests_see_the_accounts_rows_through_rls_not_the_admins(monkeypatch):
    admin, ann = _users()
    _save(admin, "imp-admin-1")
    _save(ann, "imp-ann-1")
    seen = {}

    def leaky(kind=None, folder=None, limit=200, *, user_id):
        # the user_id filter "forgotten": only Row-Level Security decides
        seen["uid"], seen["rls"] = user_id, db.rls_user()
        conn = db.get_conn()
        try:
            return [{"userId": r["user_id"], "refId": r["ref_id"]}
                    for r in conn.execute("SELECT user_id, ref_id FROM saved_items").fetchall()]
        finally:
            conn.close()

    monkeypatch.setattr(L, "list_items", leaky)
    c = _client()
    assert {i["refId"] for i in c.get("/api/saved").json()["items"]} == {"imp-admin-1"}
    _view(c, ann)
    items = c.get("/api/saved").json()["items"]
    assert [(i["userId"], i["refId"]) for i in items] == [(ann, "imp-ann-1")]
    assert seen == {"uid": ann, "rls": ann}


# ------------------------------------------------------------ quota

def test_a_view_spends_no_youtube_units(monkeypatch):
    _admin, ann = _users()
    monkeypatch.setattr(api, "API_KEY", "test-key")

    def no_network(*a, **kw):
        raise AssertionError("a support view must not call YouTube")

    monkeypatch.setattr(yt.requests, "get", no_network)
    c = _client()
    _view(c, ann)
    r = c.get("/api/inspect/video", params={"video_id": "impNotCached"})
    assert r.status_code == 403 and r.json()["error"] == "ReadOnlyView"
    assert yt.user_usage(ann)["units"] == 0


def test_a_frozen_request_gets_llm_answers_from_the_cache_only(monkeypatch):
    class Boom:
        def complete_json(self, *a, **kw):
            raise AssertionError("a support view must not call the LLM")

    monkeypatch.setattr(factory, "get_provider", lambda: Boom())
    token = quota_owner.freeze()
    try:
        assert gw.run("imp-task", "sys", "user", {"type": "object"}) is None
        assert gw.run("imp-task", "sys", "user", {"type": "object"}, private=True) is None
    finally:
        quota_owner.unfreeze(token)
    assert not quota_owner.frozen()


def test_the_freeze_lasts_only_for_the_view(monkeypatch):
    _admin, ann = _users()
    flags = []
    monkeypatch.setattr(L, "list_items", lambda *a, user_id, **kw: flags.append(quota_owner.frozen()) or [])
    c = _client()
    c.get("/api/saved")
    _view(c, ann)
    c.get("/api/saved")
    c.post("/api/auth/impersonation/end")
    c.get("/api/saved")
    assert flags == [False, True, False]


# ------------------------------------------------------------ the end

def test_leaving_gives_the_admin_back_and_is_logged():
    admin, ann = _users()
    c = _client()
    _view(c, ann, reason="ticket 7")
    r = c.post("/api/auth/impersonation/end")
    assert r.status_code == 200 and r.json()["ended"] is True
    assert r.json()["user"]["id"] == admin
    me = c.get("/api/auth/me").json()
    assert me["user"]["id"] == admin and me["impersonation"] is None
    assert c.get("/api/admin/users").status_code == 200
    assert c.post("/api/auth/impersonation/end").json() == {"ended": False}
    start, end = _log()
    assert (start["event"], start["actor_id"], start["target_id"]) == ("impersonation_start", admin, ann)
    assert start["details"]["reason"] == "ticket 7" and start["ip"]
    assert (end["event"], end["actor_id"], end["target_id"]) == ("impersonation_end", admin, ann)
    assert end["details"]["why"] == "exit"


def test_the_view_expires_by_itself():
    admin, ann = _users()
    c = _client()
    _view(c, ann)
    conn = db.get_conn()
    conn.execute("UPDATE sessions SET impersonation_expires_at = ? WHERE user_id = ?",
                 ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), admin))
    conn.commit()
    conn.close()
    me = c.get("/api/auth/me").json()
    assert me["user"]["id"] == admin and me["impersonation"] is None
    assert c.get("/api/admin/users").status_code == 200
    assert [e["event"] for e in _log()] == ["impersonation_start", "impersonation_expired"]
    # the session itself lives on
    assert c.get("/api/auth/me").json()["user"]["id"] == admin


def test_blocking_the_account_ends_the_view():
    admin, ann = _users()
    c = _client()
    _view(c, ann)
    AUTH.set_disabled(ann, True)
    assert c.get("/api/auth/me").json()["user"]["id"] == admin
    assert _log("impersonation_end")[0]["details"]["why"] == "account blocked"


def test_signing_out_in_a_view_ends_it_in_the_log():
    admin, ann = _users()
    c = _client()
    _view(c, ann)
    assert c.post("/api/auth/logout").status_code == 200
    assert _log("impersonation_end")[0]["details"]["why"] == "logout"
    assert c.get("/api/auth/me").json()["user"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

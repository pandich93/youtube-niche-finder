"""Multi-user plan P1-07: the action log. Every security event and admin action
is written once, with who, to whom and from which address; a failed sign-in
never stores the password (nor an unknown "email", which may be one); only an
admin signed in to the dashboard reads it; the filters and pages work; old
admin_actions rows move over; entries leave by age. Mail is captured, never
sent. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_audit_log.py
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import audit as AUD  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import own_channels as OWN  # noqa: E402
from application import plans as PL  # noqa: E402
from domain import audit as DA  # noqa: E402
from infrastructure import mailer  # noqa: E402

PW, NEW_PW = "correct horse battery", "a brand new long secret"


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "niche-finder <no-reply@example.test>")
    monkeypatch.setenv("NF_PUBLIC_URL", "https://niches.example.test")
    monkeypatch.delenv("NF_REGISTRATION", raising=False)
    monkeypatch.delenv("NF_AUDIT_RETENTION_DAYS", raising=False)
    monkeypatch.setattr(OWN, "disconnect", lambda channel_id, user_id: {"revoked": False})
    conn = db.get_conn()
    for t in ("sessions", "api_tokens", "login_attempts", "email_tokens", "audit_log",
              "user_plans", "plan_changes"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM plans WHERE is_base = 0")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("UPDATE users SET is_admin = 1, disabled_at = NULL WHERE id = 1")
    conn.commit()
    conn.close()
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text: sent.append((to, subject, text)))
    return sent


def _client():
    return TestClient(api.app, headers={"X-NF-Client": "tests"})


def _sign_in(email, password=PW):
    c = _client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    return c, r


def _people():
    boss = AUTH.create_user("boss@example.com", PW, is_admin=True)
    ann = AUTH.create_user("ann@example.com", PW)
    return boss, ann


def _entries(event=None):
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    finally:
        conn.close()
    return [r for r in rows if event is None or r["event"] == event]


def _details(r):
    d = r["details"]
    return json.loads(d) if isinstance(d, str) else d


def _link(mail, kind):
    m = re.search(r"https://niches\.example\.test/#/(\w+)/(\S+)", mail[2])
    assert m and m.group(1) == kind, mail[2]
    return m.group(2)


# ------------------------------------------------------------ sign-in

def test_a_sign_in_is_logged_with_the_client_address():
    _boss, ann = _people()
    _c, r = _sign_in("ann@example.com")
    assert r.status_code == 200
    [e] = _entries("login")
    assert (e["actor_id"], e["target_id"], e["ip"]) == (ann, ann, "testclient")


def test_a_failed_sign_in_never_stores_the_password_or_an_unknown_email():
    _boss, ann = _people()
    secret = "Sup3r-Secret-Guess!"
    assert _sign_in("ann@example.com", secret)[1].status_code == 401
    assert _sign_in(secret, secret)[1].status_code == 401      # a password in the email box
    known, unknown = _entries("login_failed")
    assert (known["target_id"], known["actor_id"], known["ip"]) == (ann, None, "testclient")
    assert _details(known) == {"reason": "wrong password"}
    assert unknown["target_id"] is None and unknown["target_email"] is None
    dump = json.dumps([dict(r) for r in _entries()], default=str)
    assert secret not in dump and secret.lower() not in dump


def test_a_throttled_sign_in_is_logged_too(monkeypatch):
    _people()
    monkeypatch.setattr(AUTH, "_wait", lambda conn, kind, key, now: 60)
    assert _sign_in("ann@example.com")[1].status_code == 429
    assert [_details(e)["reason"] for e in _entries("login_failed")] == ["throttled"]


# ------------------------------------------------------------ the account itself

def test_sign_up_reset_and_profile_changes_are_logged(outbox):
    c = _client()
    assert c.post("/api/auth/register", json={"email": "new@example.com"}).status_code == 200
    r = c.post("/api/auth/register/complete",
               json={"token": _link(outbox[-1], "signup"), "password": PW})
    assert r.status_code == 200
    uid = AUTH.user_id_for_email("new@example.com")
    [e] = _entries("signup")
    assert (e["actor_id"], e["target_id"], e["target_email"]) == (uid, uid, "new@example.com")

    r = c.post("/api/auth/password", json={"currentPassword": PW, "password": NEW_PW})
    assert r.status_code == 200
    assert [(e["actor_id"], e["target_id"]) for e in _entries("password_change")] == [(uid, uid)]

    assert c.post("/api/auth/email", json={"password": NEW_PW,
                                           "email": "other@example.com"}).status_code == 200
    [asked] = _entries("email_change_requested")
    assert _details(asked) == {"from": "new@example.com", "to": "other@example.com"}
    assert c.post("/api/auth/email/confirm",
                  json={"token": _link(outbox[-1], "email")}).status_code == 200
    [done] = _entries("email_change")
    assert _details(done) == {"from": "new@example.com", "to": "other@example.com"}

    assert c.post("/api/auth/password/forgot", json={"email": "other@example.com"}).status_code == 200
    r = c.post("/api/auth/password/reset",
               json={"token": _link(outbox[-1], "reset"), "password": PW})
    assert r.status_code == 200
    [reset] = _entries("password_reset")
    assert (reset["target_id"], reset["ip"]) == (uid, "testclient")
    dump = json.dumps([dict(r) for r in _entries()], default=str)
    assert PW not in dump and NEW_PW not in dump


def test_api_tokens_are_logged_without_the_token():
    _boss, ann = _people()
    c, _ = _sign_in("ann@example.com")
    made = c.post("/api/auth/tokens", json={"name": "ext", "scopes": ["read"]}).json()
    assert c.delete(f"/api/auth/tokens/{made['id']}").status_code == 200
    [created] = _entries("token_create")
    [revoked] = _entries("token_revoke")
    assert created["actor_id"] == ann and _details(created)["scopes"] == ["read"]
    assert _details(revoked) == {"id": made["id"], "name": "ext"}
    dump = json.dumps([dict(r) for r in _entries()], default=str)
    assert made["token"] not in dump


def test_tariff_changes_by_the_person_the_admin_and_the_system_are_logged():
    boss, ann = _people()
    pro = PL.create_plan({"name": "Pro", "slug": "pro", "periodDays": 1})
    PL.choose(ann, pro["id"])
    soon = datetime.now(timezone.utc) + timedelta(days=1)
    PL.assign(boss, ann, pro["id"], soon.isoformat())
    PL.expire_due(soon + timedelta(days=2))
    got = [(e["actor_id"], e["target_id"], _details(e)["by"]) for e in _entries("plan_change")]
    assert got == [(ann, ann, "self"), (boss, ann, f"admin:{boss}"), (0, ann, "system")]


def test_account_and_tariff_actions_of_an_admin_are_logged():
    boss, ann = _people()
    admin, _ = _sign_in("boss@example.com")
    made = admin.post("/api/admin/users", json={"email": "x@example.com"}).json()
    assert admin.post(f"/api/admin/users/{ann}/disable").status_code == 200
    assert admin.post(f"/api/admin/users/{ann}/enable").status_code == 200
    assert admin.request("DELETE", f"/api/admin/users/{made['id']}",
                         json={"confirmEmail": "x@example.com"}).status_code == 200
    plan = admin.post("/api/admin/plans", json={"name": "Team", "slug": "team"}).json()
    assert admin.put(f"/api/admin/plans/{plan['id']}",
                     json={"name": "Team+", "slug": "team"}).status_code == 200
    assert admin.post(f"/api/admin/plans/{plan['id']}/archive").status_code == 200
    assert admin.post(f"/api/admin/plans/{plan['id']}/restore").status_code == 200
    got = [(e["event"], e["actor_id"], e["target_id"]) for e in _entries()
           if e["event"] not in ("login",)]
    assert got == [("account_create", boss, made["id"]), ("account_disable", boss, ann),
                   ("account_enable", boss, ann), ("account_delete", boss, made["id"]),
                   ("plan_create", boss, None), ("plan_update", boss, None),
                   ("plan_archive", boss, None), ("plan_restore", boss, None)]
    assert all(e["ip"] == "testclient" for e in _entries())
    assert _details(_entries("plan_create")[0]) == {"id": plan["id"], "slug": "team"}
    # the one-time password of the new account is in the answer only
    assert made["password"] not in json.dumps([dict(r) for r in _entries()], default=str)


def test_self_delete_is_logged_and_the_log_outlives_the_account():
    _boss, ann = _people()
    c, _ = _sign_in("ann@example.com")
    assert c.request("DELETE", "/api/auth/me", json={"password": PW}).status_code == 200
    [gone] = _entries("account_self_delete")
    assert (gone["actor_id"], gone["target_id"], gone["target_email"]) == (ann, ann,
                                                                          "ann@example.com")
    assert _entries("login")                     # the earlier sign-in stays, too
    out = AUD.search(user="ann@example.com")     # found by the email after the account is gone
    assert {e["event"] for e in out["entries"]} == {"account_self_delete"}


def test_cli_user_commands_are_logged_as_the_operator(monkeypatch, capsys):
    import io

    import interfaces.cli.cli as cli

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["cli.py", *argv])
        monkeypatch.setattr(sys, "stdin", io.StringIO(PW + "\n"))
        with pytest.raises(SystemExit) as exc:
            cli.main()
        capsys.readouterr()
        return exc.value.code

    assert run("create-user", "cli@example.com", "--password-stdin") == 0
    assert run("set-password", "cli@example.com", "--password-stdin") == 0
    uid = AUTH.user_id_for_email("cli@example.com")
    got = [(e["event"], e["actor_id"], e["target_id"], e["ip"]) for e in _entries()]
    assert got == [("account_create", 0, uid, None), ("password_change", 0, uid, None)]


# ------------------------------------------------------------ who may read it

def test_only_an_admin_signed_in_to_the_dashboard_reads_the_log(monkeypatch):
    boss, _ann = _people()
    assert _client().get("/api/admin/audit").status_code == 401
    c, _ = _sign_in("ann@example.com")
    assert c.get("/api/admin/audit").status_code == 403
    monkeypatch.setattr(api, "_feature_block", lambda request, user_id: None)  # roles, not tariffs
    token = AUTH.create_api_token(boss, "ops")["token"]
    bare = TestClient(api.app, headers={"X-NF-Client": "tests", "Authorization": f"Bearer {token}"})
    assert bare.get("/api/admin/audit").status_code == 403
    admin, _ = _sign_in("boss@example.com")
    r = admin.get("/api/admin/audit")
    assert r.status_code == 200 and r.json()["total"] >= 2


# ------------------------------------------------------------ filters and pages

def _seed():
    boss, ann = _people()
    conn = db.get_conn()
    for i, (event, actor, target) in enumerate((("login", ann, ann), ("login_failed", None, ann),
                                                ("account_disable", boss, ann),
                                                ("login", boss, boss), ("token_create", boss, boss))):
        AUD.record(conn, event, actor_id=actor, target_id=target, ip="203.0.113.9")
        conn.execute("UPDATE audit_log SET created_at = ? WHERE id = (SELECT MAX(id) FROM audit_log)",
                     (f"2026-03-0{i + 1}T12:00:00+00:00",))
    conn.commit()
    conn.close()
    return boss, ann


def test_filters_by_event_group_user_and_period():
    boss, ann = _seed()
    admin, _ = _sign_in("boss@example.com")       # one more entry: today's sign-in

    def events(**params):
        r = admin.get("/api/admin/audit", params=params)
        assert r.status_code == 200, r.text
        return [e["event"] for e in r.json()["entries"]]

    assert events(event="login_failed") == ["login_failed"]
    assert events(event="auth", until="2026-03-31") == ["login", "login_failed", "login"]
    assert events(user="ann@example.com") == ["account_disable", "login_failed", "login"]
    assert events(user=str(boss), until="2026-03-31") == ["token_create", "login", "account_disable"]
    assert events(since="2026-03-02", until="2026-03-03") == ["account_disable", "login_failed"]
    out = admin.get("/api/admin/audit", params={"user": "ann@example.com"}).json()
    assert out["entries"][0]["targetEmail"] == "ann@example.com"
    assert out["entries"][0]["actorEmail"] == "boss@example.com"
    assert admin.get("/api/admin/audit", params={"event": "nope"}).status_code == 400
    assert admin.get("/api/admin/audit", params={"since": "March"}).status_code == 400


def test_pages_are_newest_first_and_count_the_total():
    _seed()
    first = AUD.search(until="2026-03-31", per_page=2)
    second = AUD.search(until="2026-03-31", per_page=2, page=2)
    last = AUD.search(until="2026-03-31", per_page=2, page=3)
    assert first["total"] == second["total"] == 5
    days = [e["at"][:10] for e in first["entries"] + second["entries"] + last["entries"]]
    assert days == ["2026-03-05", "2026-03-04", "2026-03-03", "2026-03-02", "2026-03-01"]
    assert AUD.search(per_page=10_000)["perPage"] == AUD.PAGE_MAX


# ------------------------------------------------------------ what is stored, how long

def test_details_never_keep_credentials():
    cleaned = DA.clean_details({"password": "x", "newPassword": "y", "token": "nf_z",
                                "api_key": "k", "secret": "s", "passwordHash": "h",
                                "nested": {"Authorization": "Bearer q", "ok": 1},
                                "name": "n" * 500, "scopes": ["read"]})
    assert cleaned == {"nested": {"ok": 1}, "name": "n" * 200, "scopes": ["read"]}
    with pytest.raises(AUD.AuditError):
        AUD.log("whatever")


def test_old_entries_leave_by_age(monkeypatch):
    _seed()
    now = datetime(2026, 3, 10, tzinfo=timezone.utc)
    monkeypatch.setenv("NF_AUDIT_RETENTION_DAYS", "7")       # keeps from 2026-03-03
    assert AUD.purge_expired(now) == 2
    assert AUD.search(until="2026-03-31")["total"] == 3
    monkeypatch.setenv("NF_AUDIT_RETENTION_DAYS", "0")       # 0 = forever
    assert AUD.purge_expired(now + timedelta(days=999)) == 0


def test_admin_actions_rows_move_into_the_log():
    conn = db.get_conn()
    try:
        conn.execute("CREATE TABLE admin_actions (id BIGSERIAL PRIMARY KEY, admin_id BIGINT NOT NULL, "
                     "action TEXT NOT NULL, target_user_id BIGINT NOT NULL, target_email TEXT, "
                     "created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO admin_actions (admin_id, action, target_user_id, target_email, "
                     "created_at) VALUES (0, 'delete', 42, 'old@example.com', '2026-01-02T00:00:00+00:00'), "
                     "(42, 'self-delete', 42, 'me@example.com', '2026-01-03T00:00:00+00:00')")
        db.migrate(conn)
        conn.commit()
        assert not db._existing_columns(conn, "admin_actions")
        rows = conn.execute("SELECT event, actor_id, target_id, target_email, created_at "
                            "FROM audit_log ORDER BY created_at").fetchall()
    finally:
        conn.close()
    assert [(r["event"], r["actor_id"], r["target_id"], r["target_email"], r["created_at"])
            for r in rows] == [
        ("account_delete", 0, 42, "old@example.com", "2026-01-02T00:00:00+00:00"),
        ("account_self_delete", 42, 42, "me@example.com", "2026-01-03T00:00:00+00:00")]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

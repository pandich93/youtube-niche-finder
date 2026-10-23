"""Multi-user plan P0-04: blocking and deleting accounts. A blocked person
cannot sign in and their sessions and tokens stop at once; a deleted person
leaves no row behind -- in any table with user_id, in meta -- while the other
users and the shared corpus stay as they were. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_user_deletion.py
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
from application import auth as AUTH  # noqa: E402
from application import digest as DG  # noqa: E402
from application import own_channels as OWN  # noqa: E402
from application import users_admin as UA  # noqa: E402
from domain.users import LOCAL_USER_ID  # noqa: E402
from infrastructure.youtube import client as YT  # noqa: E402

PW = "correct horse battery"


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    conn = db.get_conn()
    for table in db.PERSONAL_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE user_id <> 1")
    conn.execute("DELETE FROM admin_actions")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("UPDATE users SET is_admin = 1, disabled_at = NULL WHERE id = 1")
    conn.commit()
    conn.close()


def _columns(conn, table):
    return conn.execute(
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ?", (table,)).fetchall()


def _fill(conn, table, uid, tag="row"):
    """One row in `table` for `uid` (None = a shared row), whatever its columns:
    every NOT NULL column without a default gets a value of its type."""
    values = {}
    for c in _columns(conn, table):
        name, kind = c["column_name"], c["data_type"]
        if name == "user_id":
            values[name] = uid
        elif name == "plan_id":                            # a real plan: user_plans has a foreign key
            values[name] = conn.execute("SELECT id FROM plans ORDER BY id LIMIT 1").fetchone()["id"]
        elif c["column_default"] is None and c["is_nullable"] == "NO":
            values[name] = {"integer": 7, "bigint": 7, "smallint": 1, "double precision": 1.0,
                            "real": 1.0, "numeric": 1, "boolean": True, "jsonb": "{}", "json": "{}",
                            "bytea": b"x"}.get(kind, f"{table}-{name}-{uid}-{tag}")
    conn.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                 tuple(values.values()))


def _count(conn, table, uid):
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = ?", (uid,)).fetchone()["n"]


def _people():
    boss = AUTH.create_user("boss@example.com", PW, is_admin=True)
    ann = AUTH.create_user("ann@example.com", PW)
    bob = AUTH.create_user("bob@example.com", PW)
    return boss, ann, bob


def _client():
    return TestClient(api.app, headers={"X-NF-Client": "tests"})


def _sign_in(email, password=PW):
    c = _client()
    return c, c.post("/api/auth/login", json={"email": email, "password": password})


def _signs_in(email, password=PW) -> bool:
    return _sign_in(email, password)[1].status_code == 200


# ------------------------------------------------------------ the guard

def test_every_user_id_table_is_covered():
    conn = db.get_conn()
    found = {r["table_name"] for r in conn.execute(
        "SELECT table_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND column_name = 'user_id'").fetchall()}
    conn.close()
    assert found == set(db.PERSONAL_TABLES), (
        f"add to PERSONAL_TABLES: {sorted(found - set(db.PERSONAL_TABLES))}; "
        f"gone: {sorted(set(db.PERSONAL_TABLES) - found)}")


# ------------------------------------------------------------ deleting

def test_delete_removes_only_that_users_rows(monkeypatch):
    monkeypatch.setattr(OWN, "disconnect", lambda channel_id, user_id: {"revoked": False})
    boss, ann, bob = _people()
    conn = db.get_conn()
    for table in db.PERSONAL_TABLES:
        _fill(conn, table, ann)
        _fill(conn, table, bob)
    _fill(conn, "events", None, tag="shared")              # a shared fact, nobody's
    for uid in (ann, bob):
        for key in (YT.user_units_key(uid), YT.user_search_key(uid), DG._last_sent_key(uid)):
            conn.execute("INSERT INTO meta (key, value) VALUES (?, '5')", (key,))
    conn.execute("INSERT INTO transcripts (video_id, text, created_by) VALUES ('vdel1', 'hi', ?) "
                 "ON CONFLICT (video_id) DO UPDATE SET created_by = excluded.created_by", (ann,))
    conn.commit()
    conn.close()

    out = UA.delete(boss, ann, "ANN@example.com ")         # the email is normalised
    assert out["deleted"] == ann and out["email"] == "ann@example.com"

    conn = db.get_conn()
    try:
        for table in db.PERSONAL_TABLES:
            assert _count(conn, table, ann) == 0, table
            assert _count(conn, table, bob) >= 1, table
        assert conn.execute("SELECT COUNT(*) AS n FROM events WHERE user_id IS NULL").fetchone()["n"] >= 1
        keys = {r["key"] for r in conn.execute("SELECT key FROM meta").fetchall()}
        assert not {YT.user_units_key(ann), YT.user_search_key(ann), DG._last_sent_key(ann)} & keys
        assert {YT.user_units_key(bob), YT.user_search_key(bob), DG._last_sent_key(bob)} <= keys
        row = conn.execute("SELECT created_by FROM transcripts WHERE video_id = 'vdel1'").fetchone()
        assert row is not None and row["created_by"] is None   # the corpus stays, now the operator's
        assert conn.execute("SELECT 1 FROM users WHERE id = ?", (ann,)).fetchone() is None
        log = conn.execute("SELECT admin_id, action, target_email FROM admin_actions").fetchall()
        assert [(r["admin_id"], r["action"], r["target_email"]) for r in log] == [
            (boss, "delete", "ann@example.com")]
    finally:
        conn.close()


def test_delete_revokes_own_channel_tokens(monkeypatch):
    calls = []
    monkeypatch.setattr(OWN, "disconnect", lambda channel_id, user_id:
                        calls.append((channel_id, user_id)) or {"revoked": True})
    boss, ann, _bob = _people()
    conn = db.get_conn()
    _fill(conn, "own_channels", ann)
    conn.commit()
    channel = conn.execute("SELECT channel_id FROM own_channels WHERE user_id = ?", (ann,)).fetchone()
    conn.close()
    assert UA.delete(boss, ann, "ann@example.com")["oauthRevoked"] == 1
    assert calls == [(channel["channel_id"], ann)]


def test_cannot_remove_self_last_admin_or_local():
    boss, ann, _bob = _people()
    with pytest.raises(UA.UserAdminError):
        UA.delete(boss, boss, "boss@example.com")              # not yourself, here
    with pytest.raises(UA.UserAdminError):
        UA.delete(boss, LOCAL_USER_ID, "local")                 # the data from before accounts
    with pytest.raises(UA.UserAdminError):
        UA.delete(boss, ann, "bob@example.com")                 # the email must match
    conn = db.get_conn()
    conn.execute("UPDATE users SET is_admin = 0 WHERE id = 1")  # boss is the only admin now
    conn.commit()
    conn.close()
    with pytest.raises(UA.UserAdminError):
        UA.delete_self(boss, PW)
    with pytest.raises(UA.UserAdminError):
        UA.set_disabled(ann, boss, True)
    with pytest.raises(UA.UserNotFound):
        UA.delete(boss, 999999, "x@example.com")


def test_a_person_deletes_their_own_account_with_their_password():
    _boss, ann, _bob = _people()
    c, r = _sign_in("ann@example.com")
    assert r.status_code == 200
    assert c.request("DELETE", "/api/auth/me", json={"password": "not it at all"}).status_code == 400
    r = c.request("DELETE", "/api/auth/me", json={"password": PW})
    assert r.status_code == 200 and r.json()["deleted"] == ann
    assert c.get("/api/stats").status_code == 401
    assert not _signs_in("ann@example.com")


# ------------------------------------------------------------ blocking

def test_blocking_ends_sessions_and_tokens_at_once():
    from anyio import run

    from interfaces.mcp import auth as MA
    _boss, ann, _bob = _people()
    c, _ = _sign_in("ann@example.com")
    token = AUTH.create_api_token(ann, "ext")["token"]
    admin, _ = _sign_in("boss@example.com")
    assert admin.post(f"/api/admin/users/{ann}/disable").json()["disabled"] is True
    assert c.get("/api/stats").status_code == 401                       # the session is gone
    assert AUTH.user_for_api_token(token) is None                       # so is the token
    assert run(MA.ApiTokenVerifier().verify_token, token) is None       # and MCP with it
    _, r = _sign_in("ann@example.com")
    assert r.status_code == 401 and r.json()["detail"] == "wrong email or password"
    users = {u["id"]: u for u in admin.get("/api/admin/users").json()["users"]}
    assert users[ann]["disabled"] is True
    assert admin.post(f"/api/admin/users/{ann}/enable").json()["disabled"] is False
    assert _signs_in("ann@example.com")


def test_a_blocked_session_row_alone_does_not_sign_in():
    # even if a session row survived, a blocked account is not signed in
    _boss, ann, _bob = _people()
    c, _ = _sign_in("ann@example.com")
    conn = db.get_conn()
    conn.execute("UPDATE users SET disabled_at = ? WHERE id = ?", (db.now_iso(), ann))
    conn.commit()
    conn.close()
    assert c.get("/api/stats").status_code == 401


# ------------------------------------------------------------ HTTP roles

def test_account_routes_need_an_admin_signed_in_to_the_dashboard(monkeypatch):
    boss, _ann, bob = _people()
    a, _ = _sign_in("ann@example.com")
    for method, path, body in (("POST", "/api/admin/users", {"email": "x@example.com"}),
                               ("POST", f"/api/admin/users/{bob}/disable", None),
                               ("POST", f"/api/admin/users/{bob}/enable", None),
                               ("DELETE", f"/api/admin/users/{bob}", {"confirmEmail": "bob@example.com"})):
        assert a.request(method, path, json=body).status_code == 403, path
    monkeypatch.setattr(api, "_feature_block", lambda request, user_id: None)   # roles, not tariffs
    token = AUTH.create_api_token(boss, "ops")["token"]
    bare = TestClient(api.app, headers={"X-NF-Client": "tests", "Authorization": f"Bearer {token}"})
    assert bare.post(f"/api/admin/users/{bob}/disable").status_code == 403      # a token: no


def test_an_admin_creates_and_deletes_an_account():
    _people()
    admin, _ = _sign_in("boss@example.com")
    made = admin.post("/api/admin/users", json={"email": "New@Example.com"}).json()
    assert made["email"] == "new@example.com" and len(made["password"]) >= 10
    assert _signs_in("new@example.com", made["password"])
    assert admin.request("DELETE", f"/api/admin/users/{made['id']}",
                         json={"confirmEmail": "wrong@example.com"}).status_code == 400
    assert admin.request("DELETE", f"/api/admin/users/{made['id']}",
                         json={"confirmEmail": "new@example.com"}).json()["deleted"] == made["id"]
    assert admin.post(f"/api/admin/users/{made['id']}/disable").status_code == 404


# ------------------------------------------------------------ CLI

def test_cli_blocks_unblocks_and_deletes(monkeypatch, capsys):
    import interfaces.cli.cli as cli
    _boss, ann, _bob = _people()

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["cli.py", *argv])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        return exc.value.code, capsys.readouterr()

    def disabled():
        return {u["id"]: u["disabled"] for u in AUTH.list_users()}[ann]

    assert run("disable-user", "ann@example.com")[0] == 0 and disabled() is True
    assert run("enable-user", "ann@example.com")[0] == 0 and disabled() is False
    code, out = run("delete-user", "ann@example.com")
    assert code == 1 and "--yes" in out.err                 # not without confirming
    assert run("delete-user", "ann@example.com", "--yes")[0] == 0
    assert AUTH.user_id_for_email("ann@example.com") is None
    assert run("disable-user", "nobody@example.com")[0] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

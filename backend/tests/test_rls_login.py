"""Multi-user plan P1-02, хвост: с POSTGRES_APP_PASSWORD запросы пользователей
идут отдельным логином Postgres (NF_DB_APP_ROLE), а не SET ROLE от логина
владельца. init_db сам делает роль логином с паролем из env. Под этим логином
запрос без `AND user_id` видит только своё, и никакой SQL этого не обходит:
ни RESET ROLE, ни SET ROLE владельца, ни свой app.user_id через set_config,
ни подпись, унесённая из другого соединения; секрет подписи не читается.
Вход, регистрация, админские маршруты и system_scope работают ролью
владельца. Неверный пароль -- откат на SET ROLE с предупреждением.

Роль своя на прогон (по имени схемы процесса) и удаляется в конце: база
тестов общая с параллельными прогонами, общую роль <POSTGRES_USER>_app этот
файл не трогает. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_rls_login.py
"""
import os
import re
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import psycopg2  # noqa: E402
import psycopg2.errors  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import library as L  # noqa: E402
from application import llm_gateway as gw  # noqa: E402
from infrastructure import mailer  # noqa: E402
from infrastructure.postgres import connection as C  # noqa: E402

ROLE = f"nf_{schema_scope.SCHEMA}_login"[:60]
APP_PW = secrets.token_hex(16)
A, B = 1, 900012
PW = "correct horse battery"
OWNER = psycopg2.extensions.parse_dsn(C._dsn()).get("user")


def _drop_role():
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = ?", (ROLE,)).fetchone():
            conn.execute(f'DROP OWNED BY "{ROLE}"')
            conn.execute(f'DROP ROLE "{ROLE}"')
        conn.commit()
    finally:
        conn.close()


def _may_create_roles():
    conn = db.get_conn()
    try:
        return conn.execute("SELECT rolsuper OR rolcreaterole FROM pg_roles "
                            "WHERE rolname = current_user").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture(autouse=True, scope="module")
def _login_mode():
    if not _may_create_roles():
        pytest.skip("the test database login may not create roles")
    mp = pytest.MonkeyPatch()
    mp.setenv("NF_DB_APP_ROLE", ROLE)
    mp.setenv("POSTGRES_APP_PASSWORD", APP_PW)
    try:
        db.init_db()
        yield
    finally:
        mp.undo()
        C._forget_rls_state()
        _drop_role()


@pytest.fixture(autouse=True)
def _rows():
    conn = db.get_conn()
    for t in ("tracked_channels", "saved_items", "sessions", "llm_reservations", "llm_usage"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("INSERT INTO users (id, email, is_admin, created_at) VALUES (?, ?, 0, ?)",
                 (B, "login-b@example.com", db.now_iso()))
    for uid in (A, B):
        conn.execute("INSERT INTO saved_items (user_id, kind, ref_id, created_at) "
                     "VALUES (?, 'video', ?, ?)", (uid, f"loginvid{uid}", db.now_iso()))
    conn.commit()
    conn.close()
    yield


def _user_conn(uid):
    with db.user_scope(uid):
        return db.get_conn()


def _owners(conn, sql="SELECT user_id FROM saved_items"):
    return sorted(r[0] for r in conn.execute(sql).fetchall())


# --------------------------------------------------------- логин и его права

def test_init_db_made_the_role_a_login_that_cannot_bypass_rls():
    assert C.rls_mode() == "login"
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT rolcanlogin, rolsuper, rolbypassrls FROM pg_roles "
                           "WHERE rolname = ?", (ROLE,)).fetchone()
        assert row["rolcanlogin"] and not row["rolsuper"] and not row["rolbypassrls"]
        assert not conn.execute("SELECT pg_has_role(?, ?, 'MEMBER')", (ROLE, OWNER)).fetchone()[0]
    finally:
        conn.close()


def test_user_requests_are_the_login_itself_not_a_set_role():
    conn = _user_conn(B)
    try:
        row = conn.execute("SELECT current_user, session_user").fetchone()
        assert (row[0], row[1]) == (ROLE, ROLE)
    finally:
        conn.close()
    conn = db.get_conn()   # вне области -- владелец
    try:
        assert conn.execute("SELECT current_user").fetchone()[0] == OWNER
        assert _owners(conn) == [A, B]
    finally:
        conn.close()


def test_a_query_without_user_id_sees_only_your_rows():
    for uid in (A, B):
        conn = _user_conn(uid)
        try:
            assert _owners(conn) == [uid]
        finally:
            conn.close()


def test_reset_role_and_set_role_owner_do_not_escape():
    conn = _user_conn(B)
    try:
        conn.execute("RESET ROLE")
        assert _owners(conn) == [B]
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            conn.execute(f'SET ROLE "{OWNER}"')
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            conn.execute(f'SET SESSION AUTHORIZATION "{OWNER}"')
    finally:
        conn.close()


def test_naming_another_user_in_app_user_id_sees_nothing():
    conn = _user_conn(B)
    try:
        conn.execute("SELECT set_config('app.user_id', ?, false)", (str(A),))
        assert _owners(conn) == []
    finally:
        conn.close()


def test_a_signature_from_another_connection_does_not_work_here():
    """Подпись A видна соседям того же логина в pg_stat_activity; в другом
    соединении (другой pid) она недействительна."""
    a = _user_conn(A)
    b = _user_conn(B)
    try:
        sig = a.execute("SELECT current_setting('app.user_sig')").fetchone()[0]
        assert sig
        b.execute("SELECT set_config('app.user_id', ?, false), set_config('app.user_sig', ?, false)",
                  (str(A), sig))
        assert _owners(b) == []
        assert _owners(a) == [A]
    finally:
        a.close()
        b.close()


def test_the_signing_key_and_writes_for_others_are_refused():
    conn = _user_conn(B)
    try:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            conn.execute("SELECT key FROM nf_rls_key")
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO saved_items (user_id, kind, ref_id, created_at) "
                         "VALUES (?, 'video', 'forged', ?)", (A, db.now_iso()))
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE nf_login_probe (x int)")
    finally:
        conn.close()


def test_a_temp_table_does_not_outlive_the_request():
    """Временная таблица с именем личной не подменит её следующему
    пользователю этого же соединения из пула."""
    conn = _user_conn(B)
    pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
    conn.execute("CREATE TEMP TABLE saved_items (user_id bigint)")
    conn.execute("INSERT INTO pg_temp.saved_items VALUES (?)", (B,))
    conn.commit()
    conn.close()
    conn = _user_conn(A)
    try:
        assert conn.execute("SELECT pg_backend_pid()").fetchone()[0] == pid   # тот же из пула
        assert conn.execute("SELECT to_regclass('pg_temp.saved_items')").fetchone()[0] is None
        assert _owners(conn) == [A]
    finally:
        conn.close()


def test_a_table_created_later_is_granted_to_the_login():
    """Таблица новой версии (другой PR) -- права без ручного GRANT: по
    ALTER DEFAULT PRIVILEGES сразу и повторным GRANT на следующем init_db."""
    conn = db.get_conn()
    conn.execute("CREATE TABLE IF NOT EXISTS nf_later_table (id BIGSERIAL PRIMARY KEY, note TEXT)")
    conn.commit()
    conn.close()
    u = _user_conn(B)
    try:
        u.execute("INSERT INTO nf_later_table (note) VALUES ('x')")
        assert u.execute("SELECT count(*) FROM nf_later_table").fetchone()[0] == 1
        u.commit()
    finally:
        u.close()
    conn = db.get_conn()
    conn.execute(f'REVOKE ALL ON nf_later_table FROM "{ROLE}"')
    conn.commit()
    conn.close()
    db.init_db()
    u = _user_conn(B)
    try:
        assert u.execute("SELECT count(*) FROM nf_later_table").fetchone()[0] == 1
    finally:
        u.close()


def test_system_scope_inside_a_user_request_is_the_owner():
    with db.user_scope(A):
        hold = gw._reserve(None, None, 0.0)
        assert hold is not None
        gw._settle(hold, None, "login-key", "login", True, None)
        with db.system_scope():
            conn = db.get_conn()
        try:
            assert _owners(conn) == [A, B]
        finally:
            conn.close()


# ----------------------------------------------- откат, когда логина нет

def test_a_wrong_password_falls_back_to_set_role_with_a_warning(monkeypatch, caplog):
    monkeypatch.setenv("POSTGRES_APP_PASSWORD", "not-the-password")
    monkeypatch.setattr(C, "_rls_mode", None)
    monkeypatch.setattr(C, "_APP_POOL", None)
    with caplog.at_level("WARNING"):
        assert C.rls_mode() == "role"
    assert "cannot use the database login" in caplog.text
    conn = _user_conn(B)
    try:
        assert conn.execute("SELECT session_user").fetchone()[0] == OWNER
        assert _owners(conn) == [B]
    finally:
        conn.close()


def test_without_the_password_it_is_set_role_as_before(monkeypatch):
    monkeypatch.delenv("POSTGRES_APP_PASSWORD")
    monkeypatch.setattr(C, "_rls_mode", None)
    monkeypatch.setattr(C, "_APP_POOL", None)
    assert C.rls_mode() == "role"
    conn = _user_conn(B)
    try:
        row = conn.execute("SELECT current_user, session_user").fetchone()
        assert (row[0], row[1]) == (ROLE, OWNER)
        assert _owners(conn) == [B]
    finally:
        conn.close()


# -------------------------------------------------------------------- HTTP

@pytest.fixture
def multi_user(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "niche-finder <no-reply@example.test>")
    monkeypatch.setenv("NF_PUBLIC_URL", "https://niches.example.test")
    monkeypatch.delenv("NF_REGISTRATION", raising=False)
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text: sent.append(text))
    return sent


def _leaky_list_items(kind=None, folder=None, limit=200, *, user_id):
    """Как library.list_items, но фильтр по user_id «забыт»."""
    conn = db.get_conn()
    try:
        return [{"userId": r["user_id"], "refId": r["ref_id"]}
                for r in conn.execute("SELECT user_id, ref_id FROM saved_items").fetchall()]
    finally:
        conn.close()


def _client():
    return TestClient(api.app, headers={"X-NF-Client": "tests"})


def test_http_sign_up_sign_in_and_a_leaky_query(multi_user, monkeypatch):
    monkeypatch.setattr(L, "list_items", _leaky_list_items)
    c = _client()
    assert c.post("/api/auth/register", json={"email": "login-new@example.com"}).status_code == 200
    token = re.search(r"#/signup/(\S+)", multi_user[-1]).group(1)
    r = c.post("/api/auth/register/complete", json={"token": token, "password": PW})
    assert r.status_code == 200, r.text
    assert c.get("/api/auth/me").json()["user"]["email"] == "login-new@example.com"
    assert c.get("/api/saved").json()["items"] == []        # чужие строки не видны
    assert c.post("/api/saved", json={"kind": "video", "refId": "loginvidNew"}).status_code == 200
    uid = c.get("/api/auth/me").json()["user"]["id"]
    assert [(i["userId"], i["refId"]) for i in c.get("/api/saved").json()["items"]] == \
        [(uid, "loginvidNew")]
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/saved").status_code == 401
    c2 = _client()
    assert c2.post("/api/auth/login", json={"email": "login-new@example.com",
                                            "password": PW}).status_code == 200
    assert len(c2.get("/api/saved").json()["items"]) == 1


def test_http_admin_routes_work_for_everyone(multi_user):
    AUTH.create_user("login-admin@example.com", PW, is_admin=True)
    c = _client()
    assert c.post("/api/auth/login", json={"email": "login-admin@example.com",
                                           "password": PW}).status_code == 200
    r = c.get("/api/admin/users")
    assert r.status_code == 200, r.text
    emails = {u["email"] for u in r.json()["users"]}
    assert {"login-admin@example.com", "login-b@example.com"} <= emails


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

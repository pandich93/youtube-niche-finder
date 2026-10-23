"""Multi-user plan P1-02: Row-Level Security под условием `AND user_id = ?`.
Каждая личная таблица (PERSONAL_TABLES) под политикой; соединение в области
пользователя (db.user_scope -- её ставят HTTP и MCP) работает ролью без
BYPASSRLS и видит только свои строки даже запросом БЕЗ user_id; роль без
app.user_id не видит ничего; роль владельца (worker, CLI, system_scope) --
всё, как раньше. Через HTTP: запрос пользователя, забывший фильтр, чужого не
получает -- ни с NF_MULTI_USER=1, ни в однопользовательском режиме (user 1).
Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_rls.py
"""
import asyncio
import os
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
from infrastructure.postgres import connection as C  # noqa: E402

A, B = 1, 900002          # B -- далеко от счётчика users: create_user его не займёт
PW = "correct horse battery"
CH = "UC" + "rlsprobe".ljust(22, "0")


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _rows():
    conn = db.get_conn()
    for t in ("tracked_channels", "saved_items", "events", "sessions", "llm_reservations",
              "llm_usage"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("INSERT INTO users (id, email, is_admin, created_at) VALUES (?, ?, 0, ?)",
                 (B, "rls-b@example.com", db.now_iso()))
    for uid in (A, B):
        conn.execute("INSERT INTO tracked_channels (user_id, channel_id, added_at, active) "
                     "VALUES (?, ?, ?, 1)", (uid, f"{CH[:-1]}{uid}", db.now_iso()))
        conn.execute("INSERT INTO saved_items (user_id, kind, ref_id, created_at) "
                     "VALUES (?, 'video', ?, ?)", (uid, f"rlsvid{uid}", db.now_iso()))
        conn.execute("INSERT INTO events (kind, ref_id, created_at, user_id) VALUES (?,?,?,?)",
                     ("topic", f"mine{uid}", db.now_iso(), uid))
    conn.execute("INSERT INTO events (kind, ref_id, created_at) VALUES ('outlier', 'shared', ?)",
                 (db.now_iso(),))
    conn.commit()
    conn.close()
    yield


def _owners(sql, user_id=None):
    if user_id is None:
        conn = db.get_conn()
    else:
        with db.user_scope(user_id):
            conn = db.get_conn()
    try:
        return sorted(r[0] for r in conn.execute(sql).fetchall())
    finally:
        conn.close()


def _raw_app_role(user_id_setting):
    """Голое соединение логина владельца, переключённое в роль приложения --
    как get_conn(), но app.user_id задаём сами (None: не задан вовсе)."""
    raw = psycopg2.connect(C._dsn())
    cur = raw.cursor()
    cur.execute(f'SET search_path TO "{C._schema()}", public')
    cur.execute(f'SET ROLE "{db.app_role()}"')
    if user_id_setting is not None:
        cur.execute("SELECT set_config('app.user_id', %s, false)", (user_id_setting,))
    return raw, cur


# ------------------------------------------------------------- схема и роль

def test_every_personal_table_has_rls_and_the_policy():
    conn = db.get_conn()
    try:
        for table in db.PERSONAL_TABLES:
            row = conn.execute(
                "SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE n.nspname = current_schema() AND c.relname = ?",
                (table,)).fetchone()
            assert row and row[0], f"{table}: ROW LEVEL SECURITY is off"
            assert conn.execute(
                "SELECT 1 FROM pg_policies WHERE schemaname = current_schema() "
                "AND tablename = ? AND policyname = ?", (table, db.RLS_POLICY)).fetchone(), table
    finally:
        conn.close()


def test_the_app_role_cannot_bypass_rls_and_owns_nothing():
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles "
                           "WHERE rolname = ?", (db.app_role(),)).fetchone()
        assert row is not None, "init_db did not create the app role"
        assert not row["rolsuper"] and not row["rolbypassrls"] and not row["rolcanlogin"]
        owned = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND pg_get_userbyid(c.relowner) = ?",
            (db.app_role(),)).fetchone()[0]
        assert owned == 0
    finally:
        conn.close()


def test_init_db_twice_is_quiet():
    db.init_db()
    db.init_db()
    assert _owners("SELECT user_id FROM tracked_channels", A) == [A]


# ------------------------------------------------- запросы без AND user_id

def test_a_query_without_user_id_sees_only_your_rows():
    assert _owners("SELECT user_id FROM tracked_channels", A) == [A]
    assert _owners("SELECT user_id FROM tracked_channels", B) == [B]
    assert _owners("SELECT user_id FROM saved_items", B) == [B]
    # the owner's role (worker, CLI) still sees everyone
    assert _owners("SELECT user_id FROM tracked_channels") == [A, B]


def test_writes_without_user_id_do_not_reach_other_users():
    with db.user_scope(B):
        conn = db.get_conn()
    try:
        assert conn.execute("UPDATE tracked_channels SET note = 'B was here'").rowcount == 1
        assert conn.execute("DELETE FROM saved_items").rowcount == 1
        conn.commit()
    finally:
        conn.close()
    conn = db.get_conn()
    try:
        notes = {r["user_id"]: r["note"] for r in conn.execute(
            "SELECT user_id, note FROM tracked_channels").fetchall()}
        assert notes == {A: None, B: "B was here"}
        assert [r[0] for r in conn.execute("SELECT user_id FROM saved_items").fetchall()] == [A]
    finally:
        conn.close()


def test_inserting_a_row_for_someone_else_is_refused():
    with db.user_scope(B):
        conn = db.get_conn()
    try:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO saved_items (user_id, kind, ref_id, created_at) "
                         "VALUES (?, 'video', 'forged', ?)", (A, db.now_iso()))
    finally:
        conn.close()


def test_events_shared_rows_stay_visible_personal_ones_do_not():
    assert _owners("SELECT ref_id FROM events", B) == [f"mine{B}", "shared"]
    assert _owners("SELECT ref_id FROM events", A) == [f"mine{A}", "shared"]


@pytest.mark.parametrize("setting", [None, ""])
def test_without_app_user_id_personal_tables_are_empty(setting):
    raw, cur = _raw_app_role(setting)
    try:
        for table in ("tracked_channels", "saved_items", "sessions", "llm_usage"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0, table
        cur.execute("SELECT ref_id FROM events")
        assert [r[0] for r in cur.fetchall()] == ["shared"]
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("INSERT INTO saved_items (user_id, kind, ref_id, created_at) "
                        "VALUES (1, 'video', 'x', 'now')")
    finally:
        raw.close()


def test_a_pooled_connection_does_not_keep_the_previous_scope():
    with db.user_scope(B):
        conn = db.get_conn()
        conn.close()
    conn = db.get_conn()
    try:
        assert conn.execute("SELECT current_user = session_user").fetchone()[0]
        assert conn.execute("SELECT current_setting('app.user_id', true)").fetchone()[0] == ""
    finally:
        conn.close()


def test_system_scope_inside_a_user_scope_is_the_owner():
    with db.user_scope(B):
        with db.system_scope():
            conn = db.get_conn()
        try:
            assert sorted(r[0] for r in conn.execute(
                "SELECT user_id FROM tracked_channels").fetchall()) == [A, B]
        finally:
            conn.close()


def test_nf_db_rls_0_runs_user_requests_as_the_owner(monkeypatch):
    monkeypatch.setenv("NF_DB_RLS", "0")
    assert _owners("SELECT user_id FROM tracked_channels", B) == [A, B]


def test_a_missing_role_falls_back_to_the_owner_with_a_warning(monkeypatch, caplog):
    monkeypatch.setenv("NF_DB_APP_ROLE", "nf_rls_no_such_role")
    monkeypatch.setattr(C, "_app_role_ok", None)
    with caplog.at_level("WARNING"):
        assert _owners("SELECT user_id FROM tracked_channels", B) == [A, B]
    assert "Row-Level Security is not in effect" in caplog.text


def test_the_llm_budget_books_system_rows_from_a_user_request():
    """В однопользовательском режиме запрос -- область user 1, а расход
    записывается на SYSTEM_USER_ID: учёт бюджета идёт мимо политик."""
    with db.user_scope(A):
        hold = gw._reserve(None, None, 0.0)
        assert hold is not None
        gw._settle(hold, None, "rls-key", "rls", True, None)
    assert _owners("SELECT user_id FROM llm_reservations") == []


# ---------------------------------------------------------------- HTTP / MCP

def _leaky_list_items(kind=None, folder=None, limit=200, *, user_id):
    """Как library.list_items, но фильтр по user_id «забыт»."""
    conn = db.get_conn()
    try:
        return [{"userId": r["user_id"], "refId": r["ref_id"]}
                for r in conn.execute("SELECT user_id, ref_id FROM saved_items").fetchall()]
    finally:
        conn.close()


def test_http_single_user_request_without_the_filter_sees_only_user_1(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "0")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(L, "list_items", _leaky_list_items)
    items = TestClient(api.app).get("/api/saved").json()["items"]
    assert [i["userId"] for i in items] == [A]


def test_http_signed_in_request_without_the_filter_sees_only_its_user(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(L, "list_items", _leaky_list_items)
    uid = AUTH.create_user("rls-c@example.com", PW)
    conn = db.get_conn()
    conn.execute("INSERT INTO saved_items (user_id, kind, ref_id, created_at) "
                 "VALUES (?, 'video', 'rlsvidC', ?)", (uid, db.now_iso()))
    conn.commit()
    conn.close()
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": "rls-c@example.com",
                                           "password": PW}).status_code == 200
    items = c.get("/api/saved").json()["items"]
    assert [(i["userId"], i["refId"]) for i in items] == [(uid, "rlsvidC")]


def test_mcp_tools_run_in_their_users_scope_admin_tools_as_the_owner():
    from interfaces.mcp import auth as mcp_auth

    class Ctx:
        def __init__(self, method, params):
            self.method, self.params = method, params

    async def call_next(_ctx):
        return db.rls_user()

    def run(ctx):
        return asyncio.run(mcp_auth.rls_middleware(ctx, call_next))

    assert run(Ctx("tools/call", {"name": "list_tracked"})) == 1          # stdio = user 1
    assert run(Ctx("tools/call", {"name": "refresh_stats"})) is None      # MCP_ADMIN_TOOLS
    assert run(Ctx("prompts/list", None)) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

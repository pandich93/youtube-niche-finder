"""Tests for application/auth.py (plan 15, sub-stages 5.1-5.3): users, scrypt
passwords, sessions, and moving existing personal rows to user 1. Throwaway
schema; no network.
Run with pytest, or directly: python3 tests/test_auth.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import auth as A  # noqa: E402
from domain import users as U  # noqa: E402

PW = "correct horse battery"


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _clean():
    conn = db.get_conn()
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("UPDATE users SET password_hash = NULL WHERE id = 1")
    conn.commit()
    conn.close()


# ------------------------------------------------------------ passwords

def test_scrypt_hash_verifies_and_is_salted():
    h1, h2 = A.hash_password(PW), A.hash_password(PW)
    assert h1 != h2 and h1.startswith("scrypt$")
    assert A.verify_password(PW, h1) and not A.verify_password("wrong", h1)
    assert not A.verify_password(PW, None) and not A.verify_password(PW, "garbage")


def test_short_passwords_are_refused():
    with pytest.raises(A.AuthError):
        A.create_user("a@example.com", "short")


# ------------------------------------------------------------ users

def test_the_local_user_exists_and_owns_id_1():
    conn = db.get_conn()
    row = conn.execute("SELECT id, email, is_admin FROM users WHERE id = 1").fetchone()
    conn.close()
    assert row["email"] == "local" and row["is_admin"] == 1
    assert U.LOCAL_USER_ID == 1


def test_create_user_normalises_the_email_and_refuses_duplicates():
    uid = A.create_user("  Ann@Example.COM ", PW)
    assert uid > 1
    with pytest.raises(A.AuthError):
        A.create_user("ann@example.com", PW)
    assert [u["email"] for u in A.list_users()] == ["local", "ann@example.com"]


# ------------------------------------------------------------ sessions

def test_login_gives_a_session_that_resolves_to_the_user():
    uid = A.create_user("ann@example.com", PW)
    s = A.login("ANN@example.com", PW)
    assert s["userId"] == uid and len(s["token"]) >= 40
    assert A.user_for_token(s["token"]) == {"id": uid, "email": "ann@example.com", "isAdmin": False}


def test_the_session_token_is_stored_only_as_a_hash():
    A.create_user("ann@example.com", PW)
    token = A.login("ann@example.com", PW)["token"]
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sessions").fetchall()]
    conn.close()
    assert token not in repr(rows) and len(rows) == 1


def test_wrong_password_and_unknown_email_fail_the_same_way():
    A.create_user("ann@example.com", PW)
    with pytest.raises(A.AuthError) as a:
        A.login("ann@example.com", "wrong password!")
    with pytest.raises(A.AuthError) as b:
        A.login("nobody@example.com", PW)
    assert str(a.value) == str(b.value)


def test_the_local_user_cannot_log_in_until_it_has_a_password():
    with pytest.raises(A.AuthError):
        A.login("local", PW)
    A.set_password("local", PW)
    assert A.login("local", PW)["userId"] == 1


def test_logout_and_expiry_end_the_session():
    A.create_user("ann@example.com", PW)
    token = A.login("ann@example.com", PW)["token"]
    A.logout(token)
    assert A.user_for_token(token) is None
    token = A.login("ann@example.com", PW)["token"]
    conn = db.get_conn()
    conn.execute("UPDATE sessions SET expires_at = ?",
                 ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    conn.commit()
    conn.close()
    assert A.user_for_token(token) is None
    assert A.user_for_token("") is None and A.user_for_token("forged") is None


def test_changing_the_password_ends_existing_sessions():
    A.create_user("ann@example.com", PW)
    token = A.login("ann@example.com", PW)["token"]
    A.set_password("ann@example.com", "another long password")
    assert A.user_for_token(token) is None


# ------------------------------------------------------------ migration

@pytest.mark.parametrize("table", U.PERSONAL_TABLES)
def test_personal_tables_carry_user_id_defaulting_to_the_local_user(table):
    conn = db.get_conn()
    cols = {r["column_name"]: r for r in conn.execute(
        "SELECT column_name, column_default, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ?", (table,)).fetchall()}
    conn.close()
    assert "user_id" in cols, table
    assert cols["user_id"]["is_nullable"] == "NO" and "1" in (cols["user_id"]["column_default"] or "")


def test_migrating_an_old_database_moves_existing_rows_to_user_1():
    conn = db.get_conn()
    conn.execute("ALTER TABLE drafts DROP COLUMN user_id")
    conn.execute("INSERT INTO drafts (title, created_at) VALUES (?, ?)", ("old draft", db.now_iso()))
    conn.commit()
    added = db.migrate(conn)
    conn.commit()
    row = conn.execute("SELECT user_id FROM drafts WHERE title = 'old draft'").fetchone()
    conn.execute("DELETE FROM drafts")
    conn.commit()
    conn.close()
    assert "drafts.user_id" in added and row["user_id"] == 1


def test_multi_user_mode_is_off_by_default(monkeypatch):
    monkeypatch.delenv("NF_MULTI_USER", raising=False)
    assert U.multi_user_enabled() is False
    monkeypatch.setenv("NF_MULTI_USER", "1")
    assert U.multi_user_enabled() is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

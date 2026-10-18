"""Tariff tables (SaaS sub-project 1): init_db creates plans, user_plans and
plan_changes, and seeds exactly one base plan "free" with empty limits and
features (catalog defaults apply). Throwaway schema.
Run with pytest, or directly: python3 tests/test_plans_schema.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db  # noqa: E402


def setup_module(_=None):
    db.init_db()


def test_free_plan_is_seeded_once():
    db.init_db()                                   # a second start adds nothing
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT slug, is_base, is_public, price, period_days, limits, features "
                            "FROM plans").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    r = rows[0]
    assert r["slug"] == "free" and r["is_base"] == 1 and r["is_public"] == 1
    assert float(r["price"]) == 0 and r["period_days"] is None
    assert r["limits"] == {} and r["features"] == {}


def test_only_one_base_plan_is_allowed():
    conn = db.get_conn()
    try:
        try:
            conn.execute("INSERT INTO plans (slug, name, is_base, created_at) VALUES ('x','X',1,?)",
                         (db.now_iso(),))
            raised = False
        except Exception:
            raised = True
        conn.rollback()
    finally:
        conn.close()
    assert raised


def test_user_tables_exist():
    conn = db.get_conn()
    try:
        assert db._existing_columns(conn, "user_plans") >= {
            "user_id", "plan_id", "started_at", "expires_at", "assigned_by"}
        assert db._existing_columns(conn, "plan_changes") >= {
            "user_id", "from_plan_id", "to_plan_id", "expires_at", "changed_by", "changed_at"}
    finally:
        conn.close()


if __name__ == "__main__":
    setup_module()
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")

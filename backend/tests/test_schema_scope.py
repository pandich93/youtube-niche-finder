"""Tests for tests/schema_scope.py's per-file schema (tests/conftest.py wraps
every module that imports schema_scope in module_schema()). Real Postgres.

Run: pytest tests/test_schema_scope.py
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: E402

import infrastructure.postgres as db  # noqa: E402

pytestmark = pytest.mark.skipif(
    not schema_scope.OWN_SCHEMA, reason="NICHE_DB_SCHEMA set from outside: one shared schema")


def _schema_exists(name):
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?", (name,)).fetchone()
        return row is not None
    finally:
        conn.close()


def test_this_file_runs_in_its_own_schema_not_the_process_one():
    current = os.environ["NICHE_DB_SCHEMA"]
    assert current != schema_scope.SCHEMA
    assert current.startswith(schema_scope.SCHEMA + "_m")


def test_module_schema_is_fresh_then_dropped_and_env_restored():
    outer = os.environ["NICHE_DB_SCHEMA"]
    with schema_scope.module_schema() as inner:
        assert inner != outer
        assert os.environ["NICHE_DB_SCHEMA"] == inner
        conn = db.get_conn()  # creates the schema on first use
        try:
            conn.execute("CREATE TABLE probe (x INTEGER)")
            conn.commit()
        finally:
            conn.close()
        assert _schema_exists(inner)
    assert os.environ["NICHE_DB_SCHEMA"] == outer
    assert not _schema_exists(inner)

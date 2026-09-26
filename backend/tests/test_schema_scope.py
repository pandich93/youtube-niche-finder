"""Tests for tests/schema_scope.py's per-file schema (tests/conftest.py wraps
every module that imports schema_scope in module_schema()) and for the other
per-file resets conftest does. Real Postgres.

Run: pytest tests/test_schema_scope.py
"""
import ast
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: E402

import infrastructure.postgres as db  # noqa: E402

own_schema_only = pytest.mark.skipif(
    not schema_scope.OWN_SCHEMA, reason="NICHE_DB_SCHEMA set from outside: one shared schema")


def _schema_exists(name):
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?", (name,)).fetchone()
        return row is not None
    finally:
        conn.close()


@own_schema_only
def test_this_file_runs_in_its_own_schema_not_the_process_one():
    current = os.environ["NICHE_DB_SCHEMA"]
    assert current != schema_scope.SCHEMA
    assert current.startswith(schema_scope.SCHEMA + "_m")


@own_schema_only
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


# conftest finds a Postgres-backed file by its module attribute `schema_scope`.
# `from schema_scope import SCHEMA`, `import schema_scope as ss` or getting it
# through a helper module leaves no such attribute, and the file would quietly
# run in the shared process schema next to every other such file.
_APP_PACKAGES = ("application", "infrastructure.postgres", "interfaces")


def _imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module
            for alias in node.names:
                yield f"{node.module}.{alias.name}"


def _plain_top_level_import(tree):
    return any(isinstance(node, ast.Import)
               and any(a.name == "schema_scope" and a.asname is None for a in node.names)
               for node in tree.body)


def _defines_doubles(tree):
    return any(isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "DOUBLES" for t in node.targets)
               for node in tree.body)


def _misses_schema_scope(tree):
    """Reaches the real database (or mentions schema_scope at all) without the
    one import form conftest can see. Files with DOUBLES run against their own
    doubles (tests/module_doubles.py) and are exempt."""
    if _defines_doubles(tree):
        return False
    mods = set(_imported_modules(tree))
    needs = any(m == p or m.startswith(p + ".")
                for m in mods for p in ("schema_scope",) + _APP_PACKAGES)
    return needs and not _plain_top_level_import(tree)


def test_every_real_database_file_imports_schema_scope_plainly():
    offenders = []
    for fname in sorted(os.listdir(HERE)):
        if not (fname.startswith("test_") and fname.endswith(".py")):
            continue
        with open(os.path.join(HERE, fname), encoding="utf-8") as fh:
            if _misses_schema_scope(ast.parse(fh.read(), fname)):
                offenders.append(fname)
    assert not offenders, (
        "these files reach the database but lack a top-level `import schema_scope` "
        "(exactly that form -- conftest looks for the module attribute):\n"
        + "\n".join(offenders))


def test_guard_flags_the_import_forms_conftest_cannot_see():
    def flagged(src):
        return _misses_schema_scope(ast.parse(src))

    assert flagged("from schema_scope import SCHEMA")
    assert flagged("import schema_scope as ss")
    assert flagged("import application.library as L")
    assert flagged("from infrastructure import postgres")
    assert not flagged("import schema_scope\nimport application.library as L")
    assert not flagged("import infrastructure.youtube.rss as rss")
    assert not flagged("import application.alerts\nDOUBLES = object()")


def test_conftest_resets_process_wide_caches_between_files():
    """Separate processes used to reset these for free; one process must reset
    them after each file, or a maturity curve read from one file's schema is
    still in force (for up to CACHE_TTL_SEC) in the next file."""
    import conftest
    import application.maturity_curve as MC
    from domain import metrics as M

    try:
        M.set_maturity_curve({0: 0.5, 30: 1.0})
        MC._loaded_at = time.monotonic()
        conftest.reset_process_caches()
        assert MC._loaded_at is None
        assert M.active_maturity_curve() is M.MATURITY_CURVE
    finally:
        MC.reset_cache()
        M.set_maturity_curve(None)

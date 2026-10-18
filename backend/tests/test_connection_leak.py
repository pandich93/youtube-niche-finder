"""A connection whose caller raised before close() must not stay "idle in
transaction" forever. On 2026-10-18 collect_trending hit an SSL error after
inserting a chart snapshot; the pooled connection was never rolled back or
returned, held its locks for 13 hours and starved Postgres of connections.
Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_connection_leak.py
"""
import gc
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import psycopg2.extensions as ext  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from infrastructure.postgres import connection as C  # noqa: E402


def setup_module(_=None):
    db.init_db()


def _leaky():
    """A call site in the style of collect_trending: get_conn(), write, then
    an exception before conn.close()."""
    conn = db.get_conn()
    conn.execute("INSERT INTO meta (key, value) VALUES ('leak_probe', '1') "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value")
    _leaky.raw = conn._conn
    raise RuntimeError("network died mid-collection")


def test_a_dropped_connection_is_rolled_back_and_returned():
    try:
        _leaky()
    except RuntimeError:
        pass
    # no gc.collect(): refcounting alone must return it, as in the worker
    raw = _leaky.raw
    idle = raw.closed or raw.get_transaction_status() == ext.TRANSACTION_STATUS_IDLE
    returned = all(c is not raw for c in C._pool()._used.values())
    if not idle:            # clean up the leak so the schema teardown can't hang
        raw.rollback()
    if not returned:
        C._pool().putconn(raw)
    assert idle, "the dropped connection stayed in a transaction"
    assert returned, "the dropped connection was never given back to the pool"
    conn = db.get_conn()
    try:                                   # the write was rolled back, not kept
        assert conn.execute("SELECT 1 FROM meta WHERE key = 'leak_probe'").fetchone() is None
    finally:
        conn.close()


def test_close_twice_is_harmless():
    conn = db.get_conn()
    conn.close()
    conn.close()
    del conn
    gc.collect()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

"""Connection plumbing and the sqlite3-lookalike shim on top of psycopg2.

Connection is configured with either a single NICHE_DATABASE_URL (a full
postgresql:// DSN) or the individual POSTGRES_HOST / POSTGRES_PORT /
POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD variables (used by the
Docker image, which talks to the `postgres` compose service). An optional
NICHE_DB_SCHEMA (default "public") lets tests and multiple deployments share
one Postgres instance without colliding.

Everything above this layer (application/*, interfaces/*) was written
against sqlite3's connection API: `conn.execute(sql, params)` with '?'
placeholders, rows that support both `row["col"]` and `row[0]`, and
`dict(row)`. Rather than touch every call site, `get_conn()` returns a thin
wrapper that provides exactly that surface on top of psycopg2, translating
'?' -> '%s' and wrapping rows in a small sqlite3.Row lookalike. This is the
one module that knows it is talking to Postgres at the connection level.
"""
import os
import re

import psycopg2
import psycopg2.extensions
import psycopg2.pool

_SCHEMA_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _dsn() -> str:
    url = os.environ.get("NICHE_DATABASE_URL")
    if url:
        return url
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    dbname = os.environ.get("POSTGRES_DB", "niches")
    user = os.environ.get("POSTGRES_USER", "niches")
    password = os.environ.get("POSTGRES_PASSWORD", "niches")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def _schema() -> str:
    name = os.environ.get("NICHE_DB_SCHEMA", "public")
    if not _SCHEMA_NAME_RE.match(name):
        raise ValueError(f"bad NICHE_DB_SCHEMA {name!r}: must be a plain identifier")
    return name


def display_dsn() -> str:
    """Connection info safe to print/return from an API -- no password."""
    dsn = _dsn()
    try:
        parsed = psycopg2.extensions.parse_dsn(dsn)
        return (f"postgresql://{parsed.get('user')}@{parsed.get('host')}:"
                f"{parsed.get('port', 5432)}/{parsed.get('dbname')}"
                f"?schema={_schema()}")
    except Exception:
        return f"postgres (schema={_schema()})"


# ------------------------------------------------------ sqlite3-lookalike shim

class Row:
    """Stand-in for sqlite3.Row: supports row[0], row["col"], dict(row), iteration."""
    __slots__ = ("_values", "_index")

    def __init__(self, values, index):
        self._values = values
        self._index = index  # dict: col_name -> position

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def keys(self):
        return list(self._index.keys())

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return repr(dict(zip(self._index.keys(), self._values)))


class _CursorResult:
    """Wraps a real psycopg2 cursor; fetch* return Row objects instead of tuples."""
    __slots__ = ("_cur", "_index")

    def __init__(self, cur):
        self._cur = cur
        self._index = ({d[0]: i for i, d in enumerate(cur.description)}
                       if cur.description else None)

    def fetchone(self):
        row = self._cur.fetchone()
        return Row(row, self._index) if row is not None else None

    def fetchall(self):
        return [Row(r, self._index) for r in self._cur.fetchall()]

    def fetchmany(self, size=None):
        rows = self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()
        return [Row(r, self._index) for r in rows]

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        # Postgres has no rowid; call sites that need a new id use
        # "RETURNING <col>" and read it with .fetchone() instead.
        raise AttributeError(
            "Postgres has no lastrowid -- use INSERT ... RETURNING <col> and fetchone()")


_POOL = None


def _pool():
    global _POOL
    if _POOL is None:
        _POOL = psycopg2.pool.ThreadedConnectionPool(1, 20, _dsn())
    return _POOL


class _PGConn:
    """Sqlite3.Connection-lookalike backed by a pooled psycopg2 connection."""
    __slots__ = ("_conn", "_returned")

    def __init__(self, conn):
        self._conn = conn
        self._returned = False

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), params if params else None)
        return _CursorResult(cur)

    def executescript(self, sql):
        cur = self._conn.cursor()
        cur.execute(sql)
        cur.close()

    def executemany(self, sql, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(sql.replace("?", "%s"), list(seq_of_params))
        cur.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        # Hand the real connection back to the pool instead of closing the
        # socket -- every call site treats get_conn()/close() as cheap, the
        # way it was with sqlite3, so the pooling stays invisible to them.
        # Only once: a second putconn of the same connection raises.
        if self._returned:
            return
        self._returned = True
        try:
            if self._conn.closed == 0:
                self._conn.rollback()  # drop any uncommitted work, matches
                                       # sqlite3's per-call-site discipline
        except Exception:
            pass
        _pool().putconn(self._conn)

    def __del__(self):
        # Many call sites do get_conn() ... close() with no try/finally. When
        # one of them raises in between (a network error mid-collection, a
        # deadlock), the wrapper is dropped without close() and the pool would
        # keep the connection checked out and "idle in transaction" for good,
        # holding its locks -- on 2026-10-18 that blocked every container's
        # start for 13 hours. Rolling back and returning it here closes that.
        try:
            self.close()
        except Exception:
            pass


_LOCKS = {}


def hold_lock(name: str) -> bool:
    """Take a session-level Postgres advisory lock named name (per schema, so
    test schemas and other installs on the same database do not collide) and
    keep it while this process lives, on its own connection outside the pool.
    False when another process holds it. If this process dies, its connection
    goes and Postgres releases the lock (multi-user plan P1-11)."""
    if name in _LOCKS:
        return True
    raw = psycopg2.connect(_dsn())
    raw.autocommit = True
    cur = raw.cursor()
    cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (f"{name}:{_schema()}",))
    got = bool(cur.fetchone()[0])
    cur.close()
    if not got:
        raw.close()
        return False
    _LOCKS[name] = raw
    return True


def release_lock(name: str):
    raw = _LOCKS.pop(name, None)
    if raw is not None:
        raw.close()


def get_conn() -> _PGConn:
    raw = _pool().getconn()
    schema = _schema()
    cur = raw.cursor()
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    cur.execute(f'SET search_path TO "{schema}", public')
    cur.close()
    raw.commit()
    return _PGConn(raw)

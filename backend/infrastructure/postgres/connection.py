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
import contextlib
import logging
import os
import re
from contextvars import ContextVar

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


def lock_until_commit(conn, name: str, n: int):
    """Take a Postgres advisory lock on (name, n) inside conn's transaction:
    another caller with the same pair waits until this one commits or rolls
    back. Per schema, like hold_lock (multi-user plan P1-10)."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(?), ?)", (f"{name}:{_schema()}", int(n)))


# ------------------------------------------------ Row-Level Security (P1-02)
#
# Изоляция пользователей держится на `AND user_id = ?` в каждом запросе;
# RLS -- вторая стена на случай, когда это условие забыли. Личные таблицы
# (schema.PERSONAL_TABLES) видят только строки, у которых user_id совпадает с
# настройкой сессии app.user_id; без неё -- ни одной строки (schema._ensure_rls).
#
# Под политики попадает только соединение, взятое внутри области пользователя
# (user_scope / set_rls_user): HTTP ставит её на запрос (session_guard), MCP --
# на вызов (interfaces/mcp/auth.py). Такое соединение переключается в роль
# NF_DB_APP_ROLE (без входа, без BYPASSRLS, не владелец таблиц) и получает
# app.user_id. Всё остальное -- worker, CLI, миграции, вход и выход, админские
# маршруты (system_scope) -- работает ролью владельца, которая RLS обходит:
# это работа сразу по всем пользователям, и user_id там передаётся явно.
#
# Роль включается SET ROLE от того же логина, что и раньше: ни нового пароля,
# ни правки docker-compose. Настройки ставятся на сессию, а не SET LOCAL:
# многие места делают commit() и продолжают на том же соединении, а SET LOCAL
# закончился бы вместе с первой транзакцией. Каждый get_conn() выставляет их
# заново, поэтому чужая область не переживает возврат соединения в пул.

_RLS_USER = ContextVar("nf_rls_user", default=None)
_log = logging.getLogger(__name__)
_app_role_ok = None   # None = ещё не проверяли в этом процессе


def rls_enabled() -> bool:
    return os.environ.get("NF_DB_RLS", "1").strip().lower() not in ("0", "false", "no", "off")


def app_role() -> str:
    """Роль, под которой работают запросы пользователей. По умолчанию --
    <логин владельца>_app, чтобы две установки на одном сервере не делили её."""
    name = os.environ.get("NF_DB_APP_ROLE", "").strip()
    if not name:
        try:
            owner = psycopg2.extensions.parse_dsn(_dsn()).get("user") or "niches"
        except Exception:
            owner = "niches"
        name = f"{owner}_app"
    if not _SCHEMA_NAME_RE.match(name):
        raise ValueError(f"bad NF_DB_APP_ROLE {name!r}: must be a plain identifier")
    return name


def set_rls_user(user_id):
    """Соединения, взятые дальше в этом контексте, видят только личные строки
    user_id (None -- снова роль владельца). Возвращает токен для reset_rls_user."""
    return _RLS_USER.set(None if user_id is None else int(user_id))


def reset_rls_user(token):
    _RLS_USER.reset(token)


def rls_user():
    return _RLS_USER.get()


@contextlib.contextmanager
def user_scope(user_id):
    token = set_rls_user(user_id)
    try:
        yield
    finally:
        reset_rls_user(token)


@contextlib.contextmanager
def system_scope():
    """Работа за всех пользователей посреди запроса одного из них: общий
    бюджет LLM, рассылка готового транскрипта всем, кто его ждал. Соединение,
    взятое внутри, -- роль владельца, без политик; user_id -- явно в SQL."""
    token = set_rls_user(None)
    try:
        yield
    finally:
        reset_rls_user(token)


def recheck_app_role(conn):
    """После init_db: роль могла только что появиться -- проверить заново
    сразу, чтобы предупреждение о её отсутствии было в логе старта."""
    global _app_role_ok
    _app_role_ok = None
    if rls_enabled():
        _use_app_role(conn._conn)


def _check_app_role(raw) -> bool:
    """Можно ли этому логину стать app_role(), и не обходит ли она RLS сама.
    Нет -- запросы пользователей идут ролью владельца, как до P1-02, а в лог
    уходит одно предупреждение (docs/hosting.md, «Row-Level Security»)."""
    role = app_role()
    cur = raw.cursor()
    try:
        cur.execute("SELECT r.rolsuper OR r.rolbypassrls, pg_has_role(current_user, r.oid, 'MEMBER') "
                    "FROM pg_roles r WHERE r.rolname = %s", (role,))
        row = cur.fetchone()
    finally:
        cur.close()
        raw.rollback()
    if row is None:
        problem = "does not exist"
    elif row[0]:
        problem = "is a superuser or has BYPASSRLS"
    elif not row[1]:
        problem = "is not granted to the database login"
    else:
        return True
    _log.warning("Row-Level Security is not in effect: database role %s %s -- user requests "
                 "run as the table owner (see docs/hosting.md, Row-Level Security)", role, problem)
    return False


def _use_app_role(raw) -> bool:
    global _app_role_ok
    if not rls_enabled():
        return False
    if _app_role_ok is None:
        _app_role_ok = _check_app_role(raw)
    return _app_role_ok


def get_conn() -> _PGConn:
    raw = _pool().getconn()
    schema = _schema()
    uid = _RLS_USER.get()
    role = app_role() if uid is not None and _use_app_role(raw) else None
    cur = raw.cursor()
    # RESET ROLE первым: схему создаёт владелец, и прошлая область соединения
    # из пула не должна дожить до этого get_conn()
    cur.execute("RESET ROLE")
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    cur.execute(f'SET search_path TO "{schema}", public')
    if role:
        cur.execute(f'SET ROLE "{role}"')
        cur.execute("SELECT set_config('app.user_id', %s, false)", (str(uid),))
    else:
        cur.execute("SELECT set_config('app.user_id', '', false)")
    cur.close()
    raw.commit()
    return _PGConn(raw)

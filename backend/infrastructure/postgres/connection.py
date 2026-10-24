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
import hashlib
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
    __slots__ = ("_conn", "_returned", "_from")

    def __init__(self, conn, pool=None):
        self._conn = conn
        self._returned = False
        self._from = pool  # None = пул владельца; иначе пул логина приложения (P1-02)

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
        pool = self._from or _pool()
        if pool.closed:
            # пул логина приложения закрыли (recheck_app_role после init_db),
            # пока соединение было на руках -- просто закрываем его
            self._conn.close()
            return
        pool.putconn(self._conn)

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
# (schema.PERSONAL_TABLES) видят только строки пользователя, которого назвала
# сессия: app.user_id и подпись к нему app.user_sig (schema._ensure_rls). Без
# них, или с подписью не к этому номеру, -- ни одной строки.
#
# Под политики попадает только соединение, взятое внутри области пользователя
# (user_scope / set_rls_user): HTTP ставит её на запрос (session_guard), MCP --
# на вызов (interfaces/mcp/auth.py). Всё остальное -- worker, CLI, миграции,
# вход и выход, админские маршруты (system_scope) -- работает ролью владельца,
# которая RLS обходит: это работа сразу по всем пользователям, и user_id там
# передаётся явно.
#
# Как соединение области становится ролью приложения app_role() (rls_mode()):
#   * "login" -- задан POSTGRES_APP_PASSWORD: отдельный пул входит в Postgres
#     логином app_role(). Он не владелец, без BYPASSRLS и не член роли
#     владельца, поэтому никакой SQL в нём (RESET ROLE, SET ROLE владельца) из-
#     под политик не выводит. Подделать app.user_id тоже нельзя: подпись --
#     SHA-256 от секрета из nf_rls_key (его читает только владелец), номера и
#     pid этого соединения в Postgres (rls_signature).
#   * "role" -- пароля нет: как до этого, SET ROLE от логина владельца. От
#     забытого фильтра защищает, от произвольного SQL -- нет (RESET ROLE); в
#     лог уходит предупреждение (docs/hosting.md, «Row-Level Security»).
#   * "" -- роли нет или RLS выключен (NF_DB_RLS=0): ролью владельца.
#
# Настройки ставятся на сессию, а не SET LOCAL: многие места делают commit()
# и продолжают на том же соединении, а SET LOCAL закончился бы вместе с первой
# транзакцией. Каждый get_conn() выставляет их заново, поэтому чужая область
# не переживает возврат соединения в пул.

_RLS_USER = ContextVar("nf_rls_user", default=None)
_log = logging.getLogger(__name__)
_rls_mode = None      # None = ещё не выбирали в этом процессе; иначе "login" | "role" | ""
_RLS_KEYS = {}        # схема -> секрет подписи из её nf_rls_key
_APP_POOL = None      # пул логина приложения (режим "login")


def rls_enabled() -> bool:
    return os.environ.get("NF_DB_RLS", "1").strip().lower() not in ("0", "false", "no", "off")


def app_role() -> str:
    """Роль, под которой работают запросы пользователей. По умолчанию --
    <логин владельца>_app, чтобы две установки на одном сервере не делили её.
    С POSTGRES_APP_PASSWORD это ещё и логин, которым входят web и MCP."""
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


def app_password() -> str:
    """Пароль логина приложения; пусто -- режима "login" нет."""
    pw = os.environ.get("POSTGRES_APP_PASSWORD", "")
    return pw if pw.strip() else ""


def _app_dsn() -> str:
    """DSN владельца, но с логином app_role() и его паролем."""
    return psycopg2.extensions.make_dsn(_dsn(), user=app_role(), password=app_password())


def rls_signature(key: str, user_id, backend_pid: int) -> str:
    """Подпись app.user_id для соединения с этим pid; та же формула -- в
    schema.nf_rls_uid(). Секрет с обеих сторон номера, поэтому продление
    SHA-256 не помогает, а pid не даёт унести подпись в другое соединение
    (текст запроса видно в pg_stat_activity соседям того же логина)."""
    return hashlib.sha256(f"{key}:{int(user_id)}:{int(backend_pid)}:{key}".encode()).hexdigest()


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


def _forget_rls_state():
    global _rls_mode, _APP_POOL
    _rls_mode = None
    _RLS_KEYS.clear()
    if _APP_POOL is not None:
        pool, _APP_POOL = _APP_POOL, None
        try:
            pool.closeall()
        except Exception:
            pass


def recheck_app_role(conn=None):
    """После init_db: роль, логин и секрет могли только что появиться --
    выбрать режим заново сразу, чтобы предупреждение было в логе старта."""
    _forget_rls_state()
    if rls_enabled():
        rls_mode()


def rls_mode() -> str:
    """"login", "role" или "" (комментарий выше). Выбирается один раз на
    процесс; recheck_app_role -- заново."""
    global _rls_mode
    if not rls_enabled():
        return ""
    if _rls_mode is None:
        _rls_mode = _choose_mode()
    return _rls_mode


def _choose_mode() -> str:
    if app_password():
        problem = _check_app_login()
        if problem is None:
            return "login"
        _log.warning("Row-Level Security: cannot use the database login %s (%s) -- falling back "
                     "to SET ROLE from the owner's login (see docs/hosting.md, Row-Level "
                     "Security)", app_role(), problem)
    raw = _pool().getconn()
    try:
        ok = _check_app_role(raw)
    finally:
        _pool().putconn(raw)
    if not ok:
        return ""
    if not app_password():
        _log.warning("Row-Level Security runs with SET ROLE from the owner's login: it catches "
                     "a forgotten user_id filter, not arbitrary SQL. Set POSTGRES_APP_PASSWORD "
                     "to give user requests a database login of their own (see docs/hosting.md, "
                     "Row-Level Security)")
    return "role"


def _check_app_login():
    """None -- логином app_role() можно войти, и сам он RLS не обойдёт: не
    суперпользователь, без BYPASSRLS, не владелец таблиц этой схемы и не член
    таких ролей. Иначе -- что не так (для предупреждения)."""
    try:
        raw = psycopg2.connect(_app_dsn(), connect_timeout=10)
    except Exception as e:
        return "cannot sign in: " + (str(e).strip().splitlines() or ["?"])[0]
    try:
        cur = raw.cursor()
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_roles r WHERE pg_has_role(current_user, r.oid, 'MEMBER') "
            "AND (r.rolsuper OR r.rolbypassrls OR r.oid IN (SELECT c.relowner FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s)))", (_schema(),))
        if cur.fetchone()[0]:
            return "it is a superuser, has BYPASSRLS or owns the tables, itself or through a role"
        return None
    except Exception as e:
        return (str(e).strip().splitlines() or ["?"])[0]
    finally:
        raw.close()


def _check_app_role(raw) -> bool:
    """Можно ли этому логину стать app_role(), и не обходит ли она RLS сама.
    Нет -- запросы пользователей идут ролью владельца, как до P1-02, а в лог
    уходит одно предупреждение (docs/hosting.md, «Row-Level Security»)."""
    role = app_role()
    cur = raw.cursor()
    try:
        cur.execute("RESET ROLE")
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


def _rls_key(owner_cur=None):
    """Секрет подписи этой схемы (nf_rls_key, его создаёт init_db). Читает
    только владелец: курсором владельца, если он уже есть, иначе соединением
    из пула владельца. None -- таблицы ещё нет (старая схема до init_db)."""
    schema = _schema()
    key = _RLS_KEYS.get(schema)
    if key:
        return key
    raw, cur = None, owner_cur
    if cur is None:
        raw = _pool().getconn()
        cur = raw.cursor()
    try:
        if raw is not None:
            cur.execute("RESET ROLE")
        cur.execute("SELECT to_regclass(%s)", (f'"{schema}".nf_rls_key',))
        if cur.fetchone()[0] is not None:
            cur.execute(f'SELECT key FROM "{schema}".nf_rls_key WHERE id = 1')
            row = cur.fetchone()
            key = row[0] if row else None
    finally:
        if raw is not None:
            cur.close()
            raw.rollback()
            _pool().putconn(raw)
    if key:
        _RLS_KEYS[schema] = key
    return key


def _app_pool():
    global _APP_POOL
    if _APP_POOL is None:
        _APP_POOL = psycopg2.pool.ThreadedConnectionPool(1, 20, _app_dsn())
    return _APP_POOL


def _set_user(cur, uid, key, pid):
    sig = rls_signature(key, uid, pid) if key else ""
    cur.execute("SELECT set_config('app.user_id', %s, false), set_config('app.user_sig', %s, false)",
                (str(uid), sig))


def _app_login_conn(uid) -> _PGConn:
    """Соединение логина приложения для пользователя uid (режим "login")."""
    key = _rls_key()
    pool = _app_pool()
    raw = pool.getconn()
    cur = raw.cursor()
    # DISCARD TEMP и pg_temp последним в search_path: временная таблица,
    # созданная прошлым запросом этого соединения, не подменит настоящую у
    # следующего. Схему не создаём -- у логина нет на это прав.
    cur.execute("DISCARD TEMP")
    cur.execute(f'SET search_path TO "{_schema()}", public, pg_temp')
    _set_user(cur, uid, key, raw.get_backend_pid())
    cur.close()
    raw.commit()
    return _PGConn(raw, pool)


def get_conn() -> _PGConn:
    uid = _RLS_USER.get()
    mode = rls_mode() if uid is not None else ""
    if mode == "login":
        return _app_login_conn(uid)
    raw = _pool().getconn()
    schema = _schema()
    cur = raw.cursor()
    # RESET ROLE первым: схему создаёт владелец, и прошлая область соединения
    # из пула не должна дожить до этого get_conn()
    cur.execute("RESET ROLE")
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    cur.execute(f'SET search_path TO "{schema}", public')
    if mode == "role":
        key = _rls_key(cur)
        cur.execute(f'SET ROLE "{app_role()}"')
        _set_user(cur, uid, key, raw.get_backend_pid())
    else:
        cur.execute("SELECT set_config('app.user_id', '', false), set_config('app.user_sig', '', false)")
    cur.close()
    raw.commit()
    return _PGConn(raw)

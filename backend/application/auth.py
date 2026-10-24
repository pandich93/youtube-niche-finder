"""Users and sign-in (plan 15, used when NF_MULTI_USER=1).

An admin creates accounts from the command line (`cli.py create-user`); with
SMTP configured people can also sign up themselves by an emailed link
(application/signup.py, multi-user plan P1-06). Passwords are hashed
with scrypt from the standard library (a per-password random salt, constant-
time comparison). A session is a random token in an HttpOnly cookie; the
database keeps only its SHA-256, so a leaked database does not leak live
sessions. Wrong password and unknown email fail with the same message.
"""
import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from application import audit as AUD
from application import plans as PL
from domain import login_throttle as LT
from domain import password_policy as PP

MIN_PASSWORD_LEN = PP.MIN_LENGTH


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name) or default))
    except ValueError:
        return default


# multi-user plan P1-04: a session lives SESSION_IDLE_DAYS after the last
# activity (extended at most once per SESSION_TOUCH_MINUTES, so a request does
# not write to the database), and never longer than SESSION_MAX_DAYS after the
# sign-in -- a stolen cookie that keeps being used still dies, and the person
# types the password again about once a quarter.
SESSION_IDLE_DAYS = _env_int("NF_SESSION_IDLE_DAYS", 14)
SESSION_MAX_DAYS = _env_int("NF_SESSION_MAX_DAYS", 90)
SESSION_TOUCH_MINUTES = _env_int("NF_SESSION_TOUCH_MINUTES", 15)
_USER_AGENT_MAX = 200
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32
_FAIL = "wrong email or password"
# Verified against when the email is unknown, so both failures take the same time.
_DUMMY_HASH = None


class AuthError(ValueError):
    pass


class LoginThrottled(AuthError):
    """Too many failed sign-ins for this email or from this address; the same
    for a known and an unknown email (multi-user plan P0-03)."""

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"too many failed sign-ins; try again in {retry_after} s")


def _b64(b):
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored) -> bool:
    try:
        algo, n, r, p, salt, dk = (stored or "").split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                             p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _check_password(password, email: str = None):
    problem = PP.problem(password, email)
    if problem:
        raise AuthError(problem)


def create_user(email: str, password: str, is_admin: bool = False) -> int:
    email = _norm(email)
    if not email:
        raise AuthError("email is required")
    _check_password(password, email)
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AuthError(f"user {email} already exists")
        row = conn.execute("INSERT INTO users (email, password_hash, is_admin, created_at) "
                           "VALUES (?,?,?,?) RETURNING id",
                           (email, hash_password(password), 1 if is_admin else 0, db.now_iso())).fetchone()
        conn.commit()
        return row["id"]
    finally:
        conn.close()


def set_password(email: str, password: str) -> int:
    """Set or change a password; ends every session and revokes every API
    token of that user."""
    _check_password(password, email)
    conn = db.get_conn()
    try:
        row = conn.execute("UPDATE users SET password_hash = ? WHERE email = ? RETURNING id",
                           (hash_password(password), _norm(email))).fetchone()
        if not row:
            raise AuthError(f"no user {_norm(email)}")
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        # a password change is often a reaction to a leak: API tokens go too
        conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (row["id"],))
        conn.commit()
        return row["id"]
    finally:
        conn.close()


def list_users() -> list:
    conn = db.get_conn()
    try:
        return [{"id": r["id"], "email": r["email"], "isAdmin": bool(r["is_admin"]),
                 "hasPassword": r["password_hash"] is not None, "createdAt": r["created_at"],
                 "disabled": r["disabled_at"] is not None}
                for r in conn.execute("SELECT * FROM users ORDER BY id").fetchall()]
    finally:
        conn.close()


def user_id_for_email(email: str):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (_norm(email),)).fetchone()
    finally:
        conn.close()
    return row["id"] if row else None


def set_disabled(user_id: int, disabled: bool) -> bool:
    """Block or unblock an account (multi-user plan P0-04). Blocking ends
    every session and revokes every API token at once, like a password
    change; unblocking lets the person sign in again. False: no such user."""
    conn = db.get_conn()
    try:
        row = conn.execute("UPDATE users SET disabled_at = ? WHERE id = ? RETURNING id",
                           (db.now_iso() if disabled else None, user_id)).fetchone()
        if row and disabled:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return row is not None


def is_admin(user_id: int) -> bool:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return bool(row and row["is_admin"])


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _wait(conn, kind: str, key: str, now: datetime) -> int:
    failures, last = db.login_failures(conn, kind, key)
    last = datetime.fromisoformat(last) if last else None
    wait = LT.email_wait if kind == "email" else LT.ip_wait
    return wait(failures, last, now)


def _failed(conn, keys, now: datetime):
    stamp = now.isoformat()
    for kind, key in keys:
        db.record_login_failure(conn, kind, key, stamp, (now - LT.WINDOW).isoformat())
    db.purge_login_failures(conn, (now - timedelta(days=1)).isoformat())
    conn.commit()


def login(email: str, password: str, client_ip: str = None) -> dict:
    """A session for the right password -- or, with two-factor sign-in on
    (P2-04), {mfaRequired, challenge, expiresAt} for the second step
    (application/two_factor.complete_login). Failed attempts are counted per
    email and per client address (multi-user plan P0-03): past the limits
    LoginThrottled says how long to wait -- before the password is even
    checked, so guessing cannot go on, and alike for a known and an unknown
    email. A success clears the email's counter."""
    global _DUMMY_HASH
    now = datetime.now(timezone.utc)
    keys = [("email", _norm(email))] + ([("ip", client_ip)] if client_ip else [])
    conn = db.get_conn()
    try:
        wait = max(_wait(conn, kind, key, now) for kind, key in keys)
        # a blocked account fails like a wrong password (multi-user plan P0-04)
        row = conn.execute("SELECT id, password_hash FROM users WHERE email = ? "
                           "AND disabled_at IS NULL", (_norm(email),)).fetchone()
        # P1-07: a failure names the account only when it exists -- an unknown
        # "email" is not written down (it may be a password typed in the wrong box)
        target = row["id"] if row else None
        if wait:
            AUD.record(conn, "login_failed", target_id=target, ip=client_ip,
                       details={"reason": "throttled"})
            conn.commit()
            raise LoginThrottled(wait)
        if not row or not row["password_hash"]:
            _DUMMY_HASH = _DUMMY_HASH or hash_password(secrets.token_hex(8))
            verify_password(password or "", _DUMMY_HASH)
            AUD.record(conn, "login_failed", target_id=target, ip=client_ip,
                       details={"reason": "no password" if row else "unknown or blocked"})
            _failed(conn, keys, now)
            raise AuthError(_FAIL)
        if not verify_password(password or "", row["password_hash"]):
            AUD.record(conn, "login_failed", target_id=target, ip=client_ip,
                       details={"reason": "wrong password"})
            _failed(conn, keys, now)
            raise AuthError(_FAIL)
        # P2-04: с двухфакторной защитой сессии ещё нет -- промежуточный токен
        # второго шага; счётчик ошибок не обнуляется до верного кода, иначе
        # каждый верный пароль давал бы новые попытки угадать код
        from application import two_factor as TF  # он импортирует этот модуль
        if TF.enabled(row["id"], conn=conn):
            challenge = TF.start_challenge(conn, row["id"])
            conn.commit()
            return challenge
        db.clear_login_failures(conn, "email", _norm(email))
        s = start_session(conn, row["id"])
        AUD.record(conn, "login", actor_id=row["id"], target_id=row["id"], ip=client_ip)
        conn.commit()
        return s
    finally:
        conn.close()


def _ts(iso):
    """A stored ISO time as an aware datetime (no zone = UTC); None if unreadable."""
    try:
        ts = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _session_expiry(created: datetime, now: datetime) -> datetime:
    """The idle expiry counted from now, capped by the absolute limit."""
    return min(now + timedelta(days=SESSION_IDLE_DAYS), created + timedelta(days=SESSION_MAX_DAYS))


def _session_live(created, expires, now) -> bool:
    """Before the idle expiry and within SESSION_MAX_DAYS of the sign-in; an
    unreadable time fails closed. The limit is checked here, not only stored,
    so lowering NF_SESSION_MAX_DAYS applies to sessions that already exist."""
    return (created is not None and expires is not None and now < expires
            and now < created + timedelta(days=SESSION_MAX_DAYS))


def _user_agent(ua):
    """The browser's User-Agent on one line, trimmed for the session list."""
    return " ".join(str(ua or "").split())[:_USER_AGENT_MAX] or None


def start_session(conn, user_id: int) -> dict:
    """A new session for user_id on conn (the caller commits): after a
    sign-in, and after a sign-up or password reset by emailed link (P1-06).
    Always a new token -- a sign-in never takes over an older one. The
    address and browser are filled by the first session_for_token()."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = _session_expiry(now, now)
    conn.execute("DELETE FROM sessions WHERE expires_at::timestamptz < now() "
                 "OR created_at::timestamptz < ?::timestamptz",
                 ((now - timedelta(days=SESSION_MAX_DAYS)).isoformat(),))
    conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at) "
                 "VALUES (?,?,?,?,?)",
                 (_token_hash(token), user_id, now.isoformat(), expires.isoformat(), now.isoformat()))
    return {"token": token, "userId": user_id, "expiresAt": expires.isoformat()}


def session_for_token(token: str, client_ip: str = None, user_agent: str = None):
    """{id, user: {id, email, isAdmin}} for a live session, else None. At most
    once per SESSION_TOUCH_MINUTES the idle expiry moves forward and the last
    activity, address and browser are written (multi-user plan P1-04); the
    other requests only read."""
    if not token:
        return None
    th, now = _token_hash(token), datetime.now(timezone.utc)
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT s.id AS sid, s.created_at, s.expires_at, s.last_seen_at, s.ip, s.user_agent, "
            "u.id, u.email, u.is_admin FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND u.disabled_at IS NULL",
            (th,)).fetchone()
        created = _ts(row["created_at"]) if row else None
        if not row or not _session_live(created, _ts(row["expires_at"]), now):
            return None
        seen, ua = _ts(row["last_seen_at"]), _user_agent(user_agent)
        if (seen is None or now - seen >= timedelta(minutes=SESSION_TOUCH_MINUTES)
                or (client_ip and not row["ip"]) or (ua and not row["user_agent"])):
            # parallel requests may both get here: they write the same, no harm
            conn.execute("UPDATE sessions SET last_seen_at = ?, expires_at = ?, "
                         "ip = COALESCE(?, ip), user_agent = COALESCE(?, user_agent) "
                         "WHERE token_hash = ?",
                         (now.isoformat(), _session_expiry(created, now).isoformat(),
                          client_ip or None, ua, th))
            conn.commit()
    finally:
        conn.close()
    return {"id": row["sid"],
            "user": {"id": row["id"], "email": row["email"], "isAdmin": bool(row["is_admin"])}}


def user_for_token(token: str, client_ip: str = None, user_agent: str = None):
    """{id, email, isAdmin} for a live session, else None."""
    s = session_for_token(token, client_ip=client_ip, user_agent=user_agent)
    return s["user"] if s else None


def list_sessions(user_id: int, current_id=None) -> list:
    """The user's live sessions, the current one (current_id) first, then the
    most recently active: when, from which address and browser. Never the
    token or its hash."""
    now = datetime.now(timezone.utc)
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT id, created_at, expires_at, last_seen_at, ip, user_agent "
                            "FROM sessions WHERE user_id = ?", (user_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        created, expires = _ts(r["created_at"]), _ts(r["expires_at"])
        if not _session_live(created, expires, now):
            continue
        out.append({"id": r["id"], "createdAt": r["created_at"],
                    "lastSeenAt": r["last_seen_at"] or r["created_at"],
                    "expiresAt": expires.isoformat(),
                    "endsBy": (created + timedelta(days=SESSION_MAX_DAYS)).isoformat(),
                    "ip": r["ip"], "userAgent": r["user_agent"],
                    "current": current_id is not None and r["id"] == current_id})
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(out, key=lambda s: (s["current"], _ts(s["lastSeenAt"]) or oldest), reverse=True)


def end_session(user_id: int, session_id: int) -> bool:
    """Sign out one of the user's own sessions; False if there is no such
    session of theirs (someone else's is not found either)."""
    conn = db.get_conn()
    try:
        row = conn.execute("DELETE FROM sessions WHERE id = ? AND user_id = ? RETURNING id",
                           (session_id, user_id)).fetchone()
        conn.commit()
    finally:
        conn.close()
    return row is not None


def end_other_sessions(user_id: int, keep_id: int) -> int:
    """'Sign out on other devices': every session of the user but keep_id.
    How many ended."""
    conn = db.get_conn()
    try:
        n = len(conn.execute("DELETE FROM sessions WHERE user_id = ? AND id != ? RETURNING id",
                             (user_id, keep_id)).fetchall())
        conn.commit()
    finally:
        conn.close()
    return n


def logout(token: str) -> None:
    if not token:
        return
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------- API tokens (5.7-5.8)
# For clients that cannot hold the session cookie: the browser extension and
# MCP over HTTP. "nf_" + 32 random bytes, shown once; only the SHA-256 is kept.

TOKEN_PREFIX = "nf_"


# multi-user plan P1-03: what a token may do, and for how long. read = GET
# routes, write = routes that change something, mcp = MCP over HTTP.
TOKEN_SCOPES = ("read", "write", "mcp")
TOKEN_DAYS_DEFAULT = 90
TOKEN_DAYS_MAX = 365


def _token_scopes(scopes) -> list:
    if scopes is None:
        return list(TOKEN_SCOPES)
    if isinstance(scopes, str):
        scopes = scopes.replace(" ", ",").split(",")
    picked = [x.strip().lower() for x in scopes if x and x.strip()]
    unknown = sorted(set(picked) - set(TOKEN_SCOPES))
    if unknown:
        raise AuthError(f"unknown token scope {', '.join(unknown)}; use {', '.join(TOKEN_SCOPES)}")
    if not picked:
        raise AuthError("a token needs at least one scope: " + ", ".join(TOKEN_SCOPES))
    return [x for x in TOKEN_SCOPES if x in picked]


def _token_expiry(days):
    """ISO time the token stops working; None = never (asked for with 0)."""
    if days is None:
        days = TOKEN_DAYS_DEFAULT
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise AuthError("expiresInDays must be a whole number of days") from None
    if days == 0:
        return None
    if not 1 <= days <= TOKEN_DAYS_MAX:
        raise AuthError(f"expiresInDays must be 1..{TOKEN_DAYS_MAX}, or 0 for no expiry")
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _expired(expires_at) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc)
    except ValueError:
        return True          # unreadable expiry: fail closed


def create_api_token(user_id: int, name: str = None, expires_in_days=None, scopes=None) -> dict:
    """A new personal token, shown once. expires_in_days: 1..365, default 90,
    0 = no expiry; scopes: any of read, write, mcp (default all three)."""
    picked, expires_at = _token_scopes(scopes), _token_expiry(expires_in_days)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    conn = db.get_conn()
    try:
        PL.ensure_room(user_id, "api_tokens", conn=conn)
        row = conn.execute("INSERT INTO api_tokens (user_id, token_hash, name, created_at, "
                           "expires_at, scopes) VALUES (?,?,?,?,?,?) RETURNING id",
                           (user_id, _token_hash(token), (name or "").strip()[:80] or None,
                            db.now_iso(), expires_at, ",".join(picked))).fetchone()
        AUD.record(conn, "token_create", actor_id=user_id, target_id=user_id,
                   details={"id": row["id"], "name": (name or "").strip()[:80] or None,
                            "scopes": picked, "expiresAt": expires_at})
        conn.commit()
    finally:
        conn.close()
    return {"id": row["id"], "token": token, "name": name, "expiresAt": expires_at,
            "scopes": picked, "note": "shown once -- copy it now; only its hash is stored"}


def list_api_tokens(user_id: int) -> list:
    conn = db.get_conn()
    try:
        return [{"id": r["id"], "name": r["name"], "createdAt": r["created_at"],
                 "lastUsedAt": r["last_used_at"], "expiresAt": r["expires_at"],
                 "expired": _expired(r["expires_at"]),
                 "scopes": [x for x in (r["scopes"] or "").split(",") if x]}
                for r in conn.execute("SELECT id, name, created_at, last_used_at, expires_at, scopes "
                                      "FROM api_tokens WHERE user_id = ? ORDER BY id",
                                      (user_id,)).fetchall()]
    finally:
        conn.close()


def revoke_api_token(user_id: int, token_id: int) -> bool:
    conn = db.get_conn()
    try:
        row = conn.execute("DELETE FROM api_tokens WHERE id = ? AND user_id = ? RETURNING id, name",
                           (token_id, user_id)).fetchone()
        if row:
            AUD.record(conn, "token_revoke", actor_id=user_id, target_id=user_id,
                       details={"id": token_id, "name": row["name"]})
        conn.commit()
        return bool(row)
    finally:
        conn.close()


def user_for_api_token(token: str):
    """{id, email, isAdmin, scopes} for a live API token, else None. An
    expired token is no token (multi-user plan P1-03); callers check the
    scope they need with token_allows()."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    conn = db.get_conn()
    try:
        now = db.now_iso()
        row = conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ? "
            "AND (expires_at IS NULL OR expires_at > ?) RETURNING user_id, scopes",
            (now, _token_hash(token), now)).fetchone()
        if not row:
            return None
        u = conn.execute("SELECT id, email, is_admin FROM users WHERE id = ? "
                         "AND disabled_at IS NULL", (row["user_id"],)).fetchone()
        conn.commit()
    finally:
        conn.close()
    if not u:
        return None
    return {"id": u["id"], "email": u["email"], "isAdmin": bool(u["is_admin"]),
            "scopes": [x for x in (row["scopes"] or "").split(",") if x]}


def token_allows(user: dict, scope: str) -> bool:
    """Whether the token behind `user` (from user_for_api_token) has `scope`."""
    return scope in (user or {}).get("scopes", ())

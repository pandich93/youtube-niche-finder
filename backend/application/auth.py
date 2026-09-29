"""Users and sign-in (plan 15, used when NF_MULTI_USER=1).

Accounts are by invitation: an admin creates them from the command line
(`cli.py create-user`), there is no self-registration. Passwords are hashed
with scrypt from the standard library (a per-password random salt, constant-
time comparison). A session is a random token in an HttpOnly cookie; the
database keeps only its SHA-256, so a leaked database does not leak live
sessions. Wrong password and unknown email fail with the same message.
"""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db

MIN_PASSWORD_LEN = 10
SESSION_DAYS = 30
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32
_FAIL = "wrong email or password"
# Verified against when the email is unknown, so both failures take the same time.
_DUMMY_HASH = None


class AuthError(ValueError):
    pass


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


def _check_password(password):
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"the password needs at least {MIN_PASSWORD_LEN} characters")


def create_user(email: str, password: str, is_admin: bool = False) -> int:
    email = _norm(email)
    if not email:
        raise AuthError("email is required")
    _check_password(password)
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
    """Set or change a password; ends every session of that user."""
    _check_password(password)
    conn = db.get_conn()
    try:
        row = conn.execute("UPDATE users SET password_hash = ? WHERE email = ? RETURNING id",
                           (hash_password(password), _norm(email))).fetchone()
        if not row:
            raise AuthError(f"no user {_norm(email)}")
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        conn.commit()
        return row["id"]
    finally:
        conn.close()


def list_users() -> list:
    conn = db.get_conn()
    try:
        return [{"id": r["id"], "email": r["email"], "isAdmin": bool(r["is_admin"]),
                 "hasPassword": r["password_hash"] is not None, "createdAt": r["created_at"]}
                for r in conn.execute("SELECT * FROM users ORDER BY id").fetchall()]
    finally:
        conn.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(email: str, password: str) -> dict:
    global _DUMMY_HASH
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id, password_hash FROM users WHERE email = ?",
                           (_norm(email),)).fetchone()
        if not row or not row["password_hash"]:
            _DUMMY_HASH = _DUMMY_HASH or hash_password(secrets.token_hex(8))
            verify_password(password or "", _DUMMY_HASH)
            raise AuthError(_FAIL)
        if not verify_password(password or "", row["password_hash"]):
            raise AuthError(_FAIL)
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
        conn.execute("DELETE FROM sessions WHERE expires_at::timestamptz < now()")
        conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (_token_hash(token), row["id"], db.now_iso(), expires.isoformat()))
        conn.commit()
        return {"token": token, "userId": row["id"], "expiresAt": expires.isoformat()}
    finally:
        conn.close()


def user_for_token(token: str):
    """{id, email, isAdmin} for a live session, else None."""
    if not token:
        return None
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT u.id, u.email, u.is_admin, s.expires_at FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
    finally:
        conn.close()
    if not row or _expired(row["expires_at"]):
        return None
    return {"id": row["id"], "email": row["email"], "isAdmin": bool(row["is_admin"])}


def _expired(iso) -> bool:
    try:
        ts = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts <= datetime.now(timezone.utc)


def logout(token: str) -> None:
    if not token:
        return
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()
    finally:
        conn.close()

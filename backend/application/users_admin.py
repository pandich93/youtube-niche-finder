"""Blocking and deleting accounts (multi-user plan P0-04).

An admin creates, blocks, unblocks and deletes accounts from the dashboard or
the command line; a person deletes their own account with their password --
the promise of the /privacy page. Deleting is real, not a flag: every row of
db.PERSONAL_TABLES with that user_id, their meta counters, then the users row.
The shared corpus stays: a transcript or tag they added passes to the
operator (created_by = NULL), shared events (user_id NULL) are not theirs.
Each action is written to admin_actions with the email, which outlives the row.

Guards: an admin does not block or delete themself here (self-delete is its
own path), the local user (the data from before accounts) is never deleted,
and the server always keeps one active admin.
"""
import secrets

import infrastructure.postgres as db
from application import auth as AUTH
from application import digest as DG
from application import own_channels as OWN
from domain.i18n import tr
from domain.users import LOCAL_USER_ID, SYSTEM_USER_ID

OPERATOR = SYSTEM_USER_ID                 # the actor of CLI actions


class UserAdminError(ValueError):
    pass


class UserNotFound(LookupError):
    pass


def _user(conn, user_id: int):
    row = conn.execute("SELECT id, email, is_admin, disabled_at, password_hash FROM users "
                       "WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise UserNotFound(user_id)
    return row


def _keeps_an_admin(conn, target) -> bool:
    if not target["is_admin"]:
        return True
    other = conn.execute("SELECT 1 FROM users WHERE is_admin = 1 AND disabled_at IS NULL "
                         "AND id != ? LIMIT 1", (target["id"],)).fetchone()
    return other is not None


def _last_admin():
    return UserAdminError(tr("Это последний активный администратор: сначала назначьте другого."))


def _local_kept():
    return UserAdminError(tr("Пользователя local удалить нельзя: у него данные, собранные до "
                             "включения учётных записей. Его можно заблокировать."))


def _log(conn, admin_id: int, action: str, user_id: int, email: str):
    conn.execute("INSERT INTO admin_actions (admin_id, action, target_user_id, target_email, "
                 "created_at) VALUES (?,?,?,?,?)", (admin_id, action, user_id, email, db.now_iso()))


def _guard(conn, actor_id: int, target, deleting: bool):
    if target["id"] == actor_id:
        raise UserAdminError(tr("Со своей учётной записью так нельзя: удалить её можно на "
                                "странице «Тариф»."))
    if deleting and target["id"] == LOCAL_USER_ID:
        raise _local_kept()
    if not _keeps_an_admin(conn, target):
        raise _last_admin()


def create(actor_id: int, email: str, is_admin: bool = False) -> dict:
    """A new account with a one-time password, shown here only."""
    password = secrets.token_urlsafe(12)
    user_id = AUTH.create_user(email, password, is_admin=is_admin)
    conn = db.get_conn()
    try:
        _log(conn, actor_id, "create", user_id, AUTH._norm(email))
        conn.commit()
    finally:
        conn.close()
    return {"id": user_id, "email": AUTH._norm(email), "isAdmin": bool(is_admin),
            "password": password}


def set_disabled(actor_id: int, user_id: int, disabled: bool) -> dict:
    conn = db.get_conn()
    try:
        target = _user(conn, user_id)
        if disabled:
            _guard(conn, actor_id, target, deleting=False)
    finally:
        conn.close()
    AUTH.set_disabled(user_id, disabled)
    conn = db.get_conn()
    try:
        _log(conn, actor_id, "disable" if disabled else "enable", user_id, target["email"])
        conn.commit()
    finally:
        conn.close()
    return {"id": user_id, "email": target["email"], "disabled": disabled}


def _erase(user_id: int, email: str, actor_id: int, action: str) -> dict:
    # 1. best effort, outside the transaction: Google forgets the tokens
    conn = db.get_conn()
    try:
        channels = [r["channel_id"] for r in conn.execute(
            "SELECT channel_id FROM own_channels WHERE user_id = ?", (user_id,)).fetchall()]
    finally:
        conn.close()
    revoked = 0
    for channel_id in channels:
        try:
            revoked += bool(OWN.disconnect(channel_id, user_id=user_id)["revoked"])
        except Exception:
            pass                           # the rows go below anyway
    # 2. everything that is theirs, in one transaction
    conn = db.get_conn()
    try:
        for table in db.PERSONAL_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        for table in ("transcripts", "video_tags"):
            conn.execute(f"UPDATE {table} SET created_by = NULL WHERE created_by = ?", (user_id,))
        conn.execute("DELETE FROM meta WHERE (key LIKE ? OR key LIKE ?) AND key LIKE ?",
                     ("yt\\_units\\_%", "search\\_calls\\_%", f"%\\_u{user_id}"))
        conn.execute("DELETE FROM meta WHERE key = ?", (DG._last_sent_key(user_id),))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        _log(conn, actor_id, action, user_id, email)
        conn.commit()
    finally:
        conn.close()
    return {"deleted": user_id, "email": email, "oauthRevoked": revoked}


def delete(actor_id: int, user_id: int, confirm_email: str) -> dict:
    """An admin deletes an account; the email typed back guards against a
    slip of the mouse."""
    conn = db.get_conn()
    try:
        target = _user(conn, user_id)
        _guard(conn, actor_id, target, deleting=True)
    finally:
        conn.close()
    if AUTH._norm(confirm_email) != target["email"]:
        raise UserAdminError(tr("Для удаления введите email этого пользователя."))
    return _erase(user_id, target["email"], actor_id, "delete")


def delete_self(user_id: int, password: str) -> dict:
    """A person deletes their own account, confirming with their password."""
    conn = db.get_conn()
    try:
        target = _user(conn, user_id)
        if user_id == LOCAL_USER_ID:
            raise _local_kept()
        if not _keeps_an_admin(conn, target):
            raise _last_admin()
    finally:
        conn.close()
    if not AUTH.verify_password(password or "", target["password_hash"]):
        raise UserAdminError(tr("Неверный пароль."))
    return _erase(user_id, target["email"], user_id, "self-delete")


def by_email(email: str) -> int:
    """The id for the CLI, which names people by email."""
    user_id = AUTH.user_id_for_email(email)
    if user_id is None:
        raise UserNotFound(email)
    return user_id

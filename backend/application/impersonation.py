"""Support view: an admin looks at an account through its owner's eyes,
read-only (multi-user plan P2-05).

The admin's own dashboard session (never an API token, never MCP) is marked
with the account it looks at (sessions.impersonating_user_id) for at most
NF_IMPERSONATION_MINUTES (30 by default). While the mark lasts the HTTP layer
(interfaces/http/api.py session_guard) serves the account's data -- _uid and
Row-Level Security see the account, not the admin -- refuses every request
that changes something except leaving the view, and freezes spending
(infrastructure/quota_owner.freeze): nobody's YouTube units, no new LLM call,
only what is already cached.

Not on an admin, not on a blocked account, not on oneself, not nested, and
only with a reason. The start, the end and the expiry go into the action log
(application/audit.py) with who, whom and why. When the time is up, the next
request clears the mark and the session is the admin's again.

The sessions table is under Row-Level Security and the view is switched from
inside a request scoped to the account, so every query here runs in the
system scope; user ids are always explicit in the SQL.
"""
import os
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from application import audit as AUD
from domain.i18n import tr

REASON_MAX = 300


def _minutes() -> int:
    """NF_IMPERSONATION_MINUTES: how long one view lasts, 1..240."""
    try:
        return min(240, max(1, int(os.environ.get("NF_IMPERSONATION_MINUTES") or 30)))
    except ValueError:
        return 30


class ImpersonationError(ValueError):
    pass


class NotFound(ImpersonationError):
    pass


def _ts(iso):
    try:
        ts = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def start(admin_id: int, session_id: int, target_id: int, reason: str) -> dict:
    """Mark the admin's session session_id to look at target_id. The caller
    (the HTTP layer) has checked that this is an admin's dashboard session."""
    reason = " ".join(str(reason or "").split())[:REASON_MAX]
    if not reason:
        raise ImpersonationError(tr("Укажите причину просмотра: она попадёт в журнал действий."))
    if target_id == admin_id:
        raise ImpersonationError(tr("Смотреть под самим собой не нужно."))
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=_minutes())
    with db.system_scope():
        conn = db.get_conn()
        try:
            target = conn.execute("SELECT id, email, is_admin, disabled_at FROM users WHERE id = ?",
                                  (target_id,)).fetchone()
            if not target:
                raise NotFound(tr("Такого пользователя нет."))
            if target["is_admin"]:
                raise ImpersonationError(tr("Под другим администратором смотреть нельзя."))
            if target["disabled_at"] is not None:
                raise ImpersonationError(tr("Аккаунт заблокирован: под ним смотреть нельзя."))
            # one statement: a session that already looks at someone is not
            # switched to a second account (no nesting, no silent hand-over)
            row = conn.execute(
                "UPDATE sessions SET impersonating_user_id = ?, impersonation_started_at = ?, "
                "impersonation_expires_at = ?, impersonation_reason = ? "
                "WHERE id = ? AND user_id = ? AND impersonating_user_id IS NULL RETURNING id",
                (target_id, now.isoformat(), expires.isoformat(), reason, session_id,
                 admin_id)).fetchone()
            if not row:
                raise ImpersonationError(tr("Эта сессия уже в режиме просмотра: сначала выйдите из него."))
            AUD.record(conn, "impersonation_start", actor_id=admin_id, target_id=target_id,
                       details={"reason": reason, "minutes": _minutes(),
                                "expiresAt": expires.isoformat(), "sessionId": session_id})
            conn.commit()
        finally:
            conn.close()
    return {"userId": target_id, "email": target["email"], "startedAt": now.isoformat(),
            "expiresAt": expires.isoformat(), "reason": reason}


def end(session_id: int, why: str = "exit") -> bool:
    """Clear the mark; the session is the admin's again. why: exit (the admin
    left), logout, expired, or a reason the view stopped being allowed. One
    log entry even when two requests race: only one UPDATE gets the row back.
    False: the session was not looking at anyone."""
    with db.system_scope():
        conn = db.get_conn()
        try:
            row = conn.execute(
                "UPDATE sessions s SET impersonating_user_id = NULL, impersonation_started_at = NULL, "
                "impersonation_expires_at = NULL, impersonation_reason = NULL "
                "FROM (SELECT id, impersonating_user_id, impersonation_started_at FROM sessions "
                "WHERE id = ? AND impersonating_user_id IS NOT NULL FOR UPDATE) old "
                "WHERE s.id = old.id RETURNING s.user_id, old.impersonating_user_id AS target, "
                "old.impersonation_started_at AS started_at", (session_id,)).fetchone()
            if row:
                started = _ts(row["started_at"])
                minutes = (round((datetime.now(timezone.utc) - started).total_seconds() / 60, 1)
                           if started else None)
                AUD.record(conn, "impersonation_expired" if why == "expired" else "impersonation_end",
                           actor_id=row["user_id"], target_id=row["target"],
                           details={"why": why, "minutes": minutes, "sessionId": session_id})
            conn.commit()
        finally:
            conn.close()
    return row is not None


def resolve(session: dict):
    """For a live session from auth.session_for_token: the view it is in,
    {user: {id, email, isAdmin}, admin, startedAt, expiresAt, reason}, else
    None. A view past its time, or one that stopped being allowed (the admin
    lost the role, the account was blocked, deleted or made an admin), ends
    here -- and the request goes on as the admin."""
    if not session or not session.get("impersonating"):
        return None
    admin = session["user"]
    with db.system_scope():
        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT s.impersonation_started_at, s.impersonation_expires_at, "
                "s.impersonation_reason, u.id, u.email, u.is_admin, u.disabled_at "
                "FROM sessions s LEFT JOIN users u ON u.id = s.impersonating_user_id "
                "WHERE s.id = ?", (session["id"],)).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    expires = _ts(row["impersonation_expires_at"])
    if expires is None or datetime.now(timezone.utc) >= expires:
        why = "expired"
    elif not admin.get("isAdmin"):
        why = "admin role removed"
    elif row["id"] is None:
        why = "account deleted"
    elif row["disabled_at"] is not None:
        why = "account blocked"
    elif row["is_admin"]:
        why = "account is an admin"
    else:
        return {"user": {"id": row["id"], "email": row["email"], "isAdmin": False},
                "admin": {"id": admin["id"], "email": admin["email"]},
                "startedAt": row["impersonation_started_at"],
                "expiresAt": expires.isoformat(), "reason": row["impersonation_reason"]}
    end(session["id"], why=why)
    return None

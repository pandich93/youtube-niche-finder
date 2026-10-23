"""The action log (multi-user plan P1-07): sign-ins, failed sign-ins, password
and email changes, sign-ups, API tokens, tariff changes and what admins do
to accounts and tariffs -- in one table, audit_log, readable by admins only.

record() writes on the caller's connection, so an entry and the action it
describes are committed together; log() is the same on a connection of its
own. The client address comes from the HTTP request (client_ip_scope(), set
by a middleware in interfaces/http/api.py, the same _client_ip as the sign-in
throttle); the CLI and the worker have none. What goes into details is
cleaned by domain/audit.py: never a password, token or secret.

Entries are kept NF_AUDIT_RETENTION_DAYS (180 by default) and removed by the
worker's daily step (purge_expired). A deleted account's entries stay until
then: the log is the record of who did what, and it has no user_id column.
"""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from domain import audit as DA
from domain.i18n import tr

OPERATOR_ID = DA.OPERATOR_ID
PAGE_MAX = 200

_client_ip = ContextVar("nf_audit_client_ip", default=None)


class AuditError(ValueError):
    pass


@contextmanager
def client_ip_scope(ip):
    """The address of the request being served, for every entry it writes."""
    token = _client_ip.set(ip)
    try:
        yield
    finally:
        _client_ip.reset(token)


def record(conn, event: str, *, actor_id=None, target_id=None, target_email=None, ip=None,
           details=None) -> None:
    """One entry on conn; the caller commits. ip=None takes the request's."""
    if event not in DA.EVENTS:
        raise AuditError(f"unknown audit event {event}")
    conn.execute("INSERT INTO audit_log (created_at, event, actor_id, target_id, target_email, "
                 "ip, details) VALUES (?,?,?,?,?,?,?::jsonb)",
                 (db.now_iso(), event, actor_id, target_id, target_email,
                  ip if ip is not None else _client_ip.get(),
                  json.dumps(DA.clean_details(details), ensure_ascii=False)))


def log(event: str, **fields) -> None:
    """record() on a connection of its own, committed at once."""
    conn = db.get_conn()
    try:
        record(conn, event, **fields)
        conn.commit()
    finally:
        conn.close()


def purge_expired(now: datetime = None) -> int:
    """The worker's daily step: drop entries older than the retention."""
    days = DA.retention_days()
    if not days:
        return 0
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    conn = db.get_conn()
    try:
        n = len(conn.execute("DELETE FROM audit_log WHERE created_at < ? RETURNING id",
                             (cutoff,)).fetchall())
        conn.commit()
        return n
    finally:
        conn.close()


def _day(value, name: str, end: bool):
    """'YYYY-MM-DD' (or a full ISO time) -> the UTC ISO bound; end=True is the
    start of the next day, so the whole day is included."""
    if not value:
        return None
    text = str(value).strip()
    try:
        if len(text) == 10:
            ts = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
            return (ts + timedelta(days=1 if end else 0)).isoformat()
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise AuditError(tr("{name}: нужна дата в формате ГГГГ-ММ-ДД", name=name)) from None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def _person(conn, who: str):
    """'12' or an email -> (ids, email) to look for as actor or target. An
    email also matches entries about a deleted account (target_email)."""
    who = (who or "").strip().lower()
    if who.isdigit():
        return [int(who)], None
    row = conn.execute("SELECT id FROM users WHERE email = ?", (who,)).fetchone()
    return ([row["id"]] if row else []), who


def search(*, event=None, user=None, since=None, until=None, page: int = 1,
           per_page: int = 50) -> dict:
    """A page of entries, newest first, with the total. event: an event name
    or a group (auth, tokens, plans, accounts, admin); user: an id or an email,
    as actor or target; since/until: days (YYYY-MM-DD), both included."""
    where, params = [], []
    if event:
        if event in DA.EVENTS:
            where.append("a.event = ?")
            params.append(event)
        elif event in set(DA.EVENTS.values()):
            names = [e for e, g in DA.EVENTS.items() if g == event]
            where.append(f"a.event IN ({', '.join('?' * len(names))})")
            params += names
        else:
            raise AuditError(tr("Неизвестное событие журнала: {event}", event=event))
    lo, hi = _day(since, "since", end=False), _day(until, "until", end=True)
    if lo:
        where.append("a.created_at >= ?")
        params.append(lo)
    if hi:
        where.append("a.created_at < ?")
        params.append(hi)
    try:
        page, per_page = max(1, int(page or 1)), min(PAGE_MAX, max(1, int(per_page or 50)))
    except (TypeError, ValueError):
        raise AuditError(tr("page и perPage: целые числа")) from None
    conn = db.get_conn()
    try:
        if user:
            ids, email = _person(conn, user)
            terms = []
            if ids:
                marks = ", ".join("?" * len(ids))
                terms.append(f"a.actor_id IN ({marks}) OR a.target_id IN ({marks})")
                params += ids + ids
            if email:
                terms.append("a.target_email = ?")
                params.append(email)
            where.append("(" + " OR ".join(terms) + ")")
        cond = ("WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) AS n FROM audit_log a {cond}",
                             tuple(params)).fetchone()["n"]
        rows = conn.execute(
            "SELECT a.*, ua.email AS actor_email, ut.email AS target_now FROM audit_log a "
            "LEFT JOIN users ua ON ua.id = a.actor_id LEFT JOIN users ut ON ut.id = a.target_id "
            f"{cond} ORDER BY a.created_at DESC, a.id DESC LIMIT ? OFFSET ?",
            tuple(params) + (per_page, (page - 1) * per_page)).fetchall()
    finally:
        conn.close()
    return {"entries": [_shape(r) for r in rows], "total": total, "page": page,
            "perPage": per_page, "events": list(DA.EVENTS), "groups": sorted(set(DA.EVENTS.values())),
            "retentionDays": DA.retention_days()}


def _details(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _shape(r) -> dict:
    return {"id": r["id"], "at": r["created_at"], "event": r["event"],
            "group": DA.EVENTS.get(r["event"]),
            "actorId": r["actor_id"], "actorEmail": r["actor_email"],
            "targetId": r["target_id"],
            # the email now, else the one written down (the account may be gone)
            "targetEmail": r["target_now"] or r["target_email"],
            "ip": r["ip"], "details": _details(r["details"])}

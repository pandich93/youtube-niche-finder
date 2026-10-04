"""Content calendar (plan 33) -- the rules are in domain/calendar.py. Drafts
(application/metadata_review.py, personal) get a planned time; the calendar
shows them by day, with the niche's or channel's best publishing hours from
best_time_to_publish (UTC) as a hint, and the worker raises a personal
draft_due event the day before and when the time comes -- delivered to
Telegram/webhook and the digest like any alert. Zero quota, no LLM.
"""
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from application import alerts as AL
from application import channel_tracking as T
from application import metadata_review as MR
from domain import content_calendar as K
from domain import periods as P
from domain.users import LOCAL_USER_ID

MAX_RANGE_DAYS = 62
NOTE = "Times are UTC; the dashboard shows them in your time zone. Best hours are a correlation."


def _now():
    return datetime.now(timezone.utc)


def _parse(value, field):
    d = P.parse_utc(value)
    if d is None:
        raise ValueError(f"{field} must be an ISO date-time, e.g. 2026-10-10T15:00:00Z")
    return d.astimezone(timezone.utc)   # stored and compared as UTC text


def _row(conn, draft_id, user_id):
    r = conn.execute("SELECT * FROM drafts WHERE id = ? AND user_id = ?",
                     (int(draft_id), user_id)).fetchone()
    if not r:
        raise LookupError(f"draft {draft_id} not found")
    return r


def best_slots(niche: str = None, channel_id: str = None) -> list:
    """The best weekday/hour slots (UTC) for a niche or channel, best first."""
    if not niche and not channel_id:
        return []
    try:
        best = T.best_time_to_publish(niche=niche, channel_id=None if niche else channel_id)
    except Exception:
        return []
    return [{"weekday": b["weekday"], "hour": b["hour"], "score": b["score"]}
            for b in best.get("best", [])[:K.MAX_SLOTS]]


def _shape(d: dict, now, slots=None) -> dict:
    planned = P.parse_utc(d.get("plannedAt"))
    out = {"id": d["id"], "title": d["title"], "niche": d["niche"], "channelId": d["channelId"],
           "isShort": d["isShort"], "videoId": d["videoId"], "plannedAt": d.get("plannedAt"),
           "publishedAt": d["publishedAt"], "createdAt": d["createdAt"],
           "sourceVideoId": d.get("sourceVideoId"),
           "state": K.state(planned, bool(d["videoId"]), now)}
    if slots is not None:
        out["bestSlots"] = slots
        out["fitsBestSlot"] = K.fits(planned, slots)
    return out


def plan_draft(draft_id: int, planned_at: str = None, user_id: int = LOCAL_USER_ID) -> dict:
    """Give a draft a release time (ISO, UTC; None takes it off the calendar)
    and say whether it falls on one of the best hours of its niche/channel."""
    planned = _parse(planned_at, "planned_at").isoformat() if planned_at else None
    conn = db.get_conn()
    try:
        _row(conn, draft_id, user_id)
        row = conn.execute("UPDATE drafts SET planned_at = ? WHERE id = ? AND user_id = ? RETURNING *",
                           (planned, int(draft_id), user_id)).fetchone()
        conn.commit()
    finally:
        conn.close()
    d = MR._shape_draft(row)
    return {**_shape(d, _now(), best_slots(d["niche"], d["channelId"])), "note": NOTE}


def content_calendar(start: str = None, end: str = None, user_id: int = LOCAL_USER_ID) -> dict:
    """Drafts planned (or published) between start and end (ISO; default:
    this week and the next three), each with its state; plus the drafts not
    planned yet, to put on the calendar."""
    now = _now()
    s = _parse(start, "start") if start else (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    e = _parse(end, "end") if end else s + timedelta(days=28)
    if e <= s:
        raise ValueError("end must be after start")
    if e - s > timedelta(days=MAX_RANGE_DAYS):
        raise ValueError(f"at most {MAX_RANGE_DAYS} days at a time")
    drafts = MR.list_drafts(limit=1000, user_id=user_id)
    items, unplanned = [], []
    for d in drafts:
        when = P.parse_utc(d.get("plannedAt") or (d["publishedAt"] if d["videoId"] else None))
        if d.get("plannedAt") is None and not d["videoId"]:
            unplanned.append(_shape(d, now))
        elif when and s <= when < e:
            items.append({**_shape(d, now), "day": when.date().isoformat()})
    items.sort(key=lambda x: x["plannedAt"] or x["publishedAt"] or "")
    return {"start": s.isoformat(), "end": e.isoformat(), "items": items,
            "unplanned": unplanned[:100], "note": NOTE}


def remind_due(now: datetime = None) -> dict:
    """Worker step: one personal draft_due event per draft and stage ('soon'
    the day before, 'due' when the time comes). Safe to run every cycle."""
    now = now or _now()
    lo = (now - timedelta(hours=K.DUE_GRACE_HOURS)).isoformat()
    hi = (now + timedelta(hours=K.REMIND_BEFORE_HOURS)).isoformat()
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT id, user_id, title, niche, channel_id, planned_at, video_id "
                            "FROM drafts WHERE planned_at IS NOT NULL AND video_id IS NULL "
                            "AND planned_at >= ? AND planned_at <= ?", (lo, hi)).fetchall()
        raised = 0
        for r in rows:
            planned = P.parse_utc(r["planned_at"])
            stage = K.reminder(planned, False, now)
            if not stage:
                continue
            payload = {"draftId": r["id"], "title": r["title"], "plannedAt": r["planned_at"],
                       "stage": stage, "niche": r["niche"]}
            if AL._emit(conn, "draft_due", f"{r['id']}:{r['planned_at']}:{stage}", payload,
                        user_id=r["user_id"]):
                raised += 1
        conn.commit()
        return {"checked": len(rows), "raised": raised}
    finally:
        conn.close()


def event_text(p: dict) -> str:
    """The words of a draft_due reminder (Telegram HTML is escaped by the caller)."""
    when = (p.get("plannedAt") or "")[:16].replace("T", " ")
    return ("выпуск в ближайшие сутки" if p.get("stage") == "soon" else "пора выпускать") + f" · {when} UTC"


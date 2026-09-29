"""Alerts orchestration (plan item 8.9): the worker calls scan() on a
schedule; it reads whatever collect_*/refresh_stats already stored (zero
YouTube quota), runs the pure detectors in domain/alerts.py, and writes any
NEW events to the `events` table -- "new" meaning no row with the same
(kind, ref_id) exists yet, which is what makes repeated cycles idempotent
without needing a separate "already processed" cursor.
"""
import json
import os

import infrastructure.postgres as db
from application import channel_tracking as T
from application import discovery as trends
from domain import alerts as A
from domain.users import LOCAL_USER_ID
from infrastructure.notify import factory as notify_factory
from infrastructure.notify.null import NullNotifier

DEFAULT_SILENCE_DAYS = A.SILENCE_DAYS_DEFAULT
DEFAULT_PERIOD = "30d"
NOTIFY_MAX_PER_CYCLE = int(os.environ.get("NOTIFY_MAX_PER_CYCLE", "10"))
DASHBOARD_URL = os.environ.get("NOTIFY_DASHBOARD_URL", "http://127.0.0.1:8080").rstrip("/")
# instant (one message per event, default) | digest (only the daily summary,
# application/digest.py) | both
NOTIFY_MODE = os.environ.get("NOTIFY_MODE", "instant").strip().lower() or "instant"


def _already_emitted(conn, kind, ref_id) -> bool:
    row = conn.execute(
        "SELECT 1 FROM events WHERE kind=? AND ref_id=? LIMIT 1", (kind, ref_id)
    ).fetchone()
    return row is not None


def _emit(conn, kind, ref_id, payload) -> bool:
    """Insert one event unless (kind, ref_id) already exists. Returns whether
    a new row was written -- the caller uses this to report counts, not to
    make decisions, so a race between two workers double-inserting the same
    (kind, ref_id) is a harmless duplicate row, not a correctness bug."""
    if _already_emitted(conn, kind, ref_id):
        return False
    conn.execute(
        "INSERT INTO events (kind, ref_id, payload, created_at, channel_id) VALUES (?,?,?,?,?)",
        (kind, ref_id, json.dumps(payload, ensure_ascii=False), db.now_iso(),
         (payload or {}).get("channelId")),
    )
    return True


def _latest_snapshot_at(conn, video_ids: list) -> dict:
    """video_id -> most recent video_stats_history.captured_at, for folding
    into acceleration's dedupe key (see domain/alerts.py docstring)."""
    if not video_ids:
        return {}
    out = {}
    for i in range(0, len(video_ids), 400):
        chunk = video_ids[i:i + 400]
        q = ("SELECT video_id, MAX(captured_at) AS captured_at FROM video_stats_history "
             "WHERE video_id IN (%s) GROUP BY video_id" % ",".join("?" * len(chunk)))
        for r in conn.execute(q, chunk).fetchall():
            out[r["video_id"]] = r["captured_at"]
    return out


def _gone_rows(conn, channel_ids: list) -> list:
    """Confirmed gone_items (plan 04) scoped like every other detector: only
    tracked channels, and only those tracked channels' videos that were
    already alerted as outliers -- ordinary videos get hidden or deleted all
    the time, a vanished outlier is the signal worth a message. Joined with
    the last numbers we stored before the item disappeared."""
    placeholders = ",".join("?" * len(channel_ids))
    rows = [dict(r, kind="channel") for r in conn.execute(
        "SELECT g.ref_id, g.first_missing_at, g.confirmed_at, c.title, "
        "c.subscriber_count, c.view_count, c.video_count, c.updated_at AS last_seen_at "
        "FROM gone_items g LEFT JOIN channels c ON c.channel_id = g.ref_id "
        "WHERE g.kind='channel' AND g.confirmed_at IS NOT NULL "
        f"AND g.ref_id IN ({placeholders})", channel_ids).fetchall()]

    outlier_scores = {}
    for r in conn.execute("SELECT ref_id, payload FROM events WHERE kind='outlier'").fetchall():
        try:
            outlier_scores[r["ref_id"]] = (json.loads(r["payload"]) or {}).get("outlierScore")
        except (TypeError, ValueError):
            outlier_scores[r["ref_id"]] = None
    for r in conn.execute(
            "SELECT g.ref_id, g.first_missing_at, g.confirmed_at, v.title, v.channel_id, "
            "v.view_count FROM gone_items g JOIN videos v ON v.video_id = g.ref_id "
            "WHERE g.kind='video' AND g.confirmed_at IS NOT NULL "
            f"AND v.channel_id IN ({placeholders})", channel_ids).fetchall():
        if r["ref_id"] in outlier_scores:
            rows.append(dict(r, kind="video", outlier_score=outlier_scores[r["ref_id"]]))
    return rows


def scan(outlier_threshold: float = A.OUTLIER_THRESHOLD_DEFAULT,
        acceleration_threshold: float = A.ACCELERATION_THRESHOLD_DEFAULT,
        silence_days: float = DEFAULT_SILENCE_DAYS, period: str = DEFAULT_PERIOD) -> dict:
    """Run every detector against TRACKED channels only -- alerts are about
    channels you asked to watch, not the whole database. Safe to call every
    worker cycle: see _emit for why repeats never duplicate."""
    # every user's tracked channels, each once: an event is a shared fact, who
    # sees it is decided when reading (list_events)
    channel_ids = T.tracked_channel_ids()
    empty_counts = {"outlier": 0, "acceleration": 0, "title_change": 0, "silence_break": 0,
                    "channel_gone": 0, "video_gone": 0}
    if not channel_ids:
        return {"channelsScanned": 0, "videosScanned": 0, "emitted": empty_counts,
                "hint": "no tracked channels -- track_channel first"}

    rows = trends.load_window(period=period, channel_ids=channel_ids)
    conn = db.get_conn()

    snap_at = _latest_snapshot_at(conn, [r["video_id"] for r in rows])
    outlier_rows = [{"video_id": r["video_id"], "title": r["title"],
                     "channel_id": r["channel_id"], "view_count": r["view_count"],
                     "outlier_score": (r.get("outlierScoreAgeAdjusted")
                                       or r.get("outlierScore") or r.get("outlierScoreNexlev"))}
                    for r in rows]
    accel_rows = [{"video_id": r["video_id"], "title": r["title"],
                   "channel_id": r["channel_id"], "acceleration": r.get("acceleration"),
                   "vph24h": r.get("vph24h"), "captured_at": snap_at.get(r["video_id"])}
                  for r in rows if r.get("acceleration") is not None]

    placeholders = ",".join("?" * len(channel_ids))
    tc_rows = [dict(r) for r in conn.execute(
        "SELECT ch.video_id, ch.changed_at, ch.old_value, ch.new_value, v.channel_id "
        "FROM video_changes ch JOIN videos v ON v.video_id = ch.video_id "
        f"WHERE ch.field='title' AND v.channel_id IN ({placeholders})",
        channel_ids).fetchall()]

    upload_rows = conn.execute(
        "SELECT channel_id, video_id, title, published_at FROM videos "
        f"WHERE channel_id IN ({placeholders}) AND published_at IS NOT NULL",
        channel_ids).fetchall()
    channel_uploads = {}
    for r in upload_rows:
        channel_uploads.setdefault(r["channel_id"], []).append(
            (r["published_at"], r["video_id"], r["title"]))

    candidates = {
        "outlier": A.detect_outliers(outlier_rows, threshold=outlier_threshold),
        "acceleration": A.detect_acceleration(accel_rows, threshold=acceleration_threshold),
        "title_change": A.detect_title_changes(tc_rows),
        "silence_break": A.detect_silence_breaks(channel_uploads, silence_days=silence_days),
        "channel_gone": [],
        "video_gone": [],
    }
    for ev in A.detect_gone(_gone_rows(conn, channel_ids)):
        candidates[ev["kind"]].append(ev)
    emitted = dict(empty_counts)
    for kind, evs in candidates.items():
        for ev in evs:
            if _emit(conn, ev["kind"], ev["refId"], ev["payload"]):
                emitted[kind] += 1
    conn.commit()
    conn.close()
    return {"channelsScanned": len(channel_ids), "videosScanned": len(rows), "emitted": emitted}


# plan 15 (5.4): an event is a shared fact; a user sees it when its channel
# (events.channel_id, set when it is emitted) is on their watchlist. "Seen" is
# personal (event_reads), not events.seen_at.
VISIBLE_TO_USER = ("EXISTS (SELECT 1 FROM tracked_channels t WHERE t.user_id = ? "
                   "AND t.channel_id = e.channel_id)")


def _shape_event(r) -> dict:
    try:
        payload = json.loads(r["payload"]) if r["payload"] else {}
    except (TypeError, ValueError):
        payload = {}
    return {"id": r["id"], "kind": r["kind"], "refId": r["ref_id"], "payload": payload,
           "createdAt": r["created_at"], "seenAt": r["seen_at"]}


def list_events(unseen_only: bool = False, kind: str = None, limit: int = 100,
                user_id: int = LOCAL_USER_ID) -> list:
    where, params = [VISIBLE_TO_USER], [user_id, user_id]
    if unseen_only:
        where.append("r.seen_at IS NULL")
    if kind:
        where.append("e.kind = ?")
        params.append(kind)
    sql = ("SELECT e.id, e.kind, e.ref_id, e.payload, e.created_at, r.seen_at FROM events e "
           "LEFT JOIN event_reads r ON r.event_id = e.id AND r.user_id = ? "
           "WHERE " + " AND ".join(where) + " ORDER BY e.created_at DESC LIMIT ?")
    params.append(limit)
    conn = db.get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_shape_event(r) for r in rows]


def unseen_count(user_id: int = LOCAL_USER_ID) -> int:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM events e WHERE " + VISIBLE_TO_USER + " AND NOT EXISTS "
        "(SELECT 1 FROM event_reads r WHERE r.event_id = e.id AND r.user_id = ?)",
        (user_id, user_id)).fetchone()
    conn.close()
    return (row["n"] if row else 0) or 0


def mark_seen(ids: list = None, all_unseen: bool = False, user_id: int = LOCAL_USER_ID) -> dict:
    """This user's read marks only, and only on events this user can see."""
    conn = db.get_conn()
    now = db.now_iso()
    sql = ("INSERT INTO event_reads (user_id, event_id, seen_at) SELECT ?, e.id, ? FROM events e "
           "WHERE " + VISIBLE_TO_USER)
    params = [user_id, now, user_id]
    if not all_unseen:
        ids = [int(i) for i in (ids or [])]
        if not ids:
            conn.close()
            return {"ok": True}
        sql += " AND e.id IN (%s)" % ",".join("?" * len(ids))
        params += ids
    conn.execute(sql + " ON CONFLICT DO NOTHING", params)
    conn.commit()
    conn.close()
    return {"ok": True}


# ------------------------------------------------------------ delivery (07)

def _html_escape(s) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_message(ev: dict) -> str:
    p = ev["payload"]
    kind = ev["kind"]
    if kind == "outlier":
        text = (f"\U0001F680 <b>Outlier</b>: {_html_escape(p.get('title'))}\n"
               f"×{p.get('outlierScore')} · {p.get('views')} просмотров")
    elif kind == "acceleration":
        text = (f"⚡ <b>Ускорение</b>: {_html_escape(p.get('title'))}\n"
               f"×{p.get('acceleration')} · {p.get('vph24h')} VPH за 24ч")
    elif kind == "title_change":
        text = (f"✏️ <b>Смена заголовка</b>\n"
               f"«{_html_escape(p.get('oldTitle'))}» → «{_html_escape(p.get('newTitle'))}»")
    elif kind == "silence_break":
        text = (f"\U0001F514 <b>Вернулись после паузы</b>: {_html_escape(p.get('title'))}\n"
               f"молчали {p.get('gapDays')} дней")
    elif kind == "channel_gone":
        text = (f"\U0001F6AB <b>Канал больше не доступен</b>: {_html_escape(p.get('title'))}\n"
               f"было {p.get('subscribers')} подписчиков · {p.get('views')} просмотров · "
               f"{p.get('videoCount')} видео\n"
               f"не отвечает API с {(p.get('goneSince') or '')[:10]}")
    elif kind == "video_gone":
        text = (f"\U0001F6AB <b>Видео больше не доступно</b>: {_html_escape(p.get('title'))}\n"
               f"было outlier ×{p.get('outlierScore')} · {p.get('views')} просмотров")
    else:
        text = f"{_html_escape(kind)}: {_html_escape(json.dumps(p, ensure_ascii=False))}"

    links = []
    if p.get("videoId"):
        links.append(f'<a href="https://www.youtube.com/watch?v={p["videoId"]}">YouTube</a>')
    if p.get("channelId"):
        links.append(f'<a href="{DASHBOARD_URL}/#/channel/{p["channelId"]}">дашборд</a>')
    if links:
        text += "\n" + " · ".join(links)
    return text


def deliver(max_per_cycle: int = NOTIFY_MAX_PER_CYCLE) -> dict:
    """Stage 07: send every event not yet delivered to Telegram/webhook, up
    to max_per_cycle individually plus one summary line for the rest.
    Skips entirely (no DB read at all) when no notifier is configured --
    NullNotifier means "feature off", not "queue forever". A send failure
    is logged by the notifier itself and simply leaves that event
    undelivered for the next cycle to retry; it never raises, so a bad
    token/URL cannot take the worker down (mirrors _safe() in
    worker_cycle.py, which also wraps this call)."""
    if NOTIFY_MODE == "digest":
        return {"skipped": True,
               "hint": "NOTIFY_MODE=digest -- events go out in the daily digest "
                       "(application/digest.py), not one by one"}
    notifier = notify_factory.get_notifier()
    if isinstance(notifier, NullNotifier):
        return {"skipped": True,
               "hint": "no NOTIFY_TELEGRAM_BOT_TOKEN/NOTIFY_TELEGRAM_CHAT_ID or "
                       "NOTIFY_WEBHOOK_URL configured"}

    conn = db.get_conn()
    try:
        # instant delivery goes to the channel configured in .env, i.e. the
        # local user's (per-user notification settings: plan 15, 5.9)
        rows = conn.execute(
            "SELECT e.id, e.kind, e.ref_id, e.payload, e.created_at, NULL AS seen_at FROM events e "
            "WHERE " + VISIBLE_TO_USER + " ORDER BY e.created_at ASC", (LOCAL_USER_ID,)
        ).fetchall()
        events = [_shape_event(r) for r in rows]
        keys = [str(e["id"]) for e in events]
        delivered_keys = db.already_delivered_alert_keys(conn, keys)
        pending = [e for e in events if str(e["id"]) not in delivered_keys]
        if not pending:
            return {"sent": 0, "summarized": 0, "failed": 0}

        channel = notify_factory.display_target()
        to_send, rest = pending[:max_per_cycle], pending[max_per_cycle:]

        sent = failed = 0
        for ev in to_send:
            if notifier.send(_format_message(ev)):
                db.mark_alert_delivered(conn, str(ev["id"]), channel)
                sent += 1
            else:
                failed += 1
        conn.commit()

        summarized = 0
        if rest:
            by_kind = {}
            for ev in rest:
                by_kind[ev["kind"]] = by_kind.get(ev["kind"], 0) + 1
            lines = [f"\U0001F4EC И ещё {len(rest)} алертов:"]
            lines += [f"• {_html_escape(k)}: {n}" for k, n in by_kind.items()]
            lines.append(f'<a href="{DASHBOARD_URL}/#/data">открыть дашборд</a>')
            if notifier.send("\n".join(lines)):
                for ev in rest:
                    db.mark_alert_delivered(conn, str(ev["id"]), channel)
                summarized = len(rest)
            else:
                failed += len(rest)
            conn.commit()

        return {"sent": sent, "summarized": summarized, "failed": failed}
    finally:
        conn.close()

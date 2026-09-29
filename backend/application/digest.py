"""Daily digest (plan 07): one Telegram/webhook message a day instead of (or
on top of) one message per alert. Zero YouTube quota -- it only reads what
the worker already stored.

NOTIFY_MODE picks the delivery style: `instant` (default, per-event
messages as before), `digest` (only this summary; per-event delivery in
application/alerts.py stays silent) or `both`. The worker calls
send_digest() every cycle; it sends once per local day, not before
DIGEST_HOUR (container time zone, TZ in docker-compose.yml), and remembers
the day in `meta` so a restart never sends twice.
"""
import json
import os
from datetime import datetime

import infrastructure.postgres as db
from application import channel_tracking as T
from application import packaging as PKG
from domain import periods as P
from domain.users import LOCAL_USER_ID, multi_user_enabled
from infrastructure.notify.null import NullNotifier

DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "8"))
DIGEST_SKIP_EMPTY = os.environ.get("DIGEST_SKIP_EMPTY", "1") not in ("0", "false", "no")
DASHBOARD_URL = os.environ.get("NOTIFY_DASHBOARD_URL", "http://127.0.0.1:8080").rstrip("/")
TELEGRAM_LIMIT = 4096
TITLE_MAX = 80
LAST_SENT_KEY = "digest_last_sent_date"

# section -> (event kinds, payload field that ranks items, strongest first)
EVENT_SECTIONS = {
    "outliers": (("outlier",), "outlierScore"),
    "acceleration": (("acceleration",), "acceleration"),
    "gone": (("channel_gone", "video_gone"), None),
}


def _esc(s) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _compact(n) -> str:
    """1234 -> 1.2K, 36800000 -> 36.8M (Telegram text, no locale needed)."""
    n = n or 0
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suffix
    return str(int(n))


def _cut(s, n=TITLE_MAX) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _events(conn, kinds, start, user_id=LOCAL_USER_ID) -> list:
    from application.alerts import DELIVERABLE_TO_USER
    q = ("SELECT id, kind, ref_id, payload, created_at FROM events e WHERE kind IN (%s) AND "
         % ",".join("?" * len(kinds))) + DELIVERABLE_TO_USER
    params = list(kinds) + [user_id]
    if start:
        q += " AND created_at >= ?"
        params.append(start)
    out = []
    for r in conn.execute(q + " ORDER BY created_at DESC", params).fetchall():
        try:
            payload = json.loads(r["payload"]) if r["payload"] else {}
        except (TypeError, ValueError):
            payload = {}
        out.append({"id": r["id"], "kind": r["kind"], "createdAt": r["created_at"],
                    "payload": payload})
    return out


def build_digest(period: str = "24h", top_n: int = 5, user_id: int = LOCAL_USER_ID) -> dict:
    """Everything worth a morning glance for the last `period`: new outliers
    and accelerating videos on tracked channels, channels that just entered
    the corpus already outperforming, title/thumbnail swaps, and channels or
    videos that disappeared. Each section: `total` plus the `top_n` strongest
    `items`. `eventIds` lists every event the digest covers (not only the
    shown ones) so digest mode can mark them delivered."""
    start, _ = P.window(period)
    conn = db.get_conn()
    try:
        out, event_ids = {}, []
        for name, (kinds, rank_field) in EVENT_SECTIONS.items():
            evs = _events(conn, kinds, start, user_id)
            event_ids += [e["id"] for e in evs]
            if rank_field:
                evs.sort(key=lambda e: e["payload"].get(rank_field) or 0, reverse=True)
            out[name] = {"total": len(evs), "items": evs[:top_n]}
    finally:
        conn.close()

    rc = T.recently_added_outlier_channels(period=period, limit=top_n)
    rising = rc.get("channels", [])
    out["risingChannels"] = {"total": rc.get("channelsMatched", len(rising)),
                             "items": rising[:top_n]}
    # multi-user: only swaps on this user's watchlist, not everyone's
    swaps = PKG.packaging_feed(period=period, limit=top_n,
                               user_id=user_id if multi_user_enabled() else None
                               ).get("changes", [])
    out["repackaging"] = {"total": len(swaps), "items": swaps[:top_n]}

    out["period"] = period
    out["eventIds"] = event_ids
    out["empty"] = not any(out[s]["total"] for s in
                           ("outliers", "acceleration", "gone", "risingChannels", "repackaging"))
    return out


# ------------------------------------------------------------ formatting

def _event_line(e) -> str:
    p = e["payload"]
    if e["kind"] == "outlier":
        return f"• ×{p.get('outlierScore')} · {_esc(_cut(p.get('title') or p.get('videoId')))}"
    if e["kind"] == "acceleration":
        return f"• ×{p.get('acceleration')} · {_esc(_cut(p.get('title') or p.get('videoId')))}"
    if e["kind"] == "channel_gone":
        return f"• канал {_esc(_cut(p.get('title') or p.get('channelId')))}"
    if e["kind"] == "video_gone":
        return f"• видео {_esc(_cut(p.get('title') or p.get('videoId')))}"
    return f"• {_esc(e['kind'])}"


def _channel_line(c) -> str:
    return (f"• {_esc(_cut(c.get('channelTitle') or c.get('channelId')))} — "
            f"×{c.get('multiplier')}, {_compact(c.get('subscribers'))} подп.")


def _swap_line(s) -> str:
    if s.get("field") == "title":
        return (f"• заголовок: «{_esc(_cut(s.get('old'), 50))}» → "
                f"«{_esc(_cut(s.get('new'), 50))}»")
    return f"• обложка: {_esc(_cut(s.get('title') or s.get('videoId')))}"


SECTIONS = (
    ("outliers", "\U0001F680 <b>Новые outlier-видео</b>", _event_line),
    ("acceleration", "⚡ <b>Ускоряются</b>", _event_line),
    ("risingChannels", "\U0001F4C8 <b>Растущие каналы</b>", _channel_line),
    ("repackaging", "✏️ <b>Перепаковки</b>", _swap_line),
    ("gone", "\U0001F6AB <b>Пропали</b>", _event_line),
)


def _render(d, per_section: int) -> str:
    lines = ["\U0001F5DE <b>niche-finder — за сутки</b>"]
    for key, header, line_fn in SECTIONS:
        sec = d.get(key) or {}
        if not sec.get("total"):
            continue
        shown = sec["items"][:per_section]
        lines.append("")
        lines.append(f"{header} ({sec['total']})")
        lines += [line_fn(x) for x in shown]
        if sec["total"] > len(shown):
            lines.append(f"…и ещё {sec['total'] - len(shown)}")
    lines.append("")
    lines.append(f'<a href="{DASHBOARD_URL}/#/overview">открыть дашборд</a>')
    return "\n".join(lines)


def format_digest(d: dict) -> str:
    """Telegram HTML, never longer than TELEGRAM_LIMIT: if the full list does
    not fit, fewer items are shown per section (the rest become "…и ещё N")."""
    most = max((len((d.get(k) or {}).get("items", [])) for k, _, _ in SECTIONS), default=0)
    for per_section in range(most, -1, -1):
        text = _render(d, per_section)
        if len(text) <= TELEGRAM_LIMIT:
            return text
    return text[:TELEGRAM_LIMIT]


# ------------------------------------------------------------ sending

def _last_sent_key(user_id):
    # the local user keeps the key digests always used, others get their own
    return LAST_SENT_KEY if user_id == LOCAL_USER_ID else f"{LAST_SENT_KEY}_u{user_id}"


def send_digest(now: datetime = None, force: bool = False, period: str = "24h",
                user_id: int = LOCAL_USER_ID) -> dict:
    """Send today's digest if it is due. Due = a notifier is configured, the
    local hour is >= DIGEST_HOUR and nothing was sent yet today (force skips
    both time checks). An empty day counts as done when DIGEST_SKIP_EMPTY is
    on; a failed send does not, so the next worker cycle retries. Every
    event the digest covered is marked delivered, so switching NOTIFY_MODE
    back to instant does not replay them one by one."""
    from application import notify_settings as NS
    notifier = NS.notifier_for(user_id)
    if isinstance(notifier, NullNotifier):
        return {"sent": False, "reason": "no notifier",
                "hint": "set NOTIFY_TELEGRAM_BOT_TOKEN + NOTIFY_TELEGRAM_CHAT_ID "
                        "or NOTIFY_WEBHOOK_URL in .env"}
    now = now or datetime.now().astimezone()
    today = now.date().isoformat()

    conn = db.get_conn()
    try:
        if not force:
            if now.hour < DIGEST_HOUR:
                return {"sent": False, "reason": "too early", "digestHour": DIGEST_HOUR}
            if db.get_meta(conn, _last_sent_key(user_id)) == today:
                return {"sent": False, "reason": "already sent today"}

        d = build_digest(period=period, user_id=user_id)
        if d["empty"] and DIGEST_SKIP_EMPTY and not force:
            db.set_meta(conn, _last_sent_key(user_id), today)
            conn.commit()
            return {"sent": False, "reason": "empty"}

        if not notifier.send(format_digest(d)):
            return {"sent": False, "reason": "send failed"}
        for event_id in d["eventIds"]:
            db.mark_alert_delivered(conn, str(event_id), "digest", user_id=user_id)
        db.set_meta(conn, _last_sent_key(user_id), today)
        conn.commit()
        return {"sent": True, "events": len(d["eventIds"]), "date": today}
    finally:
        conn.close()


def digest_wanted() -> bool:
    """Whether anyone gets a digest at all (the worker's cheap pre-check)."""
    from application import notify_settings as NS
    return any(NS.mode_for(uid) in ("digest", "both") for uid in NS.recipients())


def send_all_digests(now: datetime = None) -> dict:
    """plan 15 (5.9): the digest for every user who asked for one."""
    from application import notify_settings as NS
    return {str(uid): send_digest(now=now, user_id=uid) for uid in NS.recipients()
            if NS.mode_for(uid) in ("digest", "both")}

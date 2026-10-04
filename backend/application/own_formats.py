"""Your own channel's formats (plan 24): a year of YouTube Analytics rows by
day and creatorContentType, kept in own_channel_daily (personal, like the
rest of your channel's numbers), and what they say -- domain/own_formats.py.
Analytics API quota only, through your own OAuth token.
"""
from datetime import date, timedelta

import infrastructure.postgres as db
from domain import own_formats as F
from domain.users import LOCAL_USER_ID
from infrastructure.youtube import analytics as YA

DAYS = 365
METRICS = ["views", "estimatedMinutesWatched", "subscribersGained"]


def sync_daily(conn, token, user_id, channel_id, end: date) -> int:
    """One Analytics query: the last DAYS days by day and format. Returns
    how many rows were stored; the caller commits."""
    start = end - timedelta(days=DAYS - 1)
    rows = YA.report(token, channel_id, start.isoformat(), end.isoformat(), METRICS,
                     dimensions="day,creatorContentType", sort="day")
    for r in rows:
        conn.execute(
            "INSERT INTO own_channel_daily (user_id, channel_id, day, content_type, views, "
            "minutes_watched, subscribers_gained) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (user_id, channel_id, day, content_type) DO UPDATE SET "
            "views = EXCLUDED.views, minutes_watched = EXCLUDED.minutes_watched, "
            "subscribers_gained = EXCLUDED.subscribers_gained",
            (user_id, channel_id, r["day"], r["creatorContentType"], r.get("views"),
             r.get("estimatedMinutesWatched"), r.get("subscribersGained")))
    return len(rows)


def formats(channel_id: str, user_id: int = LOCAL_USER_ID) -> dict:
    """Shorts against long videos and watch hours toward YPP for one of your
    connected channels, from the stored rows (no API call)."""
    from application import own_channels as OWN
    conn = db.get_conn()
    try:
        OWN._owned(conn, user_id, channel_id)
        rows = [{"day": r["day"], "contentType": r["content_type"], "views": r["views"],
                 "minutes": r["minutes_watched"], "subscribers": r["subscribers_gained"]}
                for r in conn.execute(
                    "SELECT day, content_type, views, minutes_watched, subscribers_gained "
                    "FROM own_channel_daily WHERE user_id = ? AND channel_id = ? ORDER BY day",
                    (user_id, channel_id)).fetchall()]
    finally:
        conn.close()
    if not rows:
        return {"channelId": channel_id, "available": False,
                "hint": "no format data yet -- it arrives with the next sync of your channel"}
    end = F._day(rows[-1]["day"])
    return {"channelId": channel_id, "available": True, "through": end.isoformat(),
            "last90": F.summary(rows, end, days=90), "weekly": F.weekly(rows, end),
            "shortsVsLong": F.shorts_vs_long(rows, end), "watchHours": F.watch_hours(rows, end),
            "note": "Your own YouTube Analytics data; shares, r and dates are estimates of "
                    "niche-finder."}

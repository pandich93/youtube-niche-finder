"""YPP thresholds a channel visibly meets (plan 12) -- the rules and the honest
caveats are in domain/monetization.py. Reads the channel row and the videos we
collected in the last 90 days; zero quota, nothing fetched from YouTube.
"""
import infrastructure.postgres as db
from domain import metrics as M
from domain import monetization as MZ
from domain import periods as P


def for_channel(channel_id: str) -> dict:
    conn = db.get_conn()
    try:
        ch = conn.execute("SELECT subscriber_count, hidden_subs FROM channels WHERE channel_id = ?",
                          (channel_id,)).fetchone()
        start, _ = P.window(f"{MZ.WINDOW_DAYS}d")
        rows = conn.execute(
            "SELECT published_at, view_count, duration_seconds, is_short FROM videos "
            "WHERE channel_id = ? AND published_at >= ?", (channel_id, start)).fetchall()
    finally:
        conn.close()
    if not ch:
        return MZ.ypp_eligibility(None, False, [])
    videos = [{"ageDays": P.days_since(r["published_at"]), "views": r["view_count"],
               "isShort": bool(r["is_short"]) if r["is_short"] is not None
               else M.is_short(r["duration_seconds"])} for r in rows]
    return MZ.ypp_eligibility(ch["subscriber_count"], bool(ch["hidden_subs"]), videos)

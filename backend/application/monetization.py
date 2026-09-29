"""YPP thresholds a channel visibly meets (plan 12) -- the rules and the honest
caveats are in domain/monetization.py. Reads the channel row and the videos we
collected in the last 90 days; zero quota, nothing fetched from YouTube.
"""
import infrastructure.postgres as db
from domain import metrics as M
from domain import monetization as MZ
from domain import periods as P


def _video_input(r) -> dict:
    return {"ageDays": P.days_since(r["published_at"]), "views": r["view_count"],
            "isShort": bool(r["is_short"]) if r["is_short"] is not None
            else M.is_short(r["duration_seconds"])}


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
    videos = [_video_input(r) for r in rows]
    return MZ.ypp_eligibility(ch["subscriber_count"], bool(ch["hidden_subs"]), videos)


def statuses(channel_ids) -> dict:
    """{channel_id: status} for many channels in two queries -- what the
    min_ypp_status filters of the search screens use."""
    ids = list(dict.fromkeys(channel_ids))
    chans, videos = {}, {cid: [] for cid in ids}
    start, _ = P.window(f"{MZ.WINDOW_DAYS}d")
    conn = db.get_conn()
    try:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            for r in conn.execute("SELECT channel_id, subscriber_count, hidden_subs FROM channels "
                                  f"WHERE channel_id IN ({marks})", chunk).fetchall():
                chans[r["channel_id"]] = r
            for r in conn.execute(
                    "SELECT channel_id, published_at, view_count, duration_seconds, is_short "
                    f"FROM videos WHERE channel_id IN ({marks}) AND published_at >= ?",
                    chunk + [start]).fetchall():
                videos[r["channel_id"]].append(_video_input(r))
    finally:
        conn.close()
    out = {}
    for cid in ids:
        ch = chans.get(cid)
        out[cid] = (MZ.ypp_eligibility(ch["subscriber_count"], bool(ch["hidden_subs"]),
                                       videos[cid])["status"] if ch else "unknown")
    return out


def keep_channels(channel_ids, min_status: str) -> set:
    """The ids whose visible YPP status is at least min_status."""
    return {cid for cid, st in statuses(channel_ids).items() if MZ.meets(st, min_status)}

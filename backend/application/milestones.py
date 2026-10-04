"""Subscriber milestones of one channel (plan 17) -- the rules are in
domain/milestones.py. Reads the subscriber snapshots the worker takes; zero
quota, nothing fetched from YouTube.
"""
import infrastructure.postgres as db
from domain import milestones as MS
from domain import monetization as MZ
from domain import periods as P


def for_channel(channel_id: str) -> dict:
    conn = db.get_conn()
    try:
        ch = conn.execute("SELECT subscriber_count, hidden_subs FROM channels WHERE channel_id = ?",
                          (channel_id,)).fetchone()
        rows = conn.execute(
            "SELECT captured_at, subscriber_count FROM channel_stats_history "
            "WHERE channel_id = ? AND subscriber_count IS NOT NULL ORDER BY captured_at",
            (channel_id,)).fetchall()
    finally:
        conn.close()
    if not ch or ch["hidden_subs"] or ch["subscriber_count"] is None:
        return {"subscribers": None, "forecasts": [], "reason": "subscribers-hidden",
                "note": MS.NOTE}
    points = [(P.parse_utc(r["captured_at"]), r["subscriber_count"]) for r in rows
              if P.parse_utc(r["captured_at"])]
    current = points[-1][1] if points else ch["subscriber_count"]
    forecasts = [MS.forecast(points, t) for t in MS.next_targets(current)]
    out = {"subscribers": current, "forecasts": forecasts, "note": MS.NOTE}
    # plan 17: 1,000 subscribers stays a YPP bar after 2027-02-01 too; say
    # whether the pace gets there before the rules change
    yppf = next((f for f in forecasts if f["target"] == MZ.TIERS_2027["full"]["subscribers"]), None)
    if yppf:
        eta = yppf["eta30"] or yppf["eta90"]
        out["ypp1000BeforeRules2027"] = (None if eta is None
                                         else eta < MZ.RULES_2027_FROM.isoformat())
    return out


def latest_pairs(conn, channel_ids) -> list:
    """[{channel_id, title, previous, current}] -- the two latest subscriber
    snapshots of each channel, for the milestone alert. Channels with a
    hidden count are left out."""
    if not channel_ids:
        return []
    marks = ",".join("?" * len(channel_ids))
    rows = conn.execute(
        "SELECT x.channel_id, x.subscriber_count, x.captured_at, x.rn, c.title FROM ("
        "  SELECT channel_id, subscriber_count, captured_at, ROW_NUMBER() OVER "
        "  (PARTITION BY channel_id ORDER BY captured_at DESC) AS rn "
        f"  FROM channel_stats_history WHERE channel_id IN ({marks})) x "
        "JOIN channels c ON c.channel_id = x.channel_id "
        "WHERE x.rn <= 2 AND COALESCE(c.hidden_subs, 0) = 0", list(channel_ids)).fetchall()
    by = {}
    for r in rows:
        d = by.setdefault(r["channel_id"], {"channel_id": r["channel_id"], "title": r["title"],
                                            "previous": None, "current": None,
                                            "previousAt": None, "currentAt": None})
        key = "current" if r["rn"] == 1 else "previous"
        d[key], d[key + "At"] = r["subscriber_count"], r["captured_at"]
    return list(by.values())

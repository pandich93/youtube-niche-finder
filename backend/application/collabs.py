"""Collaboration partners (plan 31) -- the rule is in domain/collabs.py. Takes
a wide pool of similar channels (similar_channels: embedding centroids), then
keeps those of your size, active and not templated. Contacts are not in the
YouTube API (e-mails are hidden): the answer is links to channels. Only
channels we collected can be candidates. Zero quota, no LLM.
"""
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from application import search as Q
from application import template_risk as TRA
from domain import collabs as CB
from domain import periods as P

POOL = 200
NOTE = ("Only channels we collected; YouTube does not give out contacts, so these are links to "
        "channels. 'Active' means an upload among the videos we collected.")


def _facts(conn, ids) -> dict:
    """{channel_id: {title, subscribers, lastUpload, growth30dPct}}."""
    out = {}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    for r in conn.execute(f"SELECT channel_id, title, subscriber_count, hidden_subs FROM channels "
                          f"WHERE channel_id IN ({marks})", ids).fetchall():
        out[r["channel_id"]] = {"title": r["title"],
                                "subscribers": None if r["hidden_subs"] else r["subscriber_count"]}
    for r in conn.execute(f"SELECT channel_id, MAX(published_at) AS last FROM videos "
                          f"WHERE channel_id IN ({marks}) GROUP BY channel_id", ids).fetchall():
        out.setdefault(r["channel_id"], {})["lastUpload"] = r["last"]
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    hist = {}
    for r in conn.execute(f"SELECT channel_id, captured_at, subscriber_count FROM channel_stats_history "
                          f"WHERE channel_id IN ({marks}) AND captured_at >= ? ORDER BY captured_at",
                          ids + [since]).fetchall():
        if r["subscriber_count"] is not None:
            hist.setdefault(r["channel_id"], []).append(r["subscriber_count"])
    for cid, subs in hist.items():
        if len(subs) >= 2 and subs[0]:
            out.setdefault(cid, {})["growth30dPct"] = round((subs[-1] - subs[0]) / subs[0] * 100, 1)
    return out


def collab_candidates(channel_id: str, min_ratio: float = CB.MIN_RATIO,
                      max_ratio: float = CB.MAX_RATIO, active_days: int = CB.ACTIVE_DAYS,
                      niche: str = None, limit: int = 10) -> dict:
    """Up to `limit` channels close to this one in topic, between min_ratio
    and max_ratio of its subscribers, with an upload in the last active_days
    and no high template risk -- each with the numbers that picked it."""
    if not 0 < min_ratio <= max_ratio:
        raise ValueError("min_ratio must be above zero and not above max_ratio")
    sim = Q.similar_channels(channel_id, niche=niche, limit=POOL)
    conn = db.get_conn()
    try:
        own = conn.execute("SELECT title, subscriber_count, hidden_subs FROM channels "
                           "WHERE channel_id = ?", (channel_id,)).fetchone()
        pool = sim.get("similar", [])
        facts = _facts(conn, [s["channelId"] for s in pool])
    finally:
        conn.close()
    base = {"channelId": channel_id, "channelTitle": own["title"] if own else None,
            "minRatio": min_ratio, "maxRatio": max_ratio, "activeDays": active_days,
            "poolSize": len(pool), "note": NOTE}
    if not own:
        return {**base, "found": False, "candidates": [],
                "hint": "Channel not in the database -- collect it first."}
    own_subs = None if own["hidden_subs"] else own["subscriber_count"]
    base.update({"found": True, "subscribers": own_subs})
    if not pool:
        return {**base, "candidates": [], "excluded": {},
                "hint": sim.get("hint") or "no similar channels collected yet"}
    if not own_subs:
        return {**base, "candidates": [], "excluded": {},
                "hint": "this channel hides its subscriber count: there is no size to match"}
    kept, excluded = [], {}
    for s in pool:
        f = facts.get(s["channelId"], {})
        c = {"channelId": s["channelId"], "title": f.get("title") or s.get("title"),
             "subscribers": f.get("subscribers"), "similarity": s["similarity"],
             "sizeRatio": CB.size_ratio(f.get("subscribers"), own_subs),
             "lastUpload": f.get("lastUpload"),
             "daysSinceUpload": (round(P.days_since(f["lastUpload"]), 1)
                                 if f.get("lastUpload") else None),
             "growth30dPct": f.get("growth30dPct"), "templateRisk": None}
        why = CB.check(c, own_subs, min_ratio, max_ratio, active_days)
        if why is None:
            # the template score reads the channel's uploads: only for those still in
            risk = TRA.template_risk(c["channelId"])
            c["templateRisk"] = risk.get("level") if risk.get("found") else None
            why = CB.check(c, own_subs, min_ratio, max_ratio, active_days)
        if why:
            excluded[why] = excluded.get(why, 0) + 1
            continue
        kept.append(c)
        if len(kept) >= limit * 3:
            break
    kept.sort(key=CB.sort_key)
    return {**base, "candidates": kept[:limit], "excluded": excluded}

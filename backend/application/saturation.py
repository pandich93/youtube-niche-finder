"""Niche saturation trend (plan 08): the rules are in domain/saturation.py,
this reads the last 120 days from Postgres for a niche, a set of channels
(niche_overview_from_channel, a cluster) or every niche at once. It always
reads its own window, whatever period the caller asked the overview for --
a trend needs the 90 days before the last 30. Zero quota, no LLM.
"""
import infrastructure.postgres as db
from application import discovery as trends
from domain import periods as P
from domain import saturation as S

WINDOW = f"{S.WINDOW_DAYS}d"
CAUGHT_YOUNG_DAYS = 3


def _channel_ages(channel_ids) -> dict:
    ids = list(channel_ids)
    if not ids:
        return {}
    conn = db.get_conn()
    try:
        out = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute("SELECT channel_id, published_at FROM channels WHERE channel_id IN (%s)"
                                  % ",".join("?" * len(chunk)), chunk).fetchall():
                out[r["channel_id"]] = P.days_since(r["published_at"]) if r["published_at"] else None
        return out
    finally:
        conn.close()


def _video(r) -> dict:
    caught = None
    if r.get("first_seen_at") and r.get("published_at"):
        caught = (r["ageDays"] - P.days_since(r["first_seen_at"])) < CAUGHT_YOUNG_DAYS
    return {"channelId": r["channel_id"], "ageDays": r["ageDays"],
            "projectedViews": r.get("projected30dViews"), "outlierScore": r.get("outlierScore"),
            "isShort": bool(r.get("isShort")), "caughtYoung": caught}


def from_rows(rows, ages=None) -> dict:
    """Saturation of whatever rows load_window returned (already <= 120 days)."""
    if ages is None:
        ages = _channel_ages({r["channel_id"] for r in rows})
    return S.saturation([_video(r) for r in rows], ages)


def niche_saturation(niche: str) -> dict:
    return from_rows(trends.load_window(period=WINDOW, niche=niche))


def channels_saturation(channel_ids) -> dict:
    return from_rows(trends.load_window(period=WINDOW, channel_ids=list(channel_ids)))


def grouped(rows, groups: dict) -> dict:
    """{key: saturation} for {key: set of video ids} -- one read of rows and
    channel ages shared by every group (all niches, all clusters)."""
    ages = _channel_ages({r["channel_id"] for r in rows})
    return {key: S.saturation([_video(r) for r in rows if r["video_id"] in ids], ages)
            for key, ids in groups.items()}


def all_niches_saturation() -> dict:
    """Every niche's status in one pass, for the niche list."""
    rows = trends.load_window(period=WINDOW)
    ids = {r["video_id"] for r in rows}
    conn = db.get_conn()
    try:
        slugs = [r["slug"] for r in conn.execute("SELECT slug FROM niches ORDER BY slug").fetchall()]
        members = {s: set() for s in slugs}
        for r in conn.execute("SELECT video_id, niche_slug FROM video_niches").fetchall():
            if r["video_id"] in ids and r["niche_slug"] in members:
                members[r["niche_slug"]].add(r["video_id"])
    finally:
        conn.close()
    result = grouped(rows, members)
    return {"niches": [{"niche": s, **result[s]} for s in slugs]}

"""Keep stored YouTube data fresh (plan 16).

YouTube API Developer Policies III.E.4 allow keeping data fetched with an API
key for at most 30 days before it is refreshed or deleted. The worker's
regular refreshes only cover recent videos (hot: 7 days, full: 30 days) and
videos.batchGetStats returns counters, not titles or descriptions. This
re-reads everything older than REFRESH_STALE_DAYS with videos.list /
channels.list -- texts and counters -- oldest first, under a daily cap.

Nothing is deleted, here or anywhere else (the user's call, 2026-10-04): a
row the API no longer returns is marked in gone_items (plan 04) and kept.
Keeping data longer than 30 days without refreshing it, or keeping history,
is the installation owner's own risk; SECURITY.md and PRIVACY.md say so.
"""
import os
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from application import collecting as collector

STALE_DAYS = int(os.environ.get("REFRESH_STALE_DAYS", "25"))
MAX_VIDEOS = int(os.environ.get("REFRESH_STALE_MAX_VIDEOS", "2500"))      # ~50 units
MAX_CHANNELS = int(os.environ.get("REFRESH_STALE_MAX_CHANNELS", "2500"))  # ~50 units
CHUNK = 500        # ids per commit, so a quota stop mid-run keeps what was done

_NOT_GONE = ("NOT EXISTS (SELECT 1 FROM gone_items g WHERE g.kind = '{kind}' "
             "AND g.ref_id = t.{col} AND g.confirmed_at IS NOT NULL)")


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _stale_ids(conn, table, col, kind, cutoff, limit) -> list:
    if limit <= 0:
        return []
    return [r[col] for r in conn.execute(
        f"SELECT t.{col} FROM {table} t WHERE (t.updated_at IS NULL OR t.updated_at < ?) "
        f"AND {_NOT_GONE.format(kind=kind, col=col)} "
        "ORDER BY t.updated_at ASC NULLS FIRST LIMIT ?", (cutoff, limit)).fetchall()]


def status(stale_days: int = None) -> dict:
    """How much stored data is older than the refresh age -- for the Data
    screen and db_stats. Confirmed-gone rows are counted apart: the worker
    no longer asks for them."""
    days = STALE_DAYS if stale_days is None else stale_days
    cutoff = _cutoff(days)
    conn = db.get_conn()
    try:
        def block(table, col, kind):
            r = conn.execute(
                f"SELECT COUNT(*) AS total, "
                f"SUM(CASE WHEN (t.updated_at IS NULL OR t.updated_at < ?) "
                f"AND {_NOT_GONE.format(kind=kind, col=col)} THEN 1 ELSE 0 END) AS stale, "
                f"MIN(t.updated_at) AS oldest FROM {table} t", (cutoff,)).fetchone()
            return {"total": r["total"], "stale": int(r["stale"] or 0),
                    "oldestUpdatedAt": r["oldest"]}
        return {"staleDays": days, "videos": block("videos", "video_id", "video"),
                "channels": block("channels", "channel_id", "channel"),
                "historySince": conn.execute(
                    "SELECT MIN(captured_at) FROM video_stats_history").fetchone()[0],
                "deletes": False,
                "dailyCap": {"videos": MAX_VIDEOS, "channels": MAX_CHANNELS}}
    finally:
        conn.close()


def refresh_stale(api_key: str, stale_days: int = None, max_videos: int = None,
                  max_channels: int = None) -> dict:
    """Re-read stored videos and channels not refreshed for `stale_days`,
    oldest first. ~1 unit per 50 rows from the shared pool. QuotaExceeded
    propagates: what was committed before it stays, nothing is marked."""
    days = STALE_DAYS if stale_days is None else stale_days
    max_videos = MAX_VIDEOS if max_videos is None else max_videos
    max_channels = MAX_CHANNELS if max_channels is None else max_channels
    cutoff = _cutoff(days)
    conn = db.get_conn()
    try:
        video_ids = _stale_ids(conn, "videos", "video_id", "video", cutoff, max_videos)
        channel_ids = _stale_ids(conn, "channels", "channel_id", "channel", cutoff, max_channels)
    finally:
        conn.close()

    out = {"videos": {"requested": len(video_ids), "refreshed": 0, "missing": 0},
           "channels": {"requested": len(channel_ids), "refreshed": 0, "missing": 0}}
    for i in range(0, len(video_ids), CHUNK):
        chunk = video_ids[i:i + CHUNK]
        items = yt.videos_list(api_key, chunk)
        conn = db.get_conn()
        try:
            now = db.now_iso()
            # embed=False: a refresh keeps the stored vector; the embedding
            # backfill recomputes it when it is missing
            out["videos"]["refreshed"] += collector.store_videos(conn, items, embed=False, now=now)
            gone = collector._track_gone(conn, "video", chunk, [it["id"] for it in items], now)
            out["videos"]["missing"] += gone["missing"]
            conn.commit()
        finally:
            conn.close()
    for i in range(0, len(channel_ids), CHUNK):
        chunk = channel_ids[i:i + CHUNK]
        items = yt.channels_list(api_key, chunk)
        conn = db.get_conn()
        try:
            now = db.now_iso()
            out["channels"]["refreshed"] += len(collector.store_channels(conn, items, now))
            gone = collector._track_gone(conn, "channel", chunk, [it["id"] for it in items], now)
            out["channels"]["missing"] += gone["missing"]
            conn.commit()
        finally:
            conn.close()
    out["quota"] = {"units_from_shared_pool": (len(video_ids) + 49) // 50
                    + (len(channel_ids) + 49) // 50, "search_calls": 0}
    return out

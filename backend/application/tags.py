"""Curated tags on videos (theme/trigger/format/...) and their outlier-hit
rate within a niche -- turns "this niche breaks out on fear-based titles"
from a chat observation into a queryable number. Stage 03's planned LLM
auto-tagging writes into the same table with source='llm', which is why
protection against 'llm' clobbering a human- or Claude-set tag lives in the
repository layer (infrastructure/postgres/repositories.py), not just here.
"""
import infrastructure.postgres as db
from application import discovery as trends
from domain import tag_stats as TS

VALID_SOURCES = ("manual", "claude-mcp", "llm")


def tag_videos(items: list, source: str, replace: bool = False) -> dict:
    """items: [{"video_id", "tag_group", "tags": [...]}, ...] -- each item
    tags one video in one group; pass several items to cover multiple
    videos/groups in one call. replace=True makes an item's `tags` the full
    set for that (video_id, tag_group), removing anything else already
    there (subject to the same source protection as a plain upsert)."""
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}, got {source!r}")
    if not items:
        raise ValueError("items is required")

    conn = db.get_conn()
    written = blocked = 0
    try:
        for item in items:
            video_id = item.get("video_id")
            tag_group = item.get("tag_group")
            tags = [t for t in (item.get("tags") or []) if t]
            if not video_id or not tag_group:
                raise ValueError("each item needs video_id and tag_group")
            if replace:
                r = db.replace_video_tags(conn, video_id, tag_group, tags, source)
                written += r["written"]
                blocked += r["blocked"]
            else:
                for tag in tags:
                    if db.upsert_video_tag(conn, video_id, tag_group, tag, source):
                        written += 1
                    else:
                        blocked += 1
        conn.commit()
    finally:
        conn.close()
    return {"items": len(items), "written": written, "blockedByProtection": blocked}


def list_video_tags(niche: str = None, video_id: str = None) -> list:
    if bool(niche) == bool(video_id):
        raise ValueError("pass exactly one of niche or video_id")
    conn = db.get_conn()
    if video_id:
        rows = conn.execute(
            "SELECT video_id, tag_group, tag, source, created_at FROM video_tags "
            "WHERE video_id = ? ORDER BY tag_group, tag", (video_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT vt.video_id, vt.tag_group, vt.tag, vt.source, vt.created_at "
            "FROM video_tags vt JOIN video_niches vn ON vn.video_id = vt.video_id "
            "WHERE vn.niche_slug = ? ORDER BY vt.video_id, vt.tag_group, vt.tag",
            (niche,)).fetchall()
    conn.close()
    return [{"videoId": r["video_id"], "tagGroup": r["tag_group"], "tag": r["tag"],
            "source": r["source"], "createdAt": r["created_at"]} for r in rows]


def tag_stats(niche: str, tag_group: str, outlier_threshold: float = 3.0,
              exclude_recent_days: int = 30) -> dict:
    """Per-tag videos/hits/hitRate/lift/medianViews/medianOutlier for one
    tag_group in one niche. lift is measured against the whole niche's hit
    rate (see domain.tag_stats), not just the tagged subset, so it stays
    meaningful before tagging coverage is complete."""
    rows = trends.load_window(period="all", niche=niche)
    if exclude_recent_days:
        rows = [r for r in rows if (r.get("ageDays") or 0) >= exclude_recent_days]
    if not rows:
        return {"niche": niche, "tagGroup": tag_group, "found": False,
                "hint": "nothing collected under this niche yet, or every video is younger "
                        "than exclude_recent_days"}

    video_rows = [{"video_id": r["video_id"], "views": r["view_count"] or 0,
                  "outlier": r["outlierScore"] or r["outlierScoreNexlev"]} for r in rows]
    conn = db.get_conn()
    tag_rows = [{"video_id": t["video_id"], "tag": t["tag"]} for t in conn.execute(
        "SELECT vt.video_id, vt.tag FROM video_tags vt "
        "JOIN video_niches vn ON vn.video_id = vt.video_id "
        "WHERE vn.niche_slug = ? AND vt.tag_group = ?", (niche, tag_group)).fetchall()]
    conn.close()

    result = TS.compute(video_rows, tag_rows, outlier_threshold=outlier_threshold)
    result.update({"niche": niche, "tagGroup": tag_group, "found": True})
    return result

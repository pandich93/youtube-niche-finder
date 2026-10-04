"""Thumbnail similarity (plan 13): which videos look alike, which thumbnails
match a text description, and which visual styles work in a niche.

embed_thumbnails() is the worker step (opt-in, WORKER_THUMB_EMBED): it takes
the 320x180 thumbnail -- from thumbnail_archive when plan 05 already has it,
otherwise from i.ytimg.com, which is not the Data API and costs no quota --
turns it into a CLIP vector and keeps only the vector (videos.thumb_embedding,
plus thumb_embedding_v with an HNSW index when pgvector is there). A thumbnail
swap logged after the vector was taken re-embeds the video; an image that
could not be read is retried after RETRY_DAYS.

CLIP compares style and content, not the words written on the thumbnail.
"""
import statistics as st
import time

import numpy as np

import infrastructure.postgres as db
import infrastructure.thumbnails as TH
from application import discovery as trends
from domain import niche_clusters as NC
from infrastructure.embeddings import image_provider as IP

RETRY_DAYS = 7
MIN_FOR_STYLES = 12
STYLE_EXAMPLES = 4
MAX_LIMIT = 50
NO_VECTOR_HINT = ("this video's thumbnail has no vector yet -- turn on WORKER_THUMB_EMBED "
                  "or run embed_thumbnails")


# ---------------------------------------------------------- embedding

def _candidates(conn, limit, niche=None):
    joins, params = "", []
    if niche:
        joins = " JOIN video_niches vn ON vn.video_id = v.video_id AND vn.niche_slug = ?"
        params.append(niche)
    return [r["video_id"] for r in conn.execute(
        f"SELECT v.video_id FROM videos v{joins} WHERE "
        "((v.thumb_embedding IS NULL AND (v.thumb_embedded_at IS NULL OR "
        "  v.thumb_embedded_at::timestamptz < now() - (? || ' days')::interval)) "
        "OR (v.thumb_embedding IS NOT NULL AND EXISTS (SELECT 1 FROM video_changes c "
        "  WHERE c.video_id = v.video_id AND c.field = 'thumbnail_image' "
        "  AND c.changed_at::timestamptz > v.thumb_embedded_at::timestamptz))) "
        "ORDER BY v.published_at DESC NULLS LAST LIMIT ?",
        params + [str(RETRY_DAYS), limit]).fetchall()]


def _archived(conn, video_id):
    """The newest image plan 05 already keeps for this video, or None."""
    row = conn.execute("SELECT image FROM thumbnail_archive WHERE video_id = ? "
                       "ORDER BY captured_at DESC LIMIT 1", (video_id,)).fetchone()
    return bytes(row["image"]) if row and row["image"] else None


def embed_thumbnails(limit: int = 200, niche: str = None, batch_size: int = 32,
                     pause_seconds: float = 0.2) -> dict:
    """Embed up to `limit` thumbnails (newest first, optionally one niche's)
    that have no vector or a stale one. Never raises on a bad image: it is
    counted in `failed`. Zero quota."""
    conn = db.get_conn()
    out = {"candidates": 0, "embedded": 0, "failed": 0, "downloaded": 0, "fromArchive": 0,
           "niche": niche}
    try:
        ids = _candidates(conn, limit, niche)
        out["candidates"] = len(ids)
        for i in range(0, len(ids), batch_size):
            chunk = ids[i:i + batch_size]
            images = []
            for vid in chunk:
                img = _archived(conn, vid)
                if img is not None:
                    out["fromArchive"] += 1
                else:
                    if out["downloaded"] and pause_seconds:
                        time.sleep(pause_seconds)     # a gentle pace for i.ytimg.com
                    img = TH.fetch_thumbnail(vid)
                    out["downloaded"] += 1
                images.append(img)
            vectors = IP.embed_images(images)
            now = db.now_iso()
            for vid, vec in zip(chunk, vectors):
                if vec is None:
                    out["failed"] += 1
                    conn.execute("UPDATE videos SET thumb_embedding = NULL, thumb_embedded_at = ? "
                                 "WHERE video_id = ?", (now, vid))
                    continue
                conn.execute("UPDATE videos SET thumb_embedding = ?, thumb_embedded_at = ? "
                             "WHERE video_id = ?", (IP.to_blob(vec), now, vid))
                if db.pgvector_available():
                    conn.execute("UPDATE videos SET thumb_embedding_v = ?::vector WHERE video_id = ?",
                                 (IP.to_pgvector_literal(vec), vid))
                out["embedded"] += 1
            conn.commit()
    finally:
        conn.close()
    return out


# ---------------------------------------------------------- search

def _nearest(conn, vec, niche=None, limit=12, exclude_video=None, exclude_channel=None):
    limit = max(1, min(int(limit), MAX_LIMIT))
    joins, where, params = "", ["v.thumb_embedding IS NOT NULL"], []
    if niche:
        joins = " JOIN video_niches vn ON vn.video_id = v.video_id"
        where.append("vn.niche_slug = ?")
        params.append(niche)
    if exclude_video:
        where.append("v.video_id != ?")
        params.append(exclude_video)
    if exclude_channel:
        where.append("v.channel_id != ?")
        params.append(exclude_channel)
    cols = ("v.video_id, v.title, v.channel_id, v.view_count, v.published_at, v.thumbnail, "
            "c.title AS channel_title")
    base = f"FROM videos v LEFT JOIN channels c ON c.channel_id = v.channel_id{joins} " \
           f"WHERE {' AND '.join(where)}"
    if db.pgvector_available():
        lit = IP.to_pgvector_literal(vec)
        db.filtered_ann(conn)
        rows = conn.execute(
            f"SELECT {cols}, (1 - (v.thumb_embedding_v <=> ?::vector)) AS similarity {base} "
            f"AND v.thumb_embedding_v IS NOT NULL ORDER BY v.thumb_embedding_v <=> ?::vector LIMIT ?",
            [lit] + params + [lit, limit]).fetchall()
        scored = [(dict(r), float(r["similarity"])) for r in rows]
    else:
        rows = conn.execute(f"SELECT {cols}, v.thumb_embedding {base}", params).fetchall()
        scored = sorted(((dict(r), float(np.dot(vec, IP.from_blob(r["thumb_embedding"]))))
                         for r in rows), key=lambda t: t[1], reverse=True)[:limit]
    return [{"videoId": r["video_id"], "title": r["title"], "channelId": r["channel_id"],
             "channelTitle": r["channel_title"], "views": r["view_count"],
             "publishedAt": r["published_at"], "thumbnail": r["thumbnail"],
             "similarity": round(sim, 4)} for r, sim in scored]


def similar_thumbnails(video_id: str, niche: str = None, limit: int = 12,
                       exclude_same_channel: bool = False) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT thumb_embedding, channel_id FROM videos WHERE video_id = ?",
                           (video_id,)).fetchone()
        if not row or not row["thumb_embedding"]:
            return {"videoId": video_id, "similar": [], "hint": NO_VECTOR_HINT}
        vec = IP.from_blob(bytes(row["thumb_embedding"]))
        similar = _nearest(conn, vec, niche=niche, limit=limit, exclude_video=video_id,
                           exclude_channel=row["channel_id"] if exclude_same_channel else None)
    finally:
        conn.close()
    return {"videoId": video_id, "niche": niche, "similar": similar,
            "note": "Visual similarity (CLIP): style and content, not the words on the thumbnail."}


def search_thumbnails(query: str, niche: str = None, limit: int = 12) -> dict:
    if not query or not query.strip():
        raise ValueError("query is required")
    vec = IP.embed_text(query.strip())
    conn = db.get_conn()
    try:
        results = _nearest(conn, vec, niche=niche, limit=limit)
    finally:
        conn.close()
    return {"query": query.strip(), "niche": niche, "results": results,
            "hint": None if results else "no thumbnail vectors yet -- run embed_thumbnails"}


# ---------------------------------------------------------- styles

def thumbnail_styles(niche: str, k: int = None) -> dict:
    """Group a niche's thumbnails into visual styles (k-means over their CLIP
    vectors) and show how each style performs: videos, median outlier score
    and views, and its best examples."""
    rows = {r["video_id"]: r for r in trends.load_window(period="all", niche=niche)}
    conn = db.get_conn()
    try:
        vecs = {}
        ids = list(rows)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute("SELECT video_id, thumb_embedding FROM videos WHERE video_id IN (%s) "
                                  "AND thumb_embedding IS NOT NULL" % ",".join("?" * len(chunk)),
                                  chunk).fetchall():
                vecs[r["video_id"]] = IP.from_blob(bytes(r["thumb_embedding"]))
    finally:
        conn.close()
    base = {"niche": niche, "videos": len(rows), "embedded": len(vecs)}
    if len(vecs) < MIN_FOR_STYLES:
        return {**base, "found": False, "styles": [],
                "hint": f"{len(vecs)} of {len(rows)} thumbnails have a vector, {MIN_FOR_STYLES} "
                        "needed -- turn on WORKER_THUMB_EMBED or run embed_thumbnails"}

    order = list(vecs)
    k = k or NC.choose_k(len(order), target_cluster_size=8, k_min=2, k_max=8)
    labels = NC.kmeans(np.stack([vecs[v] for v in order]), k)["labels"]
    styles = []
    for label in sorted(set(labels)):
        members = [rows[v] for v, lab in zip(order, labels) if lab == label]
        outliers = [m["outlierScore"] for m in members if m.get("outlierScore") is not None]
        best = sorted(members, key=lambda m: (m.get("outlierScore") or 0, m["view_count"] or 0),
                      reverse=True)[:STYLE_EXAMPLES]
        styles.append({
            "style": len(styles) + 1, "videos": len(members),
            "share": round(len(members) / len(order), 3),
            "medianOutlierScore": round(st.median(outliers), 2) if outliers else None,
            "medianViews": int(st.median([m["view_count"] or 0 for m in members])),
            "examples": [{"videoId": m["video_id"], "title": m["title"],
                          "thumbnail": m.get("thumbnail"), "outlierScore": m.get("outlierScore"),
                          "views": m["view_count"]} for m in best]})
    styles.sort(key=lambda s: (s["medianOutlierScore"] is None, -(s["medianOutlierScore"] or 0)))
    for i, s in enumerate(styles, 1):
        s["style"] = i
    return {**base, "found": True, "styles": styles,
            "note": "Styles are k-means groups of CLIP vectors: a look, not a rule. The outlier "
                    "score is the median of each group's videos, a correlation, not a cause."}

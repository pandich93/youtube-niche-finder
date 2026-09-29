"""Repackaging (plan 05): creators swapping a video's title or thumbnail after
publishing, and what happened to its views around the swap.

Titles are already logged by refresh_stats (video_changes, field='title').
Thumbnails are not visible in the API at all -- the URL stays the same while
the image behind it changes -- so fingerprint_thumbnails() downloads each
tracked-channel video's thumbnail from i.ytimg.com (zero quota), keeps every
distinct version in thumbnail_archive and logs a change as
field='thumbnail_image'. The older URL-comparison rows (field='thumbnail')
are ignored here: they never reflect a new image.
"""
import time
from urllib.parse import quote

import infrastructure.postgres as db
import infrastructure.thumbnails as TH
from domain import packaging as PK
from domain import periods as P

FIELDS = ("title", "thumbnail_image")
EFFECT_NOTE = ("effect = views per hour in the 48h before vs after the change; it is "
               "observed, not caused -- views also decay with the video's age")


def _image_path(video_id: str, captured_at: str) -> str:
    return f"/api/thumbnails/{quote(video_id, safe='')}/{quote(captured_at, safe='')}.jpg"


# ---------------------------------------------------------- fingerprinting

def fingerprint_thumbnails(period: str = "30d", limit: int = 500,
                           pause_seconds: float = 0.2) -> dict:
    """Worker step: fingerprint the current thumbnail of recent videos of
    tracked channels. First sight of a video -> baseline row in
    thumbnail_archive; a different image (dHash distance above
    domain.packaging.THUMB_CHANGE_BITS) -> new archive row plus a
    video_changes row. An unreachable or unreadable image is counted in
    `failed` and skipped, never raised. Zero YouTube API quota."""
    conn = db.get_conn()
    start, _ = P.window(period)
    # any user's tracked channel, each video once (plan 15)
    q = ("SELECT v.video_id FROM videos v WHERE EXISTS (SELECT 1 FROM tracked_channels t "
         "WHERE t.channel_id = v.channel_id AND t.active = 1)")
    params = []
    if start:
        q += " AND v.published_at >= ?"
        params.append(start)
    q += " ORDER BY v.published_at DESC LIMIT ?"
    params.append(limit)
    ids = [r["video_id"] for r in conn.execute(q, params).fetchall()]

    out = {"checked": 0, "baselined": 0, "changed": 0, "failed": 0, "period": period}
    try:
        for i, vid in enumerate(ids):
            if i and pause_seconds:
                time.sleep(pause_seconds)
            out["checked"] += 1
            image = TH.fetch_thumbnail(vid)
            h = TH.dhash(image)
            if not h:
                out["failed"] += 1
                continue
            last = conn.execute(
                "SELECT dhash FROM thumbnail_archive WHERE video_id=? "
                "ORDER BY captured_at DESC LIMIT 1", (vid,)).fetchone()
            last_hash = last["dhash"] if last else None
            if last and not PK.thumbnail_changed(last_hash, h):
                continue
            now = db.now_iso()
            conn.execute(
                "INSERT INTO thumbnail_archive (video_id, captured_at, dhash, image) "
                "VALUES (?,?,?,?) ON CONFLICT (video_id, captured_at) DO NOTHING",
                (vid, now, h, image))
            if last is None:
                out["baselined"] += 1
            else:
                conn.execute(
                    "INSERT INTO video_changes (video_id, changed_at, field, old_value, new_value) "
                    "VALUES (?,?,?,?,?) ON CONFLICT (video_id, changed_at, field) DO NOTHING",
                    (vid, now, "thumbnail_image", last_hash, h))
                out["changed"] += 1
            conn.commit()
    finally:
        conn.commit()
        conn.close()
    return out


# ---------------------------------------------------------- reading

def _archive_times(conn, video_ids) -> dict:
    """video_id -> sorted list of thumbnail_archive.captured_at."""
    out = {}
    ids = list(video_ids)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ("SELECT video_id, captured_at FROM thumbnail_archive WHERE video_id IN (%s) "
             "ORDER BY captured_at" % ",".join("?" * len(chunk)))
        for r in conn.execute(q, chunk).fetchall():
            out.setdefault(r["video_id"], []).append(r["captured_at"])
    return out


def _history(conn, video_id):
    return [(r["captured_at"], r["view_count"]) for r in conn.execute(
        "SELECT captured_at, view_count FROM video_stats_history WHERE video_id=?",
        (video_id,)).fetchall()]


def _shape(conn, r, archive) -> dict:
    item = {
        "videoId": r["video_id"], "title": r["title"], "channelId": r["channel_id"],
        "channelTitle": r["channel_title"], "views": r["view_count"],
        "field": r["field"], "changedAt": r["changed_at"],
        "old": r["old_value"], "new": r["new_value"],
        "beforeImage": None, "afterImage": None,
        "effect": PK.change_effect(_history(conn, r["video_id"]), r["changed_at"]),
    }
    if r["field"] == "thumbnail_image":
        times = archive.get(r["video_id"], [])
        before = [t for t in times if t < r["changed_at"]]
        if before:
            item["beforeImage"] = _image_path(r["video_id"], before[-1])
        if r["changed_at"] in times:
            item["afterImage"] = _image_path(r["video_id"], r["changed_at"])
        item["distanceBits"] = PK.hamming(r["old_value"], r["new_value"])
    return item


_SELECT = ("SELECT ch.video_id, ch.changed_at, ch.field, ch.old_value, ch.new_value, "
           "v.title, v.channel_id, v.view_count, c.title AS channel_title "
           "FROM video_changes ch JOIN videos v ON v.video_id = ch.video_id "
           "LEFT JOIN channels c ON c.channel_id = v.channel_id "
           "WHERE ch.field IN ('title', 'thumbnail_image')")


def packaging_feed(period: str = "30d", channel_id: str = None, field: str = None,
                   limit: int = 50, user_id: int = None) -> dict:
    """Newest title/thumbnail swaps first, each with before/after (titles as
    text, thumbnails as archive image paths) and the views-per-hour effect.
    user_id: only channels on that user's active watchlist (the digest in
    multi-user mode); None keeps the shared feed."""
    if field and field not in FIELDS:
        raise ValueError(f"field must be one of {', '.join(FIELDS)}")
    conn = db.get_conn()
    try:
        start, _ = P.window(period)
        q, params = _SELECT, []
        if start:
            q += " AND ch.changed_at >= ?"
            params.append(start)
        if channel_id:
            q += " AND v.channel_id = ?"
            params.append(channel_id)
        if field:
            q += " AND ch.field = ?"
            params.append(field)
        if user_id is not None:
            q += (" AND EXISTS (SELECT 1 FROM tracked_channels t WHERE t.user_id = ? "
                  "AND t.channel_id = v.channel_id AND t.active = 1)")
            params.append(user_id)
        q += " ORDER BY ch.changed_at DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in conn.execute(q, params).fetchall()]
        archive = _archive_times(conn, {r["video_id"] for r in rows})
        changes = [_shape(conn, r, archive) for r in rows]
    finally:
        conn.close()
    return {"period": period, "count": len(changes), "changes": changes, "note": EFFECT_NOTE}


def packaging_history(video_id: str) -> dict:
    """Every title/thumbnail change of one video plus all archived thumbnail
    versions, oldest version first."""
    conn = db.get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            _SELECT + " AND ch.video_id = ? ORDER BY ch.changed_at DESC", (video_id,)).fetchall()]
        archive = _archive_times(conn, [video_id])
        changes = [_shape(conn, r, archive) for r in rows]
    finally:
        conn.close()
    thumbs = [{"capturedAt": t, "image": _image_path(video_id, t)}
              for t in archive.get(video_id, [])]
    return {"videoId": video_id, "count": len(changes), "changes": changes,
            "thumbnails": thumbs, "note": EFFECT_NOTE}


def thumbnail_image(video_id: str, captured_at: str):
    """Archived JPEG bytes, or None."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT image FROM thumbnail_archive WHERE video_id=? AND captured_at=?",
            (video_id, captured_at)).fetchone()
    finally:
        conn.close()
    return bytes(row["image"]) if row and row["image"] is not None else None

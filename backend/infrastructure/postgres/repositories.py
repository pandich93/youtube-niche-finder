"""Upsert / record / tracking functions -- the write-side repository API.

All functions take an already-open connection (from connection.get_conn())
as their first argument, matching the original db.py call convention used
throughout application/* and interfaces/*.
"""
import json

from infrastructure.postgres.schema import now_iso

_CHANNEL_COLS = [
    "channel_id", "title", "custom_url", "country", "description", "default_language",
    "subscriber_count", "video_count", "view_count", "thumbnail", "updated_at",
    "published_at", "topic_categories", "keywords", "uploads_playlist", "hidden_subs",
]

_VIDEO_COLS = [
    "video_id", "channel_id", "title", "description", "published_at", "duration_seconds",
    "view_count", "like_count", "comment_count", "thumbnail", "tags", "default_language",
    "embedding", "updated_at", "category_id", "region", "is_short", "topic_categories",
    "live_content", "contains_synthetic_media",
]


def _upsert(conn, table, pk, cols, row, coalesce=()):
    """Generic upsert keeping NULLs in `coalesce` columns from overwriting data."""
    data = {c: row.get(c) for c in cols}
    placeholders = ",".join(f"%({c})s" for c in cols)
    updates = []
    for c in cols:
        if c == pk:
            continue
        if c in coalesce:
            updates.append(f"{c}=COALESCE(excluded.{c}, {table}.{c})")
        else:
            updates.append(f"{c}=excluded.{c}")
    sql = (
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk}) DO UPDATE SET {','.join(updates)}"
    )
    cur = conn._conn.cursor()
    cur.execute(sql, data)
    cur.close()


def upsert_channel(conn, ch: dict):
    ch.setdefault("first_seen_at", ch.get("updated_at") or now_iso())
    conn.execute(
        "INSERT INTO channels (channel_id, first_seen_at) VALUES (?,?) "
        "ON CONFLICT (channel_id) DO NOTHING",
        (ch["channel_id"], ch["first_seen_at"]),
    )
    _upsert(conn, "channels", "channel_id", _CHANNEL_COLS, ch,
            coalesce=("published_at", "topic_categories", "keywords", "uploads_playlist"))


def upsert_video(conn, v: dict):
    v.setdefault("first_seen_at", v.get("updated_at") or now_iso())
    conn.execute(
        "INSERT INTO videos (video_id, first_seen_at) VALUES (?,?) "
        "ON CONFLICT (video_id) DO NOTHING",
        (v["video_id"], v["first_seen_at"]),
    )
    _upsert(conn, "videos", "video_id", _VIDEO_COLS, v,
            coalesce=("embedding", "category_id", "region", "topic_categories", "tags"))


def record_video_stats(conn, video_id, view_count, like_count, comment_count,
                       title=None, thumbnail=None, captured_at=None):
    """Append a stats snapshot and log title/thumbnail changes (1of10-style)."""
    captured_at = captured_at or now_iso()
    prev = conn.execute(
        "SELECT title, thumbnail FROM video_stats_history WHERE video_id=? "
        "ORDER BY captured_at DESC LIMIT 1",
        (video_id,),
    ).fetchone()
    conn.execute(
        "INSERT INTO video_stats_history "
        "(video_id, captured_at, view_count, like_count, comment_count, title, thumbnail) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT (video_id, captured_at) DO UPDATE SET "
        "view_count=excluded.view_count, like_count=excluded.like_count, "
        "comment_count=excluded.comment_count, title=excluded.title, "
        "thumbnail=excluded.thumbnail",
        (video_id, captured_at, view_count, like_count, comment_count, title, thumbnail),
    )
    if prev:
        for field, old, new in (("title", prev["title"], title),
                                ("thumbnail", prev["thumbnail"], thumbnail)):
            if old and new and old != new:
                conn.execute(
                    "INSERT INTO video_changes "
                    "(video_id, changed_at, field, old_value, new_value) VALUES (?,?,?,?,?) "
                    "ON CONFLICT (video_id, changed_at, field) DO NOTHING",
                    (video_id, captured_at, field, old, new),
                )


def record_channel_stats(conn, channel_id, subscriber_count, video_count, view_count,
                         captured_at=None):
    conn.execute(
        "INSERT INTO channel_stats_history "
        "(channel_id, captured_at, subscriber_count, video_count, view_count) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT (channel_id, captured_at) DO UPDATE SET "
        "subscriber_count=excluded.subscriber_count, video_count=excluded.video_count, "
        "view_count=excluded.view_count",
        (channel_id, captured_at or now_iso(), subscriber_count, video_count, view_count),
    )


def link_video_niche(conn, video_id: str, niche_slug: str):
    conn.execute(
        "INSERT INTO video_niches (video_id, niche_slug) VALUES (?, ?) "
        "ON CONFLICT (video_id, niche_slug) DO NOTHING",
        (video_id, niche_slug),
    )


def upsert_niche(conn, slug: str, query: str, label: str):
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO niches (slug, query, label, created_at, last_collected_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET last_collected_at=excluded.last_collected_at
        """,
        (slug, query, label, ts, ts),
    )


def track_channel(conn, channel_id: str, note: str = None):
    conn.execute(
        "INSERT INTO tracked_channels (channel_id, note, added_at, active) VALUES (?,?,?,1) "
        "ON CONFLICT(channel_id) DO UPDATE SET active=1, note=COALESCE(excluded.note, tracked_channels.note)",
        (channel_id, note, now_iso()),
    )


def untrack_channel(conn, channel_id: str):
    conn.execute("UPDATE tracked_channels SET active=0 WHERE channel_id=?", (channel_id,))


def new_chart_snapshot(conn, region: str, category_id: str, source: str) -> int:
    row = conn.execute(
        "INSERT INTO chart_snapshots (captured_at, region, category_id, source) "
        "VALUES (?,?,?,?) RETURNING snapshot_id",
        (now_iso(), region, category_id, source),
    ).fetchone()
    return row["snapshot_id"]


def add_chart_entry(conn, snapshot_id: int, video_id: str, rank: int, view_count: int):
    conn.execute(
        "INSERT INTO chart_entries (snapshot_id, video_id, rank, view_count) VALUES (?,?,?,?) "
        "ON CONFLICT (snapshot_id, video_id) DO UPDATE SET "
        "rank=excluded.rank, view_count=excluded.view_count",
        (snapshot_id, video_id, rank, view_count),
    )


def get_meta(conn, key: str):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key: str, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def record_channel_llm_labels(conn, channel_id: str, labels: dict, model: str,
                              labeled_at: str = None):
    """Stage 03: write the background classifier's verdict for one channel
    (is_faceless/content_format/topic/...). Always overwrites -- there is no
    human channel-level label to protect, unlike video_tags."""
    conn.execute(
        "UPDATE channels SET llm_labels=?::jsonb, llm_labeled_at=?, llm_model=? "
        "WHERE channel_id=?",
        (json.dumps(labels), labeled_at or now_iso(), model, channel_id),
    )


def upsert_category(conn, category_id, region, title, assignable):
    conn.execute(
        "INSERT INTO video_categories (category_id, region, title, assignable, updated_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(category_id, region) DO UPDATE SET "
        "title=excluded.title, assignable=excluded.assignable, updated_at=excluded.updated_at",
        (str(category_id), region, title, 1 if assignable else 0, now_iso()),
    )


# 'llm' never overwrites or deletes a tag a human (or Claude, via MCP) set.
PROTECTED_TAG_SOURCES = ("manual", "claude-mcp")


def upsert_video_tag(conn, video_id: str, tag_group: str, tag: str, source: str,
                     created_at: str = None, protected_sources=PROTECTED_TAG_SOURCES,
                     proposed: bool = False) -> bool:
    """Insert or update one (video_id, tag_group, tag) row. A brand new row
    always writes; overwriting an existing row is blocked only when that
    row's source is protected and `source` is not. Returns whether the row
    was actually written.

    proposed=True (stage 03) marks a tag the LLM invented outside the
    niche's existing taxonomy -- it lands in the table so a reviewer can see
    it, but application.tags.tag_stats excludes it until accepted (see
    resolve_proposed_tag)."""
    cur = conn._conn.cursor()
    cur.execute(
        "INSERT INTO video_tags (video_id, tag_group, tag, source, created_at, proposed) "
        "VALUES (%(video_id)s, %(tag_group)s, %(tag)s, %(source)s, %(created_at)s, %(proposed)s) "
        "ON CONFLICT (video_id, tag_group, tag) DO UPDATE SET "
        "source=excluded.source, created_at=excluded.created_at, proposed=excluded.proposed "
        "WHERE NOT (video_tags.source IN %(protected)s AND excluded.source NOT IN %(protected)s) "
        "RETURNING 1",
        {"video_id": video_id, "tag_group": tag_group, "tag": tag, "source": source,
         "created_at": created_at or now_iso(), "protected": tuple(protected_sources),
         "proposed": 1 if proposed else 0},
    )
    written = cur.fetchone() is not None
    cur.close()
    return written


def replace_video_tags(conn, video_id: str, tag_group: str, tags: list, source: str,
                       created_at: str = None,
                       protected_sources=PROTECTED_TAG_SOURCES) -> dict:
    """Make `tags` the full tag set for (video_id, tag_group): upsert each,
    delete whatever else was in this group -- except rows protected against
    `source` (same rule as upsert_video_tag), which are left untouched even
    though they're not in the new list."""
    is_protected_writer = source in protected_sources
    tags = list(dict.fromkeys(t for t in tags if t))

    cur = conn._conn.cursor()
    sql = "DELETE FROM video_tags WHERE video_id=%(video_id)s AND tag_group=%(tag_group)s"
    params = {"video_id": video_id, "tag_group": tag_group}
    if not is_protected_writer:
        sql += " AND source NOT IN %(protected)s"
        params["protected"] = tuple(protected_sources)
    if tags:
        sql += " AND tag NOT IN %(keep)s"
        params["keep"] = tuple(tags)
    cur.execute(sql, params)
    cur.close()

    created_at = created_at or now_iso()
    written = blocked = 0
    for tag in tags:
        if upsert_video_tag(conn, video_id, tag_group, tag, source, created_at, protected_sources):
            written += 1
        else:
            blocked += 1
    return {"written": written, "blocked": blocked}

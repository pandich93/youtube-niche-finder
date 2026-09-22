"""DDL and migration bookkeeping for the niche-finder schema.

Schema v2 adds everything needed for *time-window* analytics (24h / 7d / 30d):
history snapshots of video and channel stats, a tracked-channel watchlist,
chart snapshots, and title/thumbnail change detection.

Schema v3 (iteration 8) adds the swipe file (saved_items), metadata-review
drafts with their post-publish outcome (drafts), and worker-generated
alerts (events) -- see docs/plan-iteration-8.md.
"""
from datetime import datetime, timezone

from infrastructure.postgres.connection import get_conn  # noqa: F401  (re-export for callers)

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    title TEXT,
    custom_url TEXT,
    country TEXT,
    description TEXT,
    default_language TEXT,
    subscriber_count BIGINT,
    video_count BIGINT,
    view_count BIGINT,
    thumbnail TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT,
    title TEXT,
    description TEXT,
    published_at TEXT,
    duration_seconds INTEGER,
    view_count BIGINT,
    like_count BIGINT,
    comment_count BIGINT,
    thumbnail TEXT,
    tags TEXT,
    default_language TEXT,
    embedding BYTEA,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS niches (
    slug TEXT PRIMARY KEY,
    query TEXT,
    label TEXT,
    created_at TEXT,
    last_collected_at TEXT
);

CREATE TABLE IF NOT EXISTS video_niches (
    video_id TEXT,
    niche_slug TEXT,
    PRIMARY KEY (video_id, niche_slug)
);

-- ---------- v2: history / tracking ----------

CREATE TABLE IF NOT EXISTS video_stats_history (
    video_id TEXT,
    captured_at TEXT,
    view_count BIGINT,
    like_count BIGINT,
    comment_count BIGINT,
    title TEXT,
    thumbnail TEXT,
    PRIMARY KEY (video_id, captured_at)
);

CREATE TABLE IF NOT EXISTS channel_stats_history (
    channel_id TEXT,
    captured_at TEXT,
    subscriber_count BIGINT,
    video_count BIGINT,
    view_count BIGINT,
    PRIMARY KEY (channel_id, captured_at)
);

CREATE TABLE IF NOT EXISTS tracked_channels (
    channel_id TEXT PRIMARY KEY,
    note TEXT,
    added_at TEXT,
    last_refreshed_at TEXT,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS chart_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    captured_at TEXT,
    region TEXT,
    category_id TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS chart_entries (
    snapshot_id BIGINT,
    video_id TEXT,
    rank INTEGER,
    view_count BIGINT,
    PRIMARY KEY (snapshot_id, video_id)
);

CREATE TABLE IF NOT EXISTS video_changes (
    video_id TEXT,
    changed_at TEXT,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    PRIMARY KEY (video_id, changed_at, field)
);

CREATE TABLE IF NOT EXISTS video_categories (
    category_id TEXT,
    region TEXT,
    title TEXT,
    assignable INTEGER,
    updated_at TEXT,
    PRIMARY KEY (category_id, region)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published_at);
CREATE INDEX IF NOT EXISTS idx_video_niches_slug ON video_niches(niche_slug);
CREATE INDEX IF NOT EXISTS idx_vsh_video ON video_stats_history(video_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_csh_channel ON channel_stats_history(channel_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_chart_snap ON chart_snapshots(captured_at, region, category_id);

-- ---------- v3: swipe file, metadata-review drafts, worker alerts ----------

CREATE TABLE IF NOT EXISTS saved_items (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT,              -- 'video' | 'channel'
    ref_id TEXT,             -- video_id or channel_id
    folder TEXT,             -- user folder, defaults to 'default'
    note TEXT,
    payload TEXT,            -- JSON snapshot of metrics at save time
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS drafts (
    id BIGSERIAL PRIMARY KEY,
    video_id TEXT,           -- filled in once the draft is published and linked
    title TEXT,
    description TEXT,
    tags TEXT,               -- JSON list
    niche TEXT,
    channel_id TEXT,
    is_short INTEGER,
    review TEXT,             -- JSON snapshot of the signal review at save time
    created_at TEXT,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT,               -- 'outlier' | 'acceleration' | 'title_change' | ...
    ref_id TEXT,              -- video_id or channel_id the event is about
    payload TEXT,             -- JSON details
    created_at TEXT,
    seen_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_saved_items_kind_ref ON saved_items(kind, ref_id);
CREATE INDEX IF NOT EXISTS idx_drafts_video ON drafts(video_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_seen ON events(seen_at);

-- ---------- v4: optional LLM enrichment (cache + daily budget) ----------

CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,    -- sha256(task + model + normalized system/user/schema)
    task TEXT,
    model TEXT,
    result TEXT,              -- JSON string
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT,                 -- UTC date, YYYY-MM-DD
    model TEXT,
    calls INTEGER,
    prompt_tokens BIGINT,
    completion_tokens BIGINT,
    cost_usd NUMERIC,
    PRIMARY KEY (day, model)
);

-- ---------- v5: curated tags (theme/trigger/format...) and their hit rate ----------

CREATE TABLE IF NOT EXISTS video_tags (
    video_id TEXT,
    tag_group TEXT,           -- 'theme' | 'trigger' | 'format' | ... caller-defined
    tag TEXT,
    source TEXT,               -- 'manual' | 'claude-mcp' | 'llm'
    created_at TEXT,
    PRIMARY KEY (video_id, tag_group, tag)
);

CREATE INDEX IF NOT EXISTS idx_video_tags_group_tag ON video_tags(tag_group, tag);

-- ---------- v7: LLM-derived per-video insights cache ----------
-- task discriminates independent analyses on the same video -- stage 04's
-- 'comment_insights' and stage 05's 'why_viral' must not overwrite each
-- other's cached row.

CREATE TABLE IF NOT EXISTS video_insights (
    video_id TEXT,
    task TEXT,
    result JSONB,
    model TEXT,
    created_at TEXT,
    PRIMARY KEY (video_id, task)
);

-- ---------- v8: alert delivery dedup (stage 07) ----------
-- alert_key = events.id as text -- a delivered row here means that exact
-- event was already sent to Telegram/webhook, independent of events.seen_at
-- (which tracks the dashboard's "read" state, a different concern).

CREATE TABLE IF NOT EXISTS alert_deliveries (
    alert_key TEXT PRIMARY KEY,
    channel TEXT,
    sent_at TEXT
);
"""

# columns added to pre-existing tables (name -> DDL type)
MIGRATIONS = {
    "videos": {
        "category_id": "TEXT",
        "region": "TEXT",
        "is_short": "INTEGER",
        "topic_categories": "TEXT",
        "first_seen_at": "TEXT",
        "live_content": "TEXT",
        "contains_synthetic_media": "INTEGER",
    },
    "channels": {
        "published_at": "TEXT",
        "topic_categories": "TEXT",
        "keywords": "TEXT",
        "uploads_playlist": "TEXT",
        "first_seen_at": "TEXT",
        "hidden_subs": "INTEGER",
        # v6: stage 03 background AI labeling (faceless/format/topic/...)
        "llm_labels": "JSONB",
        "llm_labeled_at": "TEXT",
        "llm_model": "TEXT",
    },
    # v6: LLM-proposed tags (source='llm', taxonomy miss) sit in the same
    # table as manual/claude-mcp tags but stay out of tag_stats/lift until a
    # human accepts them -- see application/tags.py resolve_proposed_tag().
    "video_tags": {
        "proposed": "INTEGER",
    },
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- pgvector (06)

# sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (fastembed_provider.py)
EMBEDDING_DIM = 384

_pgvector_available = None  # None = not checked yet this process; else bool, cached


def pgvector_available() -> bool:
    """Whether embedding_v/HNSW are usable -- set once by init_db(). False on
    a plain postgres:16-alpine image (no vector extension installed);
    every caller that reads/writes embedding_v must check this first and
    fall back to the BLOB + Python-cosine path when it's False."""
    return bool(_pgvector_available)


def _ensure_pgvector(conn) -> bool:
    """Best-effort, never raises: enable the extension, add embedding_v +
    its HNSW index if they're not there yet. Only succeeds on an image that
    actually ships pgvector (pgvector/pgvector:pg16); a plain postgres image
    fails at CREATE EXTENSION and this returns False without touching
    anything else -- the BLOB column and Python-cosine functions keep
    working exactly as before (see domain rollback notes in README)."""
    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception:
        conn.rollback()
        return False
    try:
        if "embedding_v" not in _existing_columns(conn, "videos"):
            conn.execute(f"ALTER TABLE videos ADD COLUMN embedding_v vector({EMBEDDING_DIM})")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_embedding_v ON videos "
            "USING hnsw (embedding_v vector_cosine_ops)")
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def _backfill_embedding_v(conn, batch_size: int = 1000) -> int:
    """One-time-per-gap migration: copy every BLOB embedding that doesn't
    have a vector counterpart yet, batched so a large corpus doesn't hold one
    huge transaction. Safe to call on every init_db() -- a no-op once caught
    up. Returns rows migrated."""
    from infrastructure.embeddings.fastembed_provider import to_pgvector_literal, from_blob
    migrated = 0
    while True:
        rows = conn.execute(
            "SELECT video_id, embedding FROM videos "
            "WHERE embedding IS NOT NULL AND embedding_v IS NULL LIMIT ?",
            (batch_size,)).fetchall()
        if not rows:
            break
        for r in rows:
            literal = to_pgvector_literal(from_blob(r["embedding"]))
            conn.execute("UPDATE videos SET embedding_v = ?::vector WHERE video_id = ?",
                        (literal, r["video_id"]))
        conn.commit()
        migrated += len(rows)
        if len(rows) < batch_size:
            break
    return migrated


def _existing_columns(conn, table):
    return {r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ?", (table,)
    ).fetchall()}


def migrate(conn):
    """Add any v2 columns missing from a v1 database. Safe to run every start."""
    added = []
    for table, cols in MIGRATIONS.items():
        have = _existing_columns(conn, table)
        if not have:
            continue
        for col, ddl in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                added.append(f"{table}.{col}")
    if "llm_labels" in _existing_columns(conn, "channels"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_channels_llm_faceless "
            "ON channels ((llm_labels->>'is_faceless'))")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    return added


def init_db():
    global _pgvector_available
    conn = get_conn()
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()
    _pgvector_available = _ensure_pgvector(conn)
    if _pgvector_available:
        _backfill_embedding_v(conn)
    conn.close()

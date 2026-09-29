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

SCHEMA_VERSION = 4

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

-- ---------- channels/videos the API stopped returning (plan 04) ----------
-- Written by refresh_channels/refresh_stats only after a successful API call;
-- a row is "gone" once confirmed_at is set (domain/alerts.py gone_transition),
-- and is deleted as soon as the item shows up again.

CREATE TABLE IF NOT EXISTS gone_items (
    kind TEXT,               -- 'channel' | 'video'
    ref_id TEXT,
    first_missing_at TEXT,
    last_missing_at TEXT,
    miss_count INTEGER,
    confirmed_at TEXT,
    PRIMARY KEY (kind, ref_id)
);

-- ---------- thumbnail versions (plan 05) ----------
-- The API's thumbnail URL never changes when a creator swaps the image, so
-- application/packaging.py fingerprints the image itself (dHash) for tracked
-- channels and keeps each distinct version: the first one seen as a baseline,
-- then one row per detected change (also logged to video_changes with
-- field='thumbnail_image'). Once a thumbnail is replaced, YouTube serves the
-- new one at the old URL -- this table is the only place the "before" lives.

CREATE TABLE IF NOT EXISTS thumbnail_archive (
    video_id TEXT,
    captured_at TEXT,
    dhash TEXT,
    image BYTEA,             -- mqdefault.jpg, 320x180, ~10-20 KB
    PRIMARY KEY (video_id, captured_at)
);

-- ---------- sponsor map (plan 09) ----------
-- Brands a creator names in a video description (domain/sponsors.py), found by
-- the worker step `sponsors`. Shared public-data tables, no user_id. A lower
-- bound: a sponsor spoken only in the video never shows up here.
-- sponsor_scan remembers which description (md5) and rules version each video
-- was scanned with, so a changed description or new rules trigger a rescan.

CREATE TABLE IF NOT EXISTS video_sponsors (
    video_id TEXT,
    brand TEXT,
    kind TEXT,               -- 'sponsor' | 'affiliate' | 'promo_code'
    evidence TEXT,           -- the description line the signal came from
    detected_at TEXT,
    PRIMARY KEY (video_id, brand, kind)
);

CREATE INDEX IF NOT EXISTS idx_video_sponsors_brand ON video_sponsors(brand);

CREATE TABLE IF NOT EXISTS sponsor_scan (
    video_id TEXT PRIMARY KEY,
    desc_hash TEXT,
    rules_version INTEGER,
    scanned_at TEXT
);

-- ---------- v9: manually-pasted transcripts + hybrid search (stage 19) ----------
-- Never fetched automatically -- the user copies a transcript off YouTube's
-- own UI and pastes it in; see PRIVACY.md.

CREATE TABLE IF NOT EXISTS transcript_requests (
    video_id TEXT PRIMARY KEY,
    reason TEXT,
    compare_group TEXT,
    requested_by TEXT,
    status TEXT,              -- 'pending' | 'ready' | 'error'
    error TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS transcripts (
    video_id TEXT PRIMARY KEY,
    language TEXT,
    text TEXT,
    has_timestamps INTEGER,
    word_count INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS transcript_chunks (
    video_id TEXT,
    idx INTEGER,
    start_sec INTEGER,
    text TEXT,
    embedding BYTEA,
    PRIMARY KEY (video_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_transcript_chunks_fts
    ON transcript_chunks USING GIN (to_tsvector('simple', text));

-- ---------- v10: niche clusters (stage 08) ----------
-- Wholesale-replaced on every compute_clusters() run (k-means labels
-- aren't stable identities across reruns) -- see application/niche_clusters.py.

CREATE TABLE IF NOT EXISTS niche_clusters (
    cluster_id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    audience TEXT,
    channel_count INTEGER,
    median_outlier_score REAL,
    total_velocity REAL,
    faceless_share REAL,
    competition_count INTEGER,
    channel_ids TEXT,          -- JSON array of channel_id
    model TEXT,
    created_at TEXT
);

-- ---------- plan 14: your own channels (PERSONAL, plan 15 rules) ----------
-- Every row belongs to one user (user_id, 1 = the local user until plan 15).
-- token_enc is the OAuth refresh token, Fernet-encrypted with OWN_TOKENS_KEY
-- from the environment; the plaintext is never stored, logged or returned.

CREATE TABLE IF NOT EXISTS own_channels (
    user_id BIGINT NOT NULL DEFAULT 1,
    channel_id TEXT NOT NULL,
    title TEXT,
    published_at TEXT,
    scopes TEXT,
    token_enc BYTEA,
    connected_at TEXT,
    last_synced_at TEXT,
    last_error TEXT,
    PRIMARY KEY (user_id, channel_id)
);

CREATE INDEX IF NOT EXISTS idx_own_channels_user ON own_channels(user_id);

-- One row per video and window ('28d' or 'lifetime') from the Analytics API.
CREATE TABLE IF NOT EXISTS own_video_metrics (
    user_id BIGINT NOT NULL DEFAULT 1,
    channel_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    window_name TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    views BIGINT,
    minutes_watched BIGINT,
    avg_view_duration REAL,
    avg_view_pct REAL,
    subscribers_gained BIGINT,
    revenue REAL,
    cpm REAL,
    playback_cpm REAL,
    fetched_at TEXT,
    PRIMARY KEY (user_id, video_id, window_name)
);

CREATE INDEX IF NOT EXISTS idx_own_video_metrics_user ON own_video_metrics(user_id, channel_id);

-- An OAuth consent in progress: the state is single-use and expires, the PKCE
-- verifier never leaves the server.
CREATE TABLE IF NOT EXISTS own_oauth_pending (
    state TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL DEFAULT 1,
    code_verifier TEXT NOT NULL,
    created_at TEXT NOT NULL,
    redirect_uri TEXT
);

CREATE INDEX IF NOT EXISTS idx_own_oauth_pending_user ON own_oauth_pending(user_id);

-- ---------- plan 15: users and sign-in (used when NF_MULTI_USER=1) ----------
-- id 1 is the local user every personal row belonged to before; it has no
-- password until `cli.py set-password local`. Passwords are scrypt hashes;
-- sessions keep only the SHA-256 of the cookie token.

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    created_at TEXT,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- plan 15 (5.9): each user's own alert delivery. Secrets (bot token, webhook
-- URL) are Fernet-encrypted with OWN_TOKENS_KEY. No row for user 1 = the
-- NOTIFY_* settings from .env, as before.
CREATE TABLE IF NOT EXISTS user_settings (
    user_id BIGINT PRIMARY KEY,
    telegram_token_enc BYTEA,
    telegram_chat_id TEXT,
    webhook_url_enc BYTEA,
    notify_mode TEXT,
    updated_at TEXT
);

-- plan 15 (5.7-5.8): personal API tokens for the browser extension and MCP
-- over HTTP ("Authorization: Bearer nf_..."); only the SHA-256 is stored and
-- the plaintext is shown once, when the token is created.
CREATE TABLE IF NOT EXISTS api_tokens (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL DEFAULT 1,
    token_hash TEXT NOT NULL UNIQUE,
    name TEXT,
    created_at TEXT,
    last_used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);

-- plan 15 (5.4): "read" is personal even though the event is shared -- one row
-- per user and event once that user has seen it (was events.seen_at).
CREATE TABLE IF NOT EXISTS event_reads (
    user_id BIGINT NOT NULL DEFAULT 1,
    event_id BIGINT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (user_id, event_id)
);
"""

# columns added to pre-existing tables (name -> DDL type)
# plan 15: every personal table gets its owner; ADD COLUMN with DEFAULT 1
# moves the rows that already exist to the local user.
_USER_ID = "BIGINT NOT NULL DEFAULT 1"

MIGRATIONS = {
    "tracked_channels": {"user_id": _USER_ID},
    "saved_items": {"user_id": _USER_ID},
    "alert_deliveries": {"user_id": _USER_ID},
    "transcript_requests": {"user_id": _USER_ID},
    "llm_usage": {"user_id": _USER_ID},
    # plan 15 (5.4): which channel an event is about, so a user sees the events
    # of their own watchlist (filled from payload.channelId for older rows)
    "events": {"channel_id": "TEXT"},
    # plan 14 fix: the exact redirect the consent used (the token exchange must repeat it)
    "own_oauth_pending": {"redirect_uri": "TEXT"},
    "videos": {
        "category_id": "TEXT",
        "region": "TEXT",
        "is_short": "INTEGER",
        "topic_categories": "TEXT",
        "first_seen_at": "TEXT",
        "live_content": "TEXT",
        "contains_synthetic_media": "INTEGER",
        # plan 13: CLIP vector of the thumbnail (float32 BYTEA, the fallback
        # when pgvector is missing) and when it was taken, so a thumbnail swap
        # logged after it (video_changes, field='thumbnail_image') re-embeds it
        "thumb_embedding": "BYTEA",
        "thumb_embedded_at": "TEXT",
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
    # plan 02: a draft born from an outlier brief points back at that outlier.
    "drafts": {
        "source_video_id": "TEXT",
        "user_id": _USER_ID,          # plan 15 (drafts is keyed once: a second key would win)
    },
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- pgvector (06)

# sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (fastembed_provider.py)
EMBEDDING_DIM = 384
# Qdrant/clip-ViT-B-32-vision (image_provider.py, plan 13)
THUMB_EMBEDDING_DIM = 512

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
        if "thumb_embedding_v" not in _existing_columns(conn, "videos"):
            conn.execute(
                f"ALTER TABLE videos ADD COLUMN thumb_embedding_v vector({THUMB_EMBEDDING_DIM})")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_thumb_embedding_v ON videos "
            "USING hnsw (thumb_embedding_v vector_cosine_ops)")
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
    from infrastructure.embeddings.fastembed_provider import from_blob, to_pgvector_literal
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
    # plan 15: an index per owner column, and the local user every
    # pre-multi-user row belongs to (no password: it cannot sign in until one
    # is set with `cli.py set-password local`)
    from domain.users import LOCAL_USER_ID, PERSONAL_TABLES
    for table in PERSONAL_TABLES:
        if "user_id" in _existing_columns(conn, table):
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_user ON {table}(user_id)")
    _owner_keys(conn)
    if "channel_id" in _existing_columns(conn, "events"):
        # parsed in Python, not payload::jsonb: one malformed old payload must
        # not stop the server from starting -- such a row just stays unassigned
        import json
        for r in conn.execute("SELECT id, payload FROM events WHERE channel_id IS NULL "
                              "AND payload IS NOT NULL").fetchall():
            try:
                channel = (json.loads(r["payload"]) or {}).get("channelId")
            except (TypeError, ValueError, AttributeError):
                channel = None
            if channel:
                conn.execute("UPDATE events SET channel_id = ? WHERE id = ?", (channel, r["id"]))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_channel ON events(channel_id)")
    if _existing_columns(conn, "event_reads") and "seen_at" in _existing_columns(conn, "events"):
        # the local user's read marks from before event_reads existed
        conn.execute("INSERT INTO event_reads (user_id, event_id, seen_at) "
                     "SELECT ?, id, seen_at FROM events WHERE seen_at IS NOT NULL "
                     "ON CONFLICT DO NOTHING", (LOCAL_USER_ID,))
    if _existing_columns(conn, "users"):
        conn.execute("INSERT INTO users (id, email, is_admin, created_at) VALUES (?, 'local', 1, ?) "
                     "ON CONFLICT (id) DO NOTHING", (LOCAL_USER_ID, now_iso()))
        conn.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), "
                     "GREATEST((SELECT MAX(id) FROM users), 1))")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    return added


# plan 15 (5.4): a row is unique per owner, so two users can track the same
# channel or ask for the same transcript. Swapped once, on the first start.
_OWNER_KEYS = {"tracked_channels": ("user_id", "channel_id"),
               "transcript_requests": ("user_id", "video_id"),
               "alert_deliveries": ("user_id", "alert_key"),   # plan 15 (5.9)
               "llm_usage": ("user_id", "day", "model")}        # plan 15 (5.10)


def _primary_key(conn, table):
    return [r["column_name"] for r in conn.execute(
        "SELECT kcu.column_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name "
        "AND kcu.table_schema = tc.table_schema AND kcu.table_name = tc.table_name "
        "WHERE tc.table_schema = current_schema() AND tc.table_name = ? "
        "AND tc.constraint_type = 'PRIMARY KEY' ORDER BY kcu.ordinal_position", (table,)).fetchall()]


def _owner_keys(conn):
    for table, key in _OWNER_KEYS.items():
        cols = _existing_columns(conn, table)
        if "user_id" not in cols or _primary_key(conn, table) == list(key):
            continue
        name = conn.execute(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_schema = current_schema() AND table_name = ? AND constraint_type = 'PRIMARY KEY'",
            (table,)).fetchone()
        if name:
            conn.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name["constraint_name"]}"')
        conn.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({', '.join(key)})")


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

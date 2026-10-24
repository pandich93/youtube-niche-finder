"""DDL and migration bookkeeping for the niche-finder schema.

Schema v2 adds everything needed for *time-window* analytics (24h / 7d / 30d):
history snapshots of video and channel stats, a tracked-channel watchlist,
chart snapshots, and title/thumbnail change detection.

Schema v3 (iteration 8) adds the swipe file (saved_items), metadata-review
drafts with their post-publish outcome (drafts), and worker-generated
alerts (events) -- see docs/plan-iteration-8.md.
"""
import logging
from datetime import datetime, timezone

from infrastructure.postgres.connection import get_conn  # noqa: F401  (re-export for callers)

_log = logging.getLogger(__name__)

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

-- plan 24: your own channel by day and format (YouTube Analytics,
-- creatorContentType) -- Shorts against long videos, watch hours toward YPP
CREATE TABLE IF NOT EXISTS own_channel_daily (
    user_id BIGINT NOT NULL DEFAULT 1,
    channel_id TEXT NOT NULL,
    day TEXT NOT NULL,
    content_type TEXT NOT NULL,
    views BIGINT,
    minutes_watched BIGINT,
    subscribers_gained BIGINT,
    PRIMARY KEY (user_id, channel_id, day, content_type)
);

-- plan 19: topics a user watches; a new video close to one raises a personal
-- topic_match event (events.user_id)
CREATE TABLE IF NOT EXISTS user_topics (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL DEFAULT 1,
    text TEXT NOT NULL,
    embedding BYTEA,
    threshold REAL NOT NULL DEFAULT 0.6,
    created_at TEXT,
    paused INTEGER NOT NULL DEFAULT 0
);

-- plan 30: what a video costs to make (voice, editing, AI generation, stock),
-- personal: one user's named profiles. per_video + per_minute * length + the
-- monthly overhead are in US dollars.
CREATE TABLE IF NOT EXISTS cost_profiles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    per_video_usd REAL NOT NULL DEFAULT 0,
    per_minute_usd REAL NOT NULL DEFAULT 0,
    monthly_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT,
    UNIQUE (user_id, name)
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

-- multi-user plan P1-09: LLM money held for a call in flight, so parallel
-- calls cannot all pass the budget check before any of them is recorded.
-- A row goes when its call settles; one left by a crashed process stops
-- counting after llm_gateway.RESERVATION_TTL_MINUTES.
CREATE TABLE IF NOT EXISTS llm_reservations (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    usd NUMERIC NOT NULL,
    created_at TEXT NOT NULL
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

-- multi-user plan P1-06: one-time links sent by email -- sign-up (user_id is
-- NULL until the account exists; new_email is the address), password reset,
-- and confirming a new email. Only the SHA-256 of the token is kept; a link
-- works once and until expires_at.
CREATE TABLE IF NOT EXISTS email_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER,
    purpose TEXT NOT NULL,
    new_email TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- multi-user plan P1-07: the action log (application/audit.py), grown from
-- P0-04's admin_actions, whose rows migrate() moves here once. Sign-ins and
-- failed ones, password and email changes, sign-ups, API tokens, tariffs, and
-- what admins do to accounts and tariffs. No user_id column on purpose: the log
-- outlives a deleted account (a security record, kept NF_AUDIT_RETENTION_DAYS),
-- so it is not in PERSONAL_TABLES. details never holds a password or a token.
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    created_at TEXT NOT NULL,
    event TEXT NOT NULL,
    actor_id BIGINT,               -- 0 = the operator (CLI, worker); NULL = nobody signed in
    target_id BIGINT,              -- the account acted on
    target_email TEXT,             -- written down where the account may go away
    ip TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_event ON audit_log(event, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_target ON audit_log(target_id);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    created_at TEXT,
    expires_at TEXT NOT NULL,      -- idle expiry, moved forward by activity
    -- multi-user plan P1-04: what the profile's session list shows -- an id
    -- to end one by (never the hash), the last activity, the client address
    -- (as api.py _client_ip sees it) and the trimmed User-Agent
    id BIGSERIAL,
    last_seen_at TEXT,
    ip TEXT,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- multi-user plan P0-03: failed sign-ins per email and per client address,
-- so guessing slows down across restarts and processes (domain/login_throttle.py)
CREATE TABLE IF NOT EXISTS login_attempts (
    kind TEXT NOT NULL,            -- 'email' | 'ip'
    key TEXT NOT NULL,             -- normalised email or client address
    failures INTEGER NOT NULL DEFAULT 0,
    first_failure_at TEXT,
    last_failure_at TEXT,
    PRIMARY KEY (kind, key)
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_last ON login_attempts(last_failure_at);

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

-- i18n plan: the interface language a person chose (ru | en) -- for the
-- dashboard on another device, and for their alerts and digest. Its own table:
-- a row in user_settings for user 1 would switch off the NOTIFY_* fallback.
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id BIGINT PRIMARY KEY,
    locale TEXT,
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
    last_used_at TEXT,
    -- multi-user plan P1-03: NULL = no expiry (tokens made before it);
    -- scopes, comma-separated: read (GET), write (changes), mcp (MCP over HTTP)
    expires_at TEXT,
    scopes TEXT NOT NULL DEFAULT 'read,write,mcp'
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

-- ---------- SaaS sub-project 1: tariffs (used when NF_MULTI_USER=1) ----------
-- plans is shared: what a tariff allows. limits/features hold only the values
-- an admin set; a missing key means the catalog default (domain/plans.py).
CREATE TABLE IF NOT EXISTS plans (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    price NUMERIC(10,2) NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    period_days INTEGER,
    is_base INTEGER NOT NULL DEFAULT 0,
    is_public INTEGER NOT NULL DEFAULT 1,
    archived_at TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    limits JSONB NOT NULL DEFAULT '{}'::jsonb,
    features JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TEXT,
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_plans_one_base ON plans(is_base) WHERE is_base = 1;

-- personal: a user's tariff; no row = the base plan
CREATE TABLE IF NOT EXISTS user_plans (
    user_id BIGINT PRIMARY KEY,
    plan_id BIGINT NOT NULL REFERENCES plans(id),
    started_at TEXT NOT NULL,
    expires_at TEXT,
    assigned_by TEXT NOT NULL
);

-- personal: every change of a user's tariff (self, admin:<id>, system)
CREATE TABLE IF NOT EXISTS plan_changes (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL DEFAULT 1,
    from_plan_id BIGINT,
    to_plan_id BIGINT NOT NULL,
    expires_at TEXT,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
"""

# columns added to pre-existing tables (name -> DDL type)
# plan 15: every personal table gets its owner; ADD COLUMN with DEFAULT 1
# moves the rows that already exist to the local user.
_USER_ID = "BIGINT NOT NULL DEFAULT 1"

MIGRATIONS = {
    # multi-user plan P0-04: NULL = active; set = blocked (no sign-in, no sessions, no tokens)
    "users": {"disabled_at": "TEXT"},
    # multi-user plan P1-03: older tokens keep working as before -- no expiry,
    # every scope -- until revoked
    "api_tokens": {"expires_at": "TEXT", "scopes": "TEXT NOT NULL DEFAULT 'read,write,mcp'"},
    # multi-user plan P1-04: older sessions get an id (BIGSERIAL numbers the
    # existing rows); their activity, address and browser fill in on next use
    "sessions": {"id": "BIGSERIAL", "last_seen_at": "TEXT", "ip": "TEXT", "user_agent": "TEXT"},
    "tracked_channels": {"user_id": _USER_ID},
    "saved_items": {"user_id": _USER_ID},
    "alert_deliveries": {"user_id": _USER_ID},
    "transcript_requests": {"user_id": _USER_ID},
    "llm_usage": {"user_id": _USER_ID},
    # plan 15 (5.4): which channel an event is about, so a user sees the events
    # of their own watchlist (filled from payload.channelId for older rows)
    # plan 19 follow-up: a topic may also search all of YouTube once a day
    "user_topics": {"search_youtube": "INTEGER NOT NULL DEFAULT 0", "last_searched_at": "TEXT"},
    "events": {"channel_id": "TEXT",
               # plan 19: set for a personal event (a topic match) -- only this
               # user sees it; NULL keeps the watchlist rule above
               "user_id": "BIGINT"},
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
        # plan 16 (review): when titles/descriptions were last re-read --
        # updated_at moves with every counters-only refresh
        "texts_refreshed_at": "TEXT",
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
        # multi-user plan P0-02: who added the row; only they or an admin may
        # replace or remove it. NULL = the operator (worker, CLI, older rows).
        "created_by": "BIGINT",
    },
    # multi-user plan P0-02: the shared transcript's first author; only they or
    # an admin may replace or re-index it. NULL = the operator / older rows.
    "transcripts": {"created_by": "BIGINT"},
    # plan 02: a draft born from an outlier brief points back at that outlier.
    "drafts": {
        "source_video_id": "TEXT",
        "user_id": _USER_ID,          # plan 15 (drafts is keyed once: a second key would win)
        # plan 33: when the draft is planned to come out (UTC ISO), for the calendar
        "planned_at": "TEXT",
    },
}

# multi-user plan P2-05: просмотр под пользователем для поддержки
# (application/impersonation.py). Сессия админа на время смотрит «глазами»
# impersonating_user_id -- только чтение и не дольше impersonation_expires_at;
# NULL -- обычная сессия. Строка сессии по-прежнему принадлежит админу (user_id).
MIGRATIONS["sessions"] = {**MIGRATIONS["sessions"],
                          "impersonating_user_id": "BIGINT",
                          "impersonation_started_at": "TEXT",
                          "impersonation_expires_at": "TEXT",
                          "impersonation_reason": "TEXT"}


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


def filtered_ann(conn) -> None:
    """Before an HNSW query with a WHERE filter (other channel, niche, a
    similarity floor): HNSW hands back ~hnsw.ef_search (40) nearest rows and
    the filter runs after, so when those all fail it -- e.g. a channel's own
    40 near-identical uploads -- the query returns nothing. pgvector >= 0.8
    keeps walking the graph until LIMIT rows pass (iterative scan;
    strict_order keeps exact distance order). SET LOCAL lasts until the
    transaction ends. Older pgvector rejects the setting: the savepoint keeps
    that from aborting the caller's transaction, and the query then behaves
    as before."""
    try:
        conn.execute("SAVEPOINT nf_filtered_ann")
        try:
            conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            conn.execute("RELEASE SAVEPOINT nf_filtered_ann")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT nf_filtered_ann")
    except Exception:
        pass


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
    if "user_id" in _existing_columns(conn, "events"):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id)")
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
    _audit_from_admin_actions(conn)
    if _existing_columns(conn, "plans"):
        # SaaS sub-project 1: the base tariff everyone starts on. Empty limits
        # and features: the catalog defaults (NF_USER_DAILY_* and, since P1-10,
        # NF_USER_MAX_TRACKED/OWN_CHANNELS from .env, every feature on).
        conn.execute(
            "INSERT INTO plans (slug, name, description, is_base, is_public, created_at, updated_at) "
            "SELECT 'free', 'Free', 'Базовый тариф', 1, 1, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM plans)", (now_iso(), now_iso()))
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    return added


def _audit_from_admin_actions(conn):
    """Multi-user plan P1-07: P0-04's admin_actions becomes part of audit_log,
    once -- copied and dropped in init_db's transaction, so a failed start
    leaves both as they were. A table, not a view kept for old readers: the
    only writer and reader was application/users_admin.py."""
    if not _existing_columns(conn, "admin_actions") or not _existing_columns(conn, "audit_log"):
        return
    from domain.audit import LEGACY_ACTIONS
    for action, event in LEGACY_ACTIONS.items():
        conn.execute("INSERT INTO audit_log (created_at, event, actor_id, target_id, target_email) "
                     "SELECT created_at, ?, admin_id, target_user_id, target_email "
                     "FROM admin_actions WHERE action = ? ORDER BY id", (event, action))
    conn.execute("DROP TABLE admin_actions")


# multi-user plan P0-04: every table holding one person's rows, keyed by
# user_id -- deleting an account empties exactly these (application/users_admin.py).
# events: only rows with user_id = the person (NULL = a shared fact, kept).
# tests/test_user_deletion.py fails when a table with user_id is missing here.
PERSONAL_TABLES = (
    "sessions", "api_tokens", "email_tokens", "user_settings", "user_prefs", "user_plans", "plan_changes",
    "tracked_channels", "saved_items", "drafts", "alert_deliveries", "event_reads", "events",
    "transcript_requests", "llm_usage", "llm_reservations", "user_topics", "cost_profiles",
    "own_channels", "own_channel_daily", "own_video_metrics", "own_oauth_pending",
)


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


# multi-user plan P1-02: политика на каждую личную таблицу. app.user_id ставит
# get_conn() в области пользователя; не задан -- NULL, и строк не видно вовсе.
# events: NULL в user_id -- общий факт, его видят все (как и без RLS).
RLS_POLICY = "nf_owner_rows"
_RLS_UID = "NULLIF(current_setting('app.user_id', true), '')::bigint"
_RLS_EXPR = {"events": f"(user_id IS NULL OR user_id = {_RLS_UID})"}


def _rls_expr(table):
    return _RLS_EXPR.get(table, f"user_id = {_RLS_UID}")


def _savepoint(conn, name, fn):
    """Шаг, которому можно не получиться (нет прав на CREATE ROLE, гонка двух
    процессов): ошибка откатывает только его, остальное идёт дальше."""
    conn.execute(f"SAVEPOINT {name}")
    try:
        fn()
        conn.execute(f"RELEASE SAVEPOINT {name}")
        return True
    except Exception as e:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        _log.warning("Row-Level Security setup: %s failed: %s", name, str(e).strip().splitlines()[0])
        return False


def _ensure_app_role(conn, role):
    """Роль для запросов пользователей: без входа, без BYPASSRLS, не владелец.
    Логин владельца получает её членство, чтобы get_conn() делал SET ROLE."""
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = ?", (role,)).fetchone():
        _savepoint(conn, "nf_rls_role", lambda: conn.execute(
            f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT'))
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = ?", (role,)).fetchone():
        return False
    member = conn.execute("SELECT pg_has_role(current_user, ?, 'MEMBER')", (role,)).fetchone()[0]
    if not member:
        member = _savepoint(conn, "nf_rls_member",
                            lambda: conn.execute(f'GRANT "{role}" TO CURRENT_USER'))
    return member


def _grant_app_role(conn, role):
    """Права роли на таблицы и счётчики этой схемы -- только недостающие:
    повторный GRANT на каждом старте зря трогал бы каталог."""
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    if not conn.execute("SELECT has_schema_privilege(?, ?, 'USAGE')", (role, schema)).fetchone()[0]:
        conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
    for r in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') AND NOT ("
            "has_table_privilege(?, c.oid, 'SELECT') AND has_table_privilege(?, c.oid, 'INSERT') "
            "AND has_table_privilege(?, c.oid, 'UPDATE') "
            "AND has_table_privilege(?, c.oid, 'DELETE'))", (role,) * 4).fetchall():
        conn.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{r["relname"]}" TO "{role}"')
    for r in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind = 'S' AND NOT ("
            "has_sequence_privilege(?, c.oid, 'USAGE') "
            "AND has_sequence_privilege(?, c.oid, 'SELECT') "
            "AND has_sequence_privilege(?, c.oid, 'UPDATE'))", (role,) * 3).fetchall():
        conn.execute(f'GRANT USAGE, SELECT, UPDATE ON SEQUENCE "{r["relname"]}" TO "{role}"')


def _ensure_policies(conn):
    """ENABLE ROW LEVEL SECURITY и политика -- только там, где их ещё нет:
    оба берут ACCESS EXCLUSIVE, и на каждом старте ждали бы worker."""
    for table in PERSONAL_TABLES:
        if "user_id" not in _existing_columns(conn, table):
            continue
        row = conn.execute(
            "SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relname = ?", (table,)).fetchone()
        if not row["relrowsecurity"]:
            conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        if not conn.execute("SELECT 1 FROM pg_policies WHERE schemaname = current_schema() "
                            "AND tablename = ? AND policyname = ?", (table, RLS_POLICY)).fetchone():
            expr = _rls_expr(table)
            conn.execute(f"CREATE POLICY {RLS_POLICY} ON {table} USING ({expr}) "
                         f"WITH CHECK ({expr})")


def _ensure_rls(conn):
    """multi-user plan P1-02, на каждом init_db(): политики, роль, её права.
    Не роняет старт: без прав на CREATE ROLE политики всё равно ставятся, а
    запросы идут ролью владельца, как раньше (предупреждение в логе). Под
    advisory-блокировкой схемы: web, worker и MCP стартуют одновременно."""
    from infrastructure.postgres.connection import app_role, lock_until_commit, rls_enabled
    try:
        lock_until_commit(conn, "rls_setup", 0)
        # первое включение на живой базе ждёт ACCESS EXCLUSIVE; не дождались --
        # старт идёт дальше, а политики поставит следующий init_db()
        conn.execute("SET LOCAL lock_timeout = '10s'")
        _ensure_policies(conn)
        if rls_enabled():
            role = app_role()
            if _ensure_app_role(conn, role):
                _grant_app_role(conn, role)
        conn.commit()
    except Exception as e:
        conn.rollback()
        _log.warning("Row-Level Security setup failed, user requests run as the table owner: %s",
                     str(e).strip().splitlines()[0])


def init_db():
    global _pgvector_available
    from infrastructure.postgres.connection import recheck_app_role
    conn = get_conn()
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()
    _pgvector_available = _ensure_pgvector(conn)
    if _pgvector_available:
        _backfill_embedding_v(conn)
    _ensure_rls(conn)
    recheck_app_role(conn)
    conn.close()

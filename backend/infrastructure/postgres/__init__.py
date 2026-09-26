"""PostgreSQL storage layer for the niche-finder project -- facade module.

This package is split into connection.py (pool/shim), schema.py (DDL and
migrations) and repositories.py (upsert/record/tracking functions), but is
re-exported here as a single flat namespace so existing call sites can do
`import infrastructure.postgres as db` and use it exactly like the old
top-level `db.py` (`db.get_conn()`, `db.upsert_video(...)`, `db.SCHEMA`, ...).
"""
from infrastructure.postgres.connection import (  # noqa: F401
    Row,
    _CursorResult,
    _dsn,
    _PGConn,
    _pool,
    _schema,
    display_dsn,
    get_conn,
)
from infrastructure.postgres.repositories import (  # noqa: F401
    _CHANNEL_COLS,
    _VIDEO_COLS,
    PROTECTED_TAG_SOURCES,
    _upsert,
    add_chart_entry,
    already_delivered_alert_keys,
    get_meta,
    incr_meta,
    link_video_niche,
    mark_alert_delivered,
    new_chart_snapshot,
    record_channel_llm_labels,
    record_channel_stats,
    record_video_stats,
    replace_video_tags,
    save_video_insights,
    set_meta,
    sync_embedding_v,
    track_channel,
    untrack_channel,
    upsert_category,
    upsert_channel,
    upsert_niche,
    upsert_video,
    upsert_video_tag,
)
from infrastructure.postgres.schema import (  # noqa: F401
    EMBEDDING_DIM,
    MIGRATIONS,
    SCHEMA,
    SCHEMA_VERSION,
    _existing_columns,
    init_db,
    migrate,
    now_iso,
    pgvector_available,
)

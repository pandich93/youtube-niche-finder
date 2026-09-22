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
    _PGConn,
    _dsn,
    _schema,
    _pool,
    display_dsn,
    get_conn,
)
from infrastructure.postgres.schema import (  # noqa: F401
    SCHEMA_VERSION,
    SCHEMA,
    MIGRATIONS,
    now_iso,
    _existing_columns,
    migrate,
    init_db,
)
from infrastructure.postgres.repositories import (  # noqa: F401
    _CHANNEL_COLS,
    _VIDEO_COLS,
    _upsert,
    upsert_channel,
    upsert_video,
    record_video_stats,
    record_channel_stats,
    link_video_niche,
    upsert_niche,
    track_channel,
    untrack_channel,
    new_chart_snapshot,
    add_chart_entry,
    upsert_category,
    get_meta,
    set_meta,
    PROTECTED_TAG_SOURCES,
    upsert_video_tag,
    replace_video_tags,
)

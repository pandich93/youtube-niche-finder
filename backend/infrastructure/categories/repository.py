"""YouTube video categories: id -> title, cached in Postgres, with an offline
fallback (domain.categories_catalog) so the tool works before the first API
call is ever made.

This module re-exports the pure lookup tables/helpers from
domain.categories_catalog so existing call sites that do
`import categories as C` and then use `C.FALLBACK`, `C.is_assignable(...)`,
`C.rpm_niche(...)` keep working unchanged after switching to
`from infrastructure.categories import repository as C`.
"""
import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from domain.categories_catalog import (  # noqa: F401  (re-exported for callers)
    CATEGORY_RPM_NICHE,
    CHARTED_CATEGORY_IDS,
    FALLBACK,
    fallback_title,
    is_assignable,
    rpm_niche,
)


def title_for(category_id, region: str = "US") -> str:
    if category_id is None:
        return "Unknown"
    cid = str(category_id)
    try:
        conn = db.get_conn()
        row = conn.execute(
            "SELECT title FROM video_categories WHERE category_id=? AND region IN (?, 'US') "
            "ORDER BY CASE region WHEN ? THEN 0 ELSE 1 END LIMIT 1",
            (cid, region, region),
        ).fetchone()
        conn.close()
        if row and row["title"]:
            return row["title"]
    except Exception:
        pass
    return fallback_title(cid)


def refresh_categories(api_key: str, regions=("US",), hl: str = "en_US") -> dict:
    """Pull the live id->title mapping per region (1 quota unit per region)."""
    conn = db.get_conn()
    stored, quota = 0, 0
    for region in regions:
        for cat in yt.video_categories(api_key, region_code=region, hl=hl):
            db.upsert_category(conn, cat["id"], region, cat["title"], cat["assignable"])
            stored += 1
        quota += 1
    conn.commit()
    conn.close()
    return {"regions": list(regions), "categories_stored": stored, "quota_used": quota}


def seed_fallback():
    """Write the built-in mapping into the DB so offline runs have titles."""
    conn = db.get_conn()
    for cid, (title, assignable) in FALLBACK.items():
        db.upsert_category(conn, cid, "US", title, assignable)
    conn.commit()
    conn.close()

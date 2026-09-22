"""Stage 18: export every collected video in a niche as TSV/CSV -- for
spreadsheet analysis outside the dashboard. Zero YouTube quota, reads only
what's already in Postgres.
"""
import csv
import io
from datetime import datetime, timezone

import infrastructure.postgres as db
from application import discovery as trends

COLUMNS = ["channel", "handle", "subs", "video_id", "published_at", "views", "likes",
          "comments", "length_seconds", "is_short", "title", "outlierScoreRolling",
          "outlierScorePeriod", "tags"]


def export_rows(slug: str) -> list:
    """One dict per video, keys matching COLUMNS. outlierScoreRolling/
    outlierScorePeriod are whatever discovery._enrich already computed
    (None if stage 14 never ran against this data); tags is empty if
    stage 16 was never used on these videos -- the column is always
    present either way, just possibly all-None/empty."""
    rows = trends.load_window(period="all", niche=slug)
    video_ids = [r["video_id"] for r in rows]
    tags_by_video = {}
    if video_ids:
        conn = db.get_conn()
        for i in range(0, len(video_ids), 400):
            chunk = video_ids[i:i + 400]
            sql = ("SELECT video_id, tag FROM video_tags WHERE video_id IN (%s) "
                  "AND (proposed IS NULL OR proposed = 0)" % ",".join("?" * len(chunk)))
            for t in conn.execute(sql, chunk).fetchall():
                tags_by_video.setdefault(t["video_id"], []).append(t["tag"])
        conn.close()

    out = []
    for r in rows:
        out.append({
            "channel": r.get("channel_title"), "handle": r.get("channel_url"),
            "subs": r.get("subs"), "video_id": r["video_id"],
            "published_at": r.get("published_at"), "views": r.get("view_count"),
            "likes": r.get("like_count"), "comments": r.get("comment_count"),
            "length_seconds": r.get("duration_seconds"),
            "is_short": bool(r.get("isShort")), "title": r.get("title"),
            "outlierScoreRolling": r.get("outlierScoreRolling"),
            "outlierScorePeriod": r.get("outlierScorePeriod"),
            "tags": ";".join(sorted(tags_by_video.get(r["video_id"], []))),
        })
    return out


def export_niche(slug: str, fmt: str = "tsv") -> dict:
    """Returns {"content": bytes, "filename": str, "rowCount": int}. csv's
    module handles escaping tabs/newlines/quotes inside field values
    correctly regardless of delimiter (QUOTE_MINIMAL quotes a field that
    contains the delimiter, a quote char, or a newline) -- no manual
    escaping needed. CSV gets a UTF-8 BOM (utf-8-sig) so Excel autodetects
    the encoding instead of mangling non-ASCII titles; TSV doesn't need
    one (typically opened by tools that already assume UTF-8)."""
    if fmt not in ("tsv", "csv"):
        raise ValueError("fmt must be 'tsv' or 'csv'")
    rows = export_rows(slug)

    buf = io.StringIO()
    delimiter = "\t" if fmt == "tsv" else ","
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, delimiter=delimiter,
                            quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    text = buf.getvalue()
    content = text.encode("utf-8-sig") if fmt == "csv" else text.encode("utf-8")

    date_str = datetime.now(timezone.utc).date().isoformat()
    filename = f"{slug}_videos_{date_str}.{fmt}"
    return {"content": content, "filename": filename, "rowCount": len(rows)}

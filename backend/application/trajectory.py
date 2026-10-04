"""Video trajectories (plan 20): views by age from the worker's snapshots,
for one to MAX_VIDEOS videos, each next to the curve its channel would make
-- the channel's median-baseline views times the maturity curve -- with
marks for title/thumbnail swaps and the 2026-08-24 view-count change.

Zero quota: only what the worker already recorded. A video found days after
publishing has no start of its curve; observedFromHours says from when.
"""
from datetime import datetime, timezone

import infrastructure.postgres as db
from application import discovery as trends
from domain import metrics as M

MAX_VIDEOS = 5
EXPECTED_AGES_DAYS = (0.25, 0.5, 1, 2, 3, 5, 7, 10, 14, 21, 30)
NOTE = ("Points are the worker's snapshots (every 3 h for a week, then daily up to 30 days); "
        "the expected curve is an estimate of niche-finder: the channel's median views times "
        "the maturity curve.")


def _ts(iso):
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _hours(a, b) -> float:
    return round((a - b).total_seconds() / 3600, 1)


def video_trajectory(video_ids) -> dict:
    ids = list(dict.fromkeys(i.strip() for i in (video_ids or []) if i and i.strip()))
    if not ids:
        raise ValueError("video_ids is required")
    if len(ids) > MAX_VIDEOS:
        raise ValueError(f"at most {MAX_VIDEOS} videos at once")
    marks_sql = ",".join("?" * len(ids))
    conn = db.get_conn()
    try:
        vids = {r["video_id"]: dict(r) for r in conn.execute(
            "SELECT v.video_id, v.title, v.channel_id, v.published_at, v.view_count, "
            "c.title AS channel_title FROM videos v LEFT JOIN channels c "
            f"ON c.channel_id = v.channel_id WHERE v.video_id IN ({marks_sql})", ids).fetchall()}
        history = {}
        for r in conn.execute(
                "SELECT video_id, captured_at, view_count FROM video_stats_history "
                f"WHERE video_id IN ({marks_sql}) ORDER BY captured_at", ids).fetchall():
            history.setdefault(r["video_id"], []).append(r)
        changes = {}
        for r in conn.execute(
                "SELECT video_id, changed_at, field FROM video_changes "
                f"WHERE video_id IN ({marks_sql}) ORDER BY changed_at", ids).fetchall():
            changes.setdefault(r["video_id"], []).append(r)
    finally:
        conn.close()

    channel_ids = sorted({v["channel_id"] for v in vids.values() if v["channel_id"]})
    rows = trends.load_window(period="all", channel_ids=channel_ids) if channel_ids else []
    baseline = {r["video_id"]: r.get("baselineMedianViews") for r in rows}

    out = []
    for vid in ids:
        v = vids.get(vid)
        if not v:
            continue
        pub = _ts(v["published_at"]) if v["published_at"] else None
        points = []
        if pub:
            points = [{"ageHours": _hours(_ts(h["captured_at"]), pub), "views": h["view_count"],
                       "t": h["captured_at"]} for h in history.get(vid, [])]
        base = baseline.get(vid)
        max_age_days = max([p["ageHours"] / 24 for p in points] + [1])
        expected = []
        if base:
            for d in EXPECTED_AGES_DAYS:
                expected.append({"ageHours": round(d * 24, 1),
                                 "views": int(round(base * M.maturity(d)))})
                if d >= max_age_days:
                    break
        marks = []
        if pub:
            marks = [{"ageHours": _hours(_ts(c["changed_at"]), pub), "kind": c["field"],
                      "t": c["changed_at"]} for c in changes.get(vid, [])]
            if points and pub < M.VIEW_COUNT_CHANGE_AT <= _ts(points[-1]["t"]):
                marks.append({"ageHours": _hours(M.VIEW_COUNT_CHANGE_AT, pub),
                              "kind": "view_count_change",
                              "t": M.VIEW_COUNT_CHANGE_AT.isoformat()})
        out.append({"videoId": vid, "title": v["title"], "channelId": v["channel_id"],
                    "channelTitle": v["channel_title"], "publishedAt": v["published_at"],
                    "views": v["view_count"], "baselineMedianViews": base,
                    "observedFromHours": points[0]["ageHours"] if points else None,
                    "points": points, "expected": expected, "marks": marks})
    return {"videos": out, "missing": [i for i in ids if i not in vids], "note": NOTE}

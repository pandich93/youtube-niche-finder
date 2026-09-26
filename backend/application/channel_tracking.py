"""Channel tracking and deep analysis -- the part that replaces Social Blade,
ViewStats and the channel half of vidIQ.

The core idea: the YouTube API is a *snapshot* API. It tells you a channel has
412,000 subscribers, never that it gained 8,000 this week. Every growth,
velocity and acceleration number below exists only because we store our own
history (video_stats_history / channel_stats_history) on a schedule. Keep the
worker running and these fill in; without it they stay null and the tool
degrades to what a one-off API call can say.

Second caveat worth repeating in output: channels.list rounds subscriberCount
to 3 significant figures, so week-over-week subscriber deltas are meaningless
for large channels and only usable below ~100k subs. View counts are exact.
"""
import json
import math
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

import infrastructure.postgres as db
from application import collecting
from application import maturity_curve as MC
from domain import keywords as K
from domain import metrics as M
from domain import periods as P
from infrastructure.categories import repository as C


def _dt(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------- watchlist

def list_tracked(faceless: bool = None, content_format: str = None, topic: str = None) -> list:
    """faceless/content_format/topic (stage 03) filter on channels.llm_labels
    -- a channel never AI-labeled simply doesn't match any of the three."""
    conn = db.get_conn()
    where = ["t.active = 1"]
    params = []
    if faceless is not None:
        where.append("(c.llm_labels->>'is_faceless')::boolean = ?")
        params.append(faceless)
    if content_format:
        where.append("c.llm_labels->>'content_format' = ?")
        params.append(content_format)
    if topic:
        where.append("c.llm_labels->>'topic' = ?")
        params.append(topic)
    rows = conn.execute(
        f"""
        SELECT t.channel_id, t.note, t.added_at, t.last_refreshed_at, t.active,
               c.title, c.custom_url, c.subscriber_count, c.video_count, c.view_count,
               c.llm_labels, c.llm_labeled_at,
               (SELECT COUNT(*) FROM channel_stats_history h WHERE h.channel_id=t.channel_id)
                   AS snapshots
        FROM tracked_channels t LEFT JOIN channels c ON c.channel_id = t.channel_id
        WHERE {' AND '.join(where)} ORDER BY c.subscriber_count DESC
        """, params
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        labels = d.pop("llm_labels", None) or {}
        labeled_at = d.pop("llm_labeled_at", None)
        d["aiLabels"] = {
            "isFaceless": labels.get("is_faceless"), "contentFormat": labels.get("content_format"),
            "topic": labels.get("topic"), "labeledAt": labeled_at,
        } if labels else None
        out.append(d)
    return out


def resolve_channel_id(conn, api_key: str, raw: str) -> str:
    """Turn a UC id, @handle or channel URL into a real UC channel id.

    A raw handle/URL must never land in tracked_channels as-is -- the worker
    can't poll it. Checks the local `channels` table first (0 quota, via a
    previously-collected custom_url), then falls back to the YouTube API.
    Raises ValueError with a readable message when it can't be resolved;
    callers must not write anything to the watchlist in that case.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("empty channel reference")
    m = collecting.CHANNEL_ID_RE.search(raw)
    if m:
        return m.group(1)
    handle_m = collecting.HANDLE_RE.search(raw)
    handle = handle_m.group(1) if handle_m else (raw if not raw.startswith("http") else None)
    if handle:
        row = conn.execute(
            "SELECT channel_id FROM channels WHERE lower(custom_url) IN (?, ?) LIMIT 1",
            ("@" + handle.lower(), handle.lower())).fetchone()
        if row:
            return row["channel_id"]
    if not api_key:
        raise ValueError(f"can't resolve channel without an API key: {raw}")
    ch = collecting.resolve_channel(api_key, raw)
    if not ch:
        raise ValueError(f"channel not found: {raw}")
    return ch["id"]


def track(channel_id: str, note: str = None) -> dict:
    conn = db.get_conn()
    db.track_channel(conn, channel_id, note)
    conn.commit()
    conn.close()
    return {"channelId": channel_id, "tracked": True, "note": note}


def untrack(channel_id: str) -> dict:
    conn = db.get_conn()
    db.untrack_channel(conn, channel_id)
    conn.commit()
    conn.close()
    return {"channelId": channel_id, "tracked": False}


def fix_tracked(api_key: str = None, apply: bool = False) -> dict:
    """Watchlist rows whose channel_id isn't a real UC id -- written before
    resolve_channel_id existed, when track_channel(collect=False) stored the
    raw @handle/URL. Dry-run by default: reports what would change. With
    apply=True, resolvable rows are rewritten to the real UC id (dropping the
    row if that id is already tracked), and rows that can't be resolved are
    removed.
    """
    conn = db.get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT channel_id FROM tracked_channels WHERE channel_id NOT LIKE 'UC%'"
        ).fetchall()]
        fixed, unresolved = [], []
        for r in rows:
            raw = r["channel_id"]
            try:
                cid = resolve_channel_id(conn, api_key, raw)
            except ValueError:
                unresolved.append(raw)
                continue
            fixed.append({"was": raw, "now": cid})
            if apply:
                dup = conn.execute(
                    "SELECT 1 FROM tracked_channels WHERE channel_id=?", (cid,)).fetchone()
                if dup:
                    conn.execute("DELETE FROM tracked_channels WHERE channel_id=?", (raw,))
                else:
                    conn.execute(
                        "UPDATE tracked_channels SET channel_id=? WHERE channel_id=?",
                        (cid, raw))
        if apply:
            for raw in unresolved:
                conn.execute("DELETE FROM tracked_channels WHERE channel_id=?", (raw,))
            conn.commit()
        return {"applied": apply, "fixed": fixed, "removed": unresolved}
    finally:
        conn.close()


# --------------------------------------------------------------- analytics

def _channel_videos(conn, channel_id, limit=500):
    return [dict(r) for r in conn.execute(
        "SELECT video_id, title, tags, published_at, view_count, like_count, "
        "comment_count, duration_seconds, is_short, category_id "
        "FROM videos WHERE channel_id = ? ORDER BY published_at DESC LIMIT ?",
        (channel_id, limit),
    ).fetchall()]


def _channel_history(conn, channel_id):
    rows = conn.execute(
        "SELECT captured_at, subscriber_count, video_count, view_count "
        "FROM channel_stats_history WHERE channel_id = ? ORDER BY captured_at",
        (channel_id,),
    ).fetchall()
    return [(_dt(r["captured_at"]), r) for r in rows if _dt(r["captured_at"])]


def _closest_before(history, target_ts):
    best = None
    for ts, row in history:
        if ts.timestamp() <= target_ts:
            best = (ts, row)
    return best


def _growth_block(history, now_row, field, days):
    if not history or now_row is None:
        return None
    latest_ts = history[-1][0]
    prev = _closest_before(history, latest_ts.timestamp() - days * 86400)
    if not prev or prev[0] == latest_ts:
        return None
    actual_days = (latest_ts.timestamp() - prev[0].timestamp()) / 86400
    if actual_days <= 0:
        return None
    g = M.growth(now_row[field], prev[1][field], actual_days)
    if g:
        g["window_days_actual"] = round(actual_days, 2)
    return g


def channel_analytics(channel_id: str, period: str = "30d",
                      baseline_n: int = M.DEFAULT_BASELINE_N) -> dict:
    conn = db.get_conn()
    MC.ensure_loaded(conn)
    ch = conn.execute("SELECT * FROM channels WHERE channel_id = ?", (channel_id,)).fetchone()
    if not ch:
        conn.close()
        return {"channelId": channel_id, "found": False,
                "hint": "Run collect_channel / track_channel first."}
    ch = dict(ch)
    videos = _channel_videos(conn, channel_id)
    history = _channel_history(conn, channel_id)
    conn.close()

    started = _dt(ch.get("published_at"))
    age_days = ((P.now() - started).total_seconds() / 86400) if started else None
    longform = [v for v in videos if not M.is_short(v["duration_seconds"])]
    shorts = [v for v in videos if M.is_short(v["duration_seconds"])]
    views_list = [v["view_count"] or 0 for v in longform] or [v["view_count"] or 0 for v in videos]

    # ---- outliers, computed chronologically against a rolling median baseline
    # stage 14: also against a period baseline (this channel's OTHER videos of
    # the same format within +/-15d of this one) -- outlierScore/outlierBand
    # stay the rolling numbers unchanged for backward compat, outlierScoreRolling
    # is the same number under the plan's chosen name, outlierScorePeriod is new.
    siblings_all = [{"video_id": v["video_id"], "published_at": v["published_at"],
                    "view_count": v["view_count"] or 0,
                    "is_short": M.is_short(v["duration_seconds"])} for v in videos]
    chron = sorted(longform, key=lambda v: v["published_at"] or "")
    hist_views, outliers = [], []
    for v in chron:
        base = M.baseline_median(hist_views, baseline_n)
        age = P.days_since(v["published_at"])
        period_base = M.period_baseline_median(
            [s for s in siblings_all if s["video_id"] != v["video_id"]],
            v["published_at"], target_is_short=False)
        if base:
            score = (v["view_count"] or 0) / base
            outliers.append({
                "videoId": v["video_id"], "title": v["title"],
                "publishedAt": v["published_at"], "views": v["view_count"],
                "ageDays": round(age, 1),
                "outlierScore": round(score, 2),
                "outlierScoreRolling": round(score, 2),
                "outlierScoreAgeAdjusted": round(
                    M.age_adjusted_outlier(v["view_count"] or 0, base, age) or 0, 2),
                "band": M.outlier_band(score),
                "baselineMedianViews": int(base),
                "baselineMedianViewsPeriod": int(period_base) if period_base else None,
                "outlierScorePeriod": round((v["view_count"] or 0) / period_base, 2)
                    if period_base else None,
            })
        hist_views.append(v["view_count"] or 0)
    outliers.sort(key=lambda x: x["outlierScore"], reverse=True)

    # ---- growth from our own snapshots
    now_row = history[-1][1] if history else ch
    growth = {}
    for label, days in (("24h", 1), ("7d", 7), ("30d", 30), ("90d", 90)):
        growth[label] = {
            "subscribers": _growth_block(history, now_row, "subscriber_count", days),
            "views": _growth_block(history, now_row, "view_count", days),
        }
    per_day_subs = ((growth["30d"]["subscribers"] or {}).get("per_day")
                    or (growth["7d"]["subscribers"] or {}).get("per_day"))
    per_day_views = ((growth["30d"]["views"] or {}).get("per_day")
                     or (growth["7d"]["views"] or {}).get("per_day"))
    lifetime_view_rate = (ch["view_count"] / age_days) if age_days and ch["view_count"] else None
    mom = M.momentum(per_day_views, lifetime_view_rate)

    monthly_views = (per_day_views * 30) if per_day_views else (
        (ch["view_count"] / age_days * 30) if age_days and ch["view_count"] else 0)
    cat = st.mode([v["category_id"] for v in videos if v["category_id"]]) if any(
        v["category_id"] for v in videos) else None

    recent_cut, _ = P.window(period)
    recent = [v for v in videos if not recent_cut or (v["published_at"] or "") >= recent_cut]

    return {
        "channelId": channel_id,
        "found": True,
        "profile": {
            "title": ch["title"], "handle": ch["custom_url"], "country": ch["country"],
            "subscribers": ch["subscriber_count"],
            "subscribersHidden": bool(ch.get("hidden_subs")),
            "totalViews": ch["view_count"], "videoCount": ch["video_count"],
            "createdAt": ch.get("published_at"),
            "ageDays": round(age_days) if age_days else None,
            "topics": json.loads(ch["topic_categories"]) if ch.get("topic_categories") else [],
            "keywords": (ch.get("keywords") or "")[:300] or None,
            "mainCategory": C.title_for(cat) if cat else None,
            "aiLabels": {
                "isFaceless": ch["llm_labels"].get("is_faceless"),
                "contentFormat": ch["llm_labels"].get("content_format"),
                "topic": ch["llm_labels"].get("topic"),
                "language": ch["llm_labels"].get("language"),
                "labeledAt": ch.get("llm_labeled_at"),
                "model": ch.get("llm_model"),
            } if ch.get("llm_labels") else None,
        },
        "cadence": {
            "uploadsPerWeekLifetime": M.uploads_per_week(ch["video_count"] or 0, age_days),
            "uploadsInPeriod": len(recent),
            "period": period,
            "avgVideoLengthSeconds": int(st.mean([v["duration_seconds"] or 0 for v in videos]))
                if videos else None,
            "daysSinceLastUpload": round(P.days_since(videos[0]["published_at"]), 1)
                if videos else None,
            "longformCount": len(longform), "shortsCount": len(shorts),
            "shortsSharePercent": round(len(shorts) / len(videos) * 100, 1) if videos else 0,
        },
        "performance": {
            "videosAnalysed": len(videos),
            "medianViewsPerVideo": int(st.median(views_list)) if views_list else None,
            "avgViewsPerVideo": int(st.mean(views_list)) if views_list else None,
            "viralSkew": M.skew(views_list),
            "medianEngagementRate": M.safe_median(
                [M.engagement_rate(v["view_count"] or 0, v["like_count"], v["comment_count"])
                 for v in videos]),
            "medianViewsPerSubscriber": round(M.safe_median(
                [M.views_per_subscriber(v["view_count"] or 0, ch["subscriber_count"])
                 for v in videos]) or 0, 3),
            "bestVideo": max(videos, key=lambda v: v["view_count"] or 0)["title"] if videos else None,
            "shortsMedianViews": int(st.median([v["view_count"] or 0 for v in shorts]))
                if shorts else None,
            "longformMedianViews": int(st.median([v["view_count"] or 0 for v in longform]))
                if longform else None,
        },
        "growth": growth,
        "growthAvailable": len(history) > 1,
        "snapshots": len(history),
        "momentum": mom,
        "grade": M.grade(mom),
        "projections": {
            "subscribers_1y": M.project(ch["subscriber_count"], per_day_subs, 365),
            "subscribers_3y": M.project(ch["subscriber_count"], per_day_subs, 1095),
            "views_1y": M.project(ch["view_count"], per_day_views, 365),
            "model": "linear extrapolation of the 30d rate, same as Social Blade",
        },
        "revenue": {
            "monthlyViewsUsed": int(monthly_views),
            "socialBladeRange": M.revenue_range(monthly_views),
            "nicheModel": M.revenue_niche(monthly_views, C.rpm_niche(cat)),
            "warning": "AdSense only, ignores sponsorships; public estimates are "
                       "routinely off by 200-400%",
        },
        "topOutliers": outliers[:10],
        "caveats": [
            "subscriberCount is rounded to 3 significant figures by the API -- "
            "subscriber deltas are unreliable above ~100k subs",
            "growth/velocity fields are null until enough snapshots exist "
            "(run the worker or call refresh_channels/refresh_stats)",
        ],
    }


def compare_channels(channel_ids: list, period: str = "30d") -> dict:
    rows = []
    for cid in channel_ids:
        a = channel_analytics(cid, period=period)
        if not a.get("found"):
            rows.append({"channelId": cid, "found": False})
            continue
        rows.append({
            "channelId": cid,
            "title": a["profile"]["title"],
            "subscribers": a["profile"]["subscribers"],
            "totalViews": a["profile"]["totalViews"],
            "videoCount": a["profile"]["videoCount"],
            "ageDays": a["profile"]["ageDays"],
            "medianViewsPerVideo": a["performance"]["medianViewsPerVideo"],
            "medianViewsPerSubscriber": a["performance"]["medianViewsPerSubscriber"],
            "viralSkew": a["performance"]["viralSkew"],
            "uploadsPerWeek": a["cadence"]["uploadsPerWeekLifetime"],
            "shortsSharePercent": a["cadence"]["shortsSharePercent"],
            "subs_per_day_30d": (a["growth"]["30d"]["subscribers"] or {}).get("per_day"),
            "views_per_day_30d": (a["growth"]["30d"]["views"] or {}).get("per_day"),
            "momentum": a["momentum"], "grade": a["grade"],
            "monthlyRevenueEstimate": a["revenue"]["nicheModel"]["monthly_usd"],
            "bestOutlier": a["topOutliers"][0]["outlierScore"] if a["topOutliers"] else None,
        })
    ranked = [r for r in rows if r.get("found") is not False]
    ranked.sort(key=lambda r: r.get("medianViewsPerSubscriber") or 0, reverse=True)
    return {"period": period, "channels": rows,
            "rankedByViewsPerSubscriber": [r["channelId"] for r in ranked]}


# ---------------------------------------------------------------- velocity

def channel_velocity(channel_id: str, period: str = "30d", limit: int = 25) -> dict:
    """Per-video view velocity from our own snapshots -- the vidIQ/1of10 view."""
    conn = db.get_conn()
    start, _ = P.window(period)
    q = ("SELECT video_id, title, published_at, view_count, duration_seconds "
         "FROM videos WHERE channel_id = ?")
    params = [channel_id]
    if start:
        q += " AND published_at >= ?"
        params.append(start)
    q += " ORDER BY published_at DESC LIMIT ?"
    params.append(limit)
    videos = [dict(r) for r in conn.execute(q, params).fetchall()]

    out = []
    for v in videos:
        rows = conn.execute(
            "SELECT captured_at, view_count FROM video_stats_history "
            "WHERE video_id = ? ORDER BY captured_at", (v["video_id"],)).fetchall()
        hist = [(_dt(r["captured_at"]), r["view_count"] or 0) for r in rows if _dt(r["captured_at"])]
        v24 = M.vph_from_history(hist, 24)
        v48 = M.vph_from_history(hist, 48)
        prev24 = (v48 * 2 - v24) if (v24 is not None and v48 is not None) else None
        age_h = P.hours_since(v["published_at"])
        out.append({
            "videoId": v["video_id"], "title": v["title"],
            "publishedAt": v["published_at"], "ageHours": round(age_h, 1),
            "views": v["view_count"], "isShort": M.is_short(v["duration_seconds"]),
            "vphLifetime": round(M.vph_lifetime(v["view_count"] or 0, age_h), 1),
            "vph24h": round(v24, 1) if v24 is not None else None,
            "viewsGained24h": int(v24 * 24) if v24 is not None else None,
            "acceleration": M.acceleration(v24, prev24),
            "trend": ("heating up" if (M.acceleration(v24, prev24) or 0) > 1.5
                      else "cooling" if (M.acceleration(v24, prev24) or 9) < 0.7
                      else "steady" if v24 is not None else None),
            "snapshots": len(hist),
        })
    conn.close()
    out.sort(key=lambda x: (x["vph24h"] is not None, x["vph24h"] or 0), reverse=True)
    return {"channelId": channel_id, "period": period, "videos": out,
            "note": "vph24h/acceleration need at least two snapshots ~24h apart"}


def title_changes(period: str = "7d", channel_id: str = None, limit: int = 50) -> dict:
    """Creators re-titling or swapping a thumbnail is a strong signal that a
    video underperformed -- and 1of10 charges for exactly this detection."""
    conn = db.get_conn()
    start, _ = P.window(period)
    q = ("SELECT ch.video_id, ch.changed_at, ch.field, ch.old_value, ch.new_value, "
         "v.title, v.channel_id, v.view_count, c.title AS channel_title "
         "FROM video_changes ch JOIN videos v ON v.video_id = ch.video_id "
         "LEFT JOIN channels c ON c.channel_id = v.channel_id WHERE 1=1")
    params = []
    if start:
        q += " AND ch.changed_at >= ?"
        params.append(start)
    if channel_id:
        q += " AND v.channel_id = ?"
        params.append(channel_id)
    q += " ORDER BY ch.changed_at DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    return {"period": period, "changes": rows, "count": len(rows)}


# ------------------------------------------------------- publishing heatmap

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def best_time_to_publish(niche: str = None, channel_id: str = None, period: str = "90d",
                         min_samples: int = 3, timezone_offset_hours: int = 0) -> dict:
    """TubeBuddy's "score every hour of the week", rebuilt from outlier scores.

    We score each of the 168 weekday/hour buckets by the MEDIAN age-adjusted
    outlier of videos published in it, not by raw views -- otherwise the bucket
    that happens to contain one huge channel wins every time.
    """
    from application import discovery as trends
    rows = trends.load_window(period=period, niche=niche,
                              channel_ids=[channel_id] if channel_id else None)
    buckets = defaultdict(list)
    for r in rows:
        ts = _dt(r["published_at"])
        if not ts:
            continue
        ts = ts.timestamp() + timezone_offset_hours * 3600
        ts = datetime.fromtimestamp(ts, tz=timezone.utc)
        score = r["outlierScoreAgeAdjusted"] or r["outlierScore"] or r["viewsPerSubscriber"]
        if score:
            buckets[(ts.weekday(), ts.hour)].append(score)

    scored = {k: st.median(v) for k, v in buckets.items() if len(v) >= min_samples}
    if not scored:
        return {"period": period, "samples": len(rows), "heatmap": [],
                "hint": f"need >= {min_samples} videos per weekday/hour bucket; "
                        "collect more of this niche or widen the period"}
    top = max(scored.values())
    heatmap = [
        {"weekday": WEEKDAYS[d], "hour": h,
         "score": round(v / top * 100), "samples": len(buckets[(d, h)]),
         "medianOutlier": round(v, 2)}
        for (d, h), v in sorted(scored.items(), key=lambda kv: -kv[1])
    ]
    return {
        "period": period, "niche": niche, "channelId": channel_id,
        "videosAnalysed": len(rows), "bucketsScored": len(scored),
        "timezoneOffsetHours": timezone_offset_hours,
        "best": heatmap[:10], "worst": heatmap[-5:], "heatmap": heatmap,
        "note": "score is relative (best bucket = 100); publish time correlates "
                "with performance, it does not cause it",
    }


def title_patterns(niche: str = None, channel_id: str = None, period: str = "90d",
                   outlier_threshold: float = 3.0, min_videos: int = 4,
                   top_n: int = 25) -> dict:
    """Which title phrases actually correlate with breakouts in this niche."""
    from application import discovery as trends
    rows = trends.load_window(period=period, niche=niche,
                              channel_ids=[channel_id] if channel_id else None)
    shaped = [{"video_id": r["video_id"], "title": r["title"], "tags": r["tags"],
               "views": r["view_count"] or 0,
               "outlier": r["outlierScoreAgeAdjusted"] or r["outlierScore"]
               or r["outlierScoreNexlev"]} for r in rows]
    stats, total, base = K.aggregate(shaped, use_tags=False, use_title=True,
                                     n_max=3, outlier_threshold=outlier_threshold)
    ranked = K.score(stats, total, base, min_videos=min_videos, top_n=top_n, sort_by="lift")
    return {
        "period": period, "niche": niche, "channelId": channel_id,
        "videosAnalysed": total,
        "outlierBaseRatePercent": round(base * 100, 2),
        "patterns": [{k: p[k] for k in ("keyword", "videos", "outlierLift",
                                        "medianOutlier", "medianViews", "examples")}
                     for p in ranked],
        "note": f"outlierLift = P(outlier>={outlier_threshold} | phrase in title) "
                f"/ P(outlier>={outlier_threshold}). Correlation, not causation.",
    }


# ------------------------------------------------------ curve calibration

def calibrate_maturity_curve(min_videos: int = 30) -> dict:
    """Measure the maturity curve from our own snapshots (see
    metrics.fit_maturity_curve for the method and the checks). Read-only:
    the worker stores a passing curve and every process then uses it
    automatically (application/maturity_curve.py)."""
    fit = MC.calibrate(min_videos=min_videos)
    if not fit["calibrated"]:
        return {**fit, "videosWithFullHistory": fit["videosUsed"],
                "needed": min_videos,
                "hint": f"{fit['reason']}; keep the worker running -- it re-checks "
                        "daily and switches over by itself once the checks pass"}
    return {**fit,
            "howToApply": "nothing to do: the worker stores a passing curve and "
                          "every process switches to it (MATURITY_CURVE_AUTO=0 "
                          "turns that off)"}


# ------------------------------------------------- channel-level discovery
# Rebuilds of the two channel lists on NexLev's Niche Finder overview, which
# are channel-scoped rather than video-scoped: "Recently Added Outlier Channels"
# and "Niches with High Future Competition".

def _channel_rows_from_videos(rows):
    """Group enriched video rows by channel and summarise each channel."""
    by_channel = defaultdict(list)
    for r in rows:
        by_channel[r["channel_id"]].append(r)
    out = []
    for cid, vids in by_channel.items():
        first = vids[0]
        views = [v["view_count"] or 0 for v in vids]
        # Медианная база требует минимум 4 загрузок канала в базе. Если их нет,
        # падаем на пожизненное среднее (формула NexLev) — число получается
        # другого качества, поэтому помечаем, а не делаем вид, что оно то же.
        has_median = any(v["outlierScore"] is not None for v in vids)
        mults = [v["outlierScoreAgeAdjusted"] or v["outlierScore"]
                 or v["outlierScoreNexlev"] or 0 for v in vids]
        best = max(vids, key=lambda v: (v["outlierScoreAgeAdjusted"]
                                        or v["outlierScore"] or 0))
        out.append({
            "channelId": cid,
            "channelTitle": first["channel_title"],
            "channelUrl": first["channel_url"],
            "subscribers": first["subs"],
            "channelVideoCount": first["ch_video_count"],
            "channelTotalViews": first["ch_view_count"],
            "country": first["channel_country"],
            "category": first["category"],
            "categoryId": first["category_id"],
            "videosInWindow": len(vids),
            "medianViewsInWindow": int(st.median(views)) if views else 0,
            "maxViewsInWindow": max(views) if views else 0,
            "multiplier": round(max(mults), 2) if mults else None,
            "multiplierBasis": "median" if has_median else "lifetime-mean",
            "videosInDb": first["ch_video_count"],
            "medianMultiplier": round(st.median(mults), 2) if mults else None,
            "medianViewsPerSubscriber": round(
                st.median([v["viewsPerSubscriber"] for v in vids]), 3),
            "bestVideo": {"videoId": best["video_id"], "title": best["title"],
                          "views": best["view_count"],
                          "publishedAt": best["published_at"],
                          "vph": best["vphLifetime"]},
            "_rows": vids,
        })
    return out


def _strength(multiplier):
    """The 1-4 segment strength bar NexLev shows next to each multiplier."""
    if multiplier is None:
        return None
    for i, threshold in enumerate((2, 3, 5, 10)):
        if multiplier < threshold:
            return i
    return 4


def recently_added_outlier_channels(period: str = "24h", period_by: str = "discovered",
                                    min_multiplier: float = 2.0,
                                    max_subscribers: int = None,
                                    min_subscribers: int = None, niche: str = None,
                                    category_id: str = None, region: str = None,
                                    exclude_shorts: bool = True, limit: int = 25) -> dict:
    """Channels that showed up in the corpus recently AND are outperforming.

    The channel-level counterpart to viral_videos_small_channels. Defaults to
    period_by="discovered" because "recently added" is about when WE first saw
    the channel, not when it uploaded -- which is also why NexLev's own
    "Last 24 Hours" list is full of year-old videos.
    """
    from application import discovery as trends
    rows = trends.load_window(period=period, period_by=period_by, niche=niche,
                              category_id=category_id, region=region,
                              max_subscribers=max_subscribers,
                              min_subscribers=min_subscribers,
                              exclude_shorts=exclude_shorts)
    channels = _channel_rows_from_videos(rows)
    for ch in channels:
        ch.pop("_rows", None)
        ch["strength"] = _strength(ch["multiplier"])
        ch["band"] = M.outlier_band(ch["multiplier"])
    before = len(channels)
    channels = [c for c in channels
                if (c["multiplier"] or 0) >= min_multiplier]
    channels.sort(key=lambda c: c["multiplier"] or 0, reverse=True)
    hint = None
    if not channels:
        hint = (f"Ни один из {before} каналов в окне не дотянул до множителя "
                f"{min_multiplier} — понизьте min_multiplier." if before else
                f"В окне {period} по '{period_by}' нет видео — сначала соберите корпус. "
                f"Множитель считается по медиане предыдущих загрузок канала, поэтому "
                f"каналу нужно минимум 4 видео в базе.")
    return {
        "period": period, "periodBy": period_by,
        "minMultiplier": min_multiplier, "hint": hint,
        "channelsMatched": len(channels), "quotaUsed": 0,
        "legend": {"multiplier": "best age-adjusted outlier among the channel's "
                                 "videos in this window",
                   "strength": "0-4 bar: <2x, 2-3x, 3-5x, 5-10x, >10x"},
        "channels": channels[:limit],
    }


def high_future_competition(period: str = "30d", period_by: str = "published",
                            niche: str = None, region: str = None,
                            category_id: str = None, min_videos: int = 2,
                            limit: int = 25) -> dict:
    """Who is about to become your competition: young, fast-uploading channels
    whose recent videos already outperform.

    NexLev ships this as a niche-level list off their curated index; without
    that index we compute it per channel from our own corpus and then roll it
    up by category, which is the actionable half anyway. The score is:

        competitionScore = medianMultiplier * log2(1 + uploadsInWindow)
                           * youthFactor

    youthFactor rewards channels under a year old -- an established channel
    performing well is a competitor you already have, a six-month-old channel
    doing the same is one you are about to get.
    """
    from application import discovery as trends
    rows = trends.load_window(period=period, period_by=period_by, niche=niche,
                              region=region, category_id=category_id,
                              exclude_shorts=True)
    channels = _channel_rows_from_videos(rows)

    conn = db.get_conn()
    ages = {r["channel_id"]: r["published_at"] for r in conn.execute(
        "SELECT channel_id, published_at FROM channels").fetchall()}
    conn.close()

    scored = []
    for ch in channels:
        ch.pop("_rows", None)
        if ch["videosInWindow"] < min_videos:
            continue
        created = _dt(ages.get(ch["channelId"]))
        age_days = ((P.now() - created).total_seconds() / 86400) if created else None
        youth = 1.0
        if age_days is not None:
            youth = 2.0 if age_days < 180 else 1.5 if age_days < 365 else \
                1.0 if age_days < 1095 else 0.6
        med = ch["medianMultiplier"] or 0
        ch["channelAgeDays"] = round(age_days) if age_days else None
        ch["uploadsInWindow"] = ch["videosInWindow"]
        ch["youthFactor"] = youth
        ch["competitionScore"] = round(med * math.log2(1 + ch["videosInWindow"]) * youth, 2)
        scored.append(ch)
    scored.sort(key=lambda c: c["competitionScore"], reverse=True)

    by_cat = defaultdict(list)
    for ch in scored:
        by_cat[ch["categoryId"] or "unknown"].append(ch)
    niches = sorted(
        ({"categoryId": cid,
          "category": C.title_for(cid),
          "risingChannels": len(chs),
          "medianCompetitionScore": round(st.median(
              [c["competitionScore"] for c in chs]), 2),
          "medianChannelAgeDays": int(st.median(
              [c["channelAgeDays"] for c in chs if c["channelAgeDays"]]))
          if any(c["channelAgeDays"] for c in chs) else None,
          "topChannel": chs[0]["channelTitle"]}
         for cid, chs in by_cat.items()),
        key=lambda x: (x["risingChannels"], x["medianCompetitionScore"]), reverse=True)

    return {
        "period": period, "periodBy": period_by, "quotaUsed": 0,
        "channelsAnalysed": len(scored),
        "note": "approximation of NexLev's niche-level list, computed per channel "
                "from the local corpus and rolled up by category",
        "byCategory": niches[:limit],
        "channels": scored[:limit],
    }


def channel_history(channel_id: str, limit: int = 400) -> dict:
    """Ряд снимков канала для графика «просмотры во времени».

    Это ровно та картинка, за которую берут деньги ViewStats и 1of10, и она
    существует только потому, что воркер записывает цифры по расписанию:
    YouTube API отдаёт лишь состояние «прямо сейчас».
    """
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT captured_at, subscriber_count, video_count, view_count "
        "FROM channel_stats_history WHERE channel_id = ? "
        "ORDER BY captured_at DESC LIMIT ?", (channel_id, limit)).fetchall()
    conn.close()
    points = [{"t": r["captured_at"], "subscribers": r["subscriber_count"],
               "videos": r["video_count"], "views": r["view_count"]}
              for r in reversed(rows)]
    return {
        "channelId": channel_id,
        "points": points,
        "count": len(points),
        "hint": None if len(points) > 1 else
        "Нужно минимум два снимка. Их пишет воркер: docker compose up -d worker",
    }

"""The three period-scoped discovery sections, computed over the local database:

  * viral_videos_small_channels  -- NexLev's flagship list, done better
  * most_popular_categories      -- category ranking + period-over-period shift
  * trending_keywords            -- rising phrases with a performance lift

All of these read only what collect_* already stored, so they cost ZERO YouTube
quota and can be called as often as you like. `period` accepts 24h / 48h / 7d /
30d / 90d / all and, unless stated otherwise, filters by PUBLICATION date --
"what came out in the last 24 hours". Metrics that describe growth *during* the
window (vph24h, viewsGained) come from our own stats snapshots and are null
until the worker has been running long enough.
"""
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from application import collecting as collector
from domain import metrics as M
from domain import periods as P
from domain import keywords as K
from infrastructure.categories import repository as C


# ------------------------------------------------------------------ loading

BASE_SQL = """
SELECT v.video_id, v.channel_id, v.title, v.tags, v.published_at, v.first_seen_at,
       v.view_count,
       v.like_count, v.comment_count, v.duration_seconds, v.thumbnail,
       v.category_id, v.region, v.default_language, v.is_short,
       v.contains_synthetic_media,
       c.title AS channel_title, c.custom_url AS channel_url,
       c.subscriber_count AS subs, c.video_count AS ch_video_count,
       c.view_count AS ch_view_count, c.country AS channel_country
FROM videos v JOIN channels c ON c.channel_id = v.channel_id
"""


def _filters(niche=None, languages=None, region=None, category_id=None,
             max_subscribers=None, min_subscribers=None, min_views=None,
             channel_ids=None):
    joins, where, params = "", [], []
    if niche:
        joins += " JOIN video_niches vn ON vn.video_id = v.video_id AND vn.niche_slug = ?"
        params.append(niche)
    if region:
        where.append("(v.region = ? OR c.country = ?)")
        params += [region, region]
    if category_id is not None:
        where.append("v.category_id = ?")
        params.append(str(category_id))
    if max_subscribers is not None:
        where.append("(c.subscriber_count IS NULL OR c.subscriber_count <= ?)")
        params.append(max_subscribers)
    if min_subscribers is not None:
        where.append("c.subscriber_count >= ?")
        params.append(min_subscribers)
    if min_views is not None:
        where.append("v.view_count >= ?")
        params.append(min_views)
    if channel_ids:
        where.append("v.channel_id IN (%s)" % ",".join("?" * len(channel_ids)))
        params += list(channel_ids)
    return joins, where, params


def _lang_ok(row, languages):
    if not languages:
        return True
    lang = (row["default_language"] or "").lower()[:2]
    if not lang:
        return True  # unknown language: don't silently drop it
    return lang in [str(l).lower()[:2] for l in languages]


# Which timestamp the window applies to.
#   published  -- "what came out in the last 24h"   (default, the obvious reading)
#   discovered -- "what WE first saw in the last 24h" (what NexLev's UI actually
#                 does: their "Last 24 hours" list is full of year-old videos,
#                 because the window is about when the item entered their index)
PERIOD_COLUMNS = {
    "published": "v.published_at",
    "discovered": "COALESCE(v.first_seen_at, v.updated_at, v.published_at)",
    "updated": "COALESCE(v.updated_at, v.published_at)",
}


def load_window(period="7d", offset=0, ref=None, period_by="published", **filters):
    """Rows inside the window, enriched with every metric we can compute.

    `offset=1` returns the immediately preceding window (for growth comparisons).
    `period_by` selects which timestamp the window filters on -- see
    PERIOD_COLUMNS. Age, VPH and maturity are ALWAYS measured from the real
    publication date regardless, because that is what they mean.
    """
    column = PERIOD_COLUMNS.get(period_by, PERIOD_COLUMNS["published"])
    start, end = P.window(period, ref=ref, offset=offset)
    languages = filters.pop("languages", None)
    exclude_shorts = filters.pop("exclude_shorts", False)
    only_shorts = filters.pop("only_shorts", False)
    joins, where, params = _filters(**filters)

    if start:
        where.append(f"{column} >= ?")
        params.append(start)
    if offset > 0 or start:
        where.append(f"{column} < ?")
        params.append(end)

    sql = BASE_SQL + joins + (" WHERE " + " AND ".join(where) if where else "")
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    rows = [r for r in rows if _lang_ok(r, languages)]
    for r in rows:
        r["is_short"] = bool(r["is_short"]) if r["is_short"] is not None \
            else M.is_short(r["duration_seconds"])
    if exclude_shorts:
        rows = [r for r in rows if not r["is_short"]]
    if only_shorts:
        rows = [r for r in rows if r["is_short"]]

    _enrich(conn, rows, ref=ref)
    conn.close()
    return rows


def _channel_baselines(conn, channel_ids, n=M.DEFAULT_BASELINE_N,
                       period_window_days=15, period_min_videos=5):
    """Two baselines per (channel, video): `out` (rolling -- median of the
    previous `n` long-form uploads, ViewStats/1of10 style) and `out_period`
    (stage 14 -- median of the channel's OTHER videos of the same format
    published within period_window_days of this one, Shorts and long-form
    kept separate). NexLev's lifetime-mean baseline is also kept, as
    `outlierScoreNexlev` in _enrich, so results stay comparable with their UI.
    """
    if not channel_ids:
        return {}, {}
    out = {}
    out_period = {}
    ids = list(channel_ids)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ("SELECT channel_id, video_id, published_at, view_count, duration_seconds "
             "FROM videos WHERE channel_id IN (%s) ORDER BY channel_id, published_at"
             % ",".join("?" * len(chunk)))
        per_channel = defaultdict(list)
        for r in conn.execute(q, chunk).fetchall():
            per_channel[r["channel_id"]].append(r)
        for ch, vids in per_channel.items():
            history = []
            siblings = [{"published_at": v["published_at"], "view_count": v["view_count"] or 0,
                        "is_short": M.is_short(v["duration_seconds"])} for v in vids]
            for idx, v in enumerate(vids):
                out[v["video_id"]] = M.baseline_median(history, n)
                is_short = M.is_short(v["duration_seconds"])
                out_period[v["video_id"]] = M.period_baseline_median(
                    siblings[:idx] + siblings[idx + 1:], v["published_at"], is_short,
                    window_days=period_window_days, min_videos=period_min_videos)
                if not is_short:
                    history.append(v["view_count"] or 0)
    return out, out_period


def _history_map(conn, video_ids):
    if not video_ids:
        return {}
    out = defaultdict(list)
    ids = list(video_ids)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ("SELECT video_id, captured_at, view_count FROM video_stats_history "
             "WHERE video_id IN (%s) ORDER BY video_id, captured_at"
             % ",".join("?" * len(chunk)))
        for r in conn.execute(q, chunk).fetchall():
            try:
                ts = datetime.fromisoformat(r["captured_at"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out[r["video_id"]].append((ts, r["view_count"] or 0))
    return out


def _enrich(conn, rows, ref=None):
    baselines, period_baselines = _channel_baselines(conn, {r["channel_id"] for r in rows})
    history = _history_map(conn, [r["video_id"] for r in rows])
    for r in rows:
        views = r["view_count"] or 0
        age_h = P.hours_since(r["published_at"], ref)
        age_d = age_h / 24.0
        base = baselines.get(r["video_id"])
        period_base = period_baselines.get(r["video_id"])
        r["ageHours"] = round(age_h, 1)
        r["ageDays"] = round(age_d, 2)
        r["baselineMedianViews"] = int(base) if base else None
        r["outlierScore"] = round(M.outlier_vs_median(views, [base] * 3), 3) if base else None
        # stage 14: outlierScore is kept as-is for backward compat (MCP/extension
        # already read it) -- outlierScoreRolling is the same number under the
        # plan's chosen name, outlierScorePeriod is the new period-median base.
        r["outlierScoreRolling"] = r["outlierScore"]
        r["baselineMedianViewsPeriod"] = int(period_base) if period_base else None
        r["outlierScorePeriod"] = round(views / period_base, 3) if period_base else None
        r["outlierScoreAgeAdjusted"] = (
            round(M.age_adjusted_outlier(views, base, age_d), 3) if base else None)
        r["outlierBand"] = M.outlier_band(r["outlierScore"])
        r["outlierScoreNexlev"] = round(
            M.outlier_score(views, r["ch_view_count"] or 0, r["ch_video_count"] or 0), 3)
        r["viewsPerSubscriber"] = round(M.views_per_subscriber(views, r["subs"]), 3)
        r["projected30dViews"] = int(M.projected_30d_views(views, age_d))
        r["projectedViewsPerSubscriber"] = round(
            r["projected30dViews"] / max(r["subs"] or 0, 1), 3)
        r["engagementRate"] = M.engagement_rate(views, r["like_count"], r["comment_count"])
        r["likeRate"] = M.like_rate(views, r["like_count"])
        r["vphLifetime"] = round(M.vph_lifetime(views, age_h), 1)
        hist = history.get(r["video_id"]) or []
        v24 = M.vph_from_history(hist, 24)
        v48 = M.vph_from_history(hist, 48)
        r["vph24h"] = round(v24, 1) if v24 is not None else None
        r["viewsGained24h"] = int(v24 * 24) if v24 is not None else None
        r["acceleration"] = M.acceleration(v24, (v48 * 2 - v24) if v48 is not None and v24 is not None else None)
        r["snapshots"] = len(hist)
        r["categoryId"] = r["category_id"]
        r["category"] = C.title_for(r["category_id"], r["region"] or "US")
        r["isShort"] = r["is_short"]
        # None = the creator's status.containsSyntheticMedia flag was absent
        # (old video, or the field wasn't requested) -- distinct from a
        # deliberate "no" (0), so callers can compute an honest coverage rate.
        csm = r.get("contains_synthetic_media")
        r["containsSyntheticMedia"] = bool(csm) if csm is not None else None
        # "viral" = how far past its own subscriber base it went, age-normalised
        r["viralScore"] = round(r["projectedViewsPerSubscriber"], 3)
    return rows


# ------------------------------------------- 1. Viral videos on small channels

SORTS = {
    "viral": "viralScore",
    "vsr": "viewsPerSubscriber",
    "views": "view_count",
    "outlier": "outlierScore",
    "outlier_adjusted": "outlierScoreAgeAdjusted",
    "vph": "vph24h",
    "vph_lifetime": "vphLifetime",
    "velocity": "viewsGained24h",
    "engagement": "engagementRate",
    "published": "published_at",
    "acceleration": "acceleration",
}


def viral_videos_small_channels(period="7d", period_by="published",
                                max_subscribers=10000, min_views=10000,
                                min_views_per_subscriber=1.0, min_outlier_score=None,
                                niche=None, languages=None, region=None,
                                category_id=None, max_channel_video_count=None,
                                exclude_shorts=True, only_shorts=False,
                                sort_by="viral", limit=25, preset=None) -> dict:
    """Small channel + big video = the algorithm chose the content, not the brand.

    NexLev's version of this list is literally `views >= X AND subs <= Y` sorted
    by insert date over a fixed 90-day window. Ours adds the two things that
    actually matter: views-per-subscriber (so a 5k-view video on a 200-sub
    channel outranks a 50k-view video on a 500k-sub one) and age normalisation
    (so a 12-hour-old rocket is not buried under a 3-week-old video).

    preset="niche_all" drops the size/views/VSR thresholds entirely -- "every
    video collected under this niche", for when you've already picked a niche
    and want to see the whole field rather than just the breakouts. Requires
    `niche`; without it, returns an error explaining why.
    """
    if preset == "niche_all":
        if not niche:
            return {
                "period": period, "periodBy": period_by,
                "error": "preset='niche_all' requires 'niche' to be set",
                "filters": {"preset": preset, "niche": niche},
                "sortBy": sort_by, "matched": 0, "quotaUsed": 0,
                "funnel": [], "hint": None, "results": [],
            }
        max_subscribers = None
        min_views = 0
        min_views_per_subscriber = 0

    # Filters are applied here rather than in SQL so we can count survivors at
    # each step: "matched: 0" with no explanation is useless, and the usual
    # cause is a default threshold, not an empty corpus.
    rows = load_window(period=period, period_by=period_by, niche=niche,
                       languages=languages, region=region, category_id=category_id,
                       exclude_shorts=exclude_shorts, only_shorts=only_shorts)
    funnel = [("videos in window", len(rows), None)]

    if max_subscribers is not None:
        rows = [r for r in rows if r["subs"] is None or r["subs"] <= max_subscribers]
        funnel.append((f"channel subs <= {max_subscribers:,}", len(rows), "max_subscribers"))
    if max_channel_video_count is not None:
        rows = [r for r in rows if (r["ch_video_count"] or 0) <= max_channel_video_count]
        funnel.append((f"channel videos <= {max_channel_video_count}", len(rows),
                       "max_channel_video_count"))
    if min_views:
        rows = [r for r in rows if (r["view_count"] or 0) >= min_views]
        funnel.append((f"video views >= {min_views:,}", len(rows), "min_views"))
    if min_views_per_subscriber:
        rows = [r for r in rows if r["viewsPerSubscriber"] >= min_views_per_subscriber]
        funnel.append((f"views per subscriber >= {min_views_per_subscriber}", len(rows),
                       "min_views_per_subscriber"))
    if min_outlier_score is not None:
        rows = [r for r in rows
                if (r["outlierScore"] or r["outlierScoreNexlev"] or 0) >= min_outlier_score]
        funnel.append((f"outlier score >= {min_outlier_score}", len(rows),
                       "min_outlier_score"))

    key = SORTS.get(sort_by, "viralScore")
    rows.sort(key=lambda r: (r.get(key) is not None, r.get(key) or 0), reverse=True)

    return {
        "period": period,
        "periodBy": period_by,
        "filters": {
            "maxSubscribers": max_subscribers, "minViews": min_views,
            "minViewsPerSubscriber": min_views_per_subscriber,
            "minOutlierScore": min_outlier_score, "niche": niche,
            "region": region, "categoryId": category_id,
            "excludeShorts": exclude_shorts, "onlyShorts": only_shorts,
            "preset": preset,
        },
        "sortBy": sort_by,
        "matched": len(rows),
        "quotaUsed": 0,
        "funnel": [{"step": name, "remaining": n} for name, n, _ in funnel],
        "hint": _funnel_hint(funnel, period, period_by),
        "results": [_video_out(r) for r in rows[:limit]],
    }


def _funnel_hint(funnel, period, period_by):
    """Say which threshold emptied the list, in words, with the fix."""
    if funnel[-1][1] > 0:
        return None
    if funnel[0][1] == 0:
        return (f"Ни одного видео в окне {period} по '{period_by}'. Соберите больше "
                f"(collect_channel дешевле всего), возьмите период шире, или "
                f"period_by='discovered', если видео старые, а в базу попали недавно.")
    # first step that reached zero
    for i, (name, n, param) in enumerate(funnel):
        if n == 0:
            before = funnel[i - 1][1]
            return (f"Фильтр '{name}' отсёк все {before} видео. Понизьте {param} "
                    f"или уберите его (0 / None).")
    survivors = funnel[-1]
    return f"Осталось 0 после '{survivors[0]}'."


def _video_out(r):
    return {
        "videoId": r["video_id"],
        "url": f"https://www.youtube.com/watch?v={r['video_id']}",
        "title": r["title"],
        "publishedAt": r["published_at"],
        "firstSeenAt": r["first_seen_at"],
        "ageHours": r["ageHours"],
        "ageDays": r["ageDays"],
        "views": r["view_count"],
        "likes": r["like_count"],
        "comments": r["comment_count"],
        "lengthSeconds": r["duration_seconds"],
        "isShort": r["isShort"],
        "thumbnail": r["thumbnail"],
        "category": r["category"],
        "estimatedRpm": M.rpm_effective(C.rpm_niche(r["category_id"])),
        "language": r["default_language"],
        "channelId": r["channel_id"],
        "channelTitle": r["channel_title"],
        "channelUrl": r["channel_url"],
        "channelSubscribers": r["subs"],
        "channelVideoCount": r["ch_video_count"],
        "viewsPerSubscriber": r["viewsPerSubscriber"],
        "projected30dViews": r["projected30dViews"],
        "viralScore": r["viralScore"],
        "outlierScore": r["outlierScore"],
        "outlierScoreRolling": r["outlierScoreRolling"],
        "outlierScorePeriod": r["outlierScorePeriod"],
        "outlierScoreAgeAdjusted": r["outlierScoreAgeAdjusted"],
        "outlierBand": r["outlierBand"],
        "outlierScoreNexlev": r["outlierScoreNexlev"],
        "baselineMedianViews": r["baselineMedianViews"],
        "baselineMedianViewsPeriod": r["baselineMedianViewsPeriod"],
        "engagementRate": r["engagementRate"],
        "vphLifetime": r["vphLifetime"],
        "vph24h": r["vph24h"],
        "viewsGained24h": r["viewsGained24h"],
        "acceleration": r["acceleration"],
        "snapshots": r["snapshots"],
    }


# ------------------------------------------------ 2. Most popular categories

def _synthetic_share(group) -> dict:
    """Share of videos in `group` disclosed as containsSyntheticMedia, only
    over videos where the flag is actually known -- the field is absent for
    videos collected before the API exposed it (30 Oct 2024) or fetched
    without part=status, so counting unknowns as "no" would understate it."""
    known = [r["containsSyntheticMedia"] for r in group if r["containsSyntheticMedia"] is not None]
    if not known:
        return {"syntheticSharePercent": None, "syntheticDisclosureCoveragePercent": 0.0}
    return {
        "syntheticSharePercent": round(sum(1 for k in known if k) / len(known) * 100, 1),
        "syntheticDisclosureCoveragePercent": round(len(known) / len(group) * 100, 1),
    }


def most_popular_categories(period="7d", period_by="published", niche=None, region=None,
                            languages=None, max_subscribers=None, exclude_shorts=False,
                            compare_previous=True, rank_by="views",
                            min_videos=3, limit=25) -> dict:
    """Which categories own the attention in this window, and which are moving.

    Computed from our own corpus on purpose: since 21 July 2025 YouTube's
    chart=mostPopular only covers Music / Movies / Gaming, so it cannot answer
    "what is popular in Education right now" at all.
    """
    common = dict(niche=niche, region=region, languages=languages,
                  max_subscribers=max_subscribers, exclude_shorts=exclude_shorts,
                  period_by=period_by)
    rows = load_window(period=period, **common)
    prev = load_window(period=period, offset=1, **common) if compare_previous else []

    def bucket(rs):
        out = defaultdict(list)
        for r in rs:
            out[str(r["category_id"]) if r["category_id"] else "unknown"].append(r)
        return out

    now_b, prev_b = bucket(rows), bucket(prev)
    total_views = sum((r["view_count"] or 0) for r in rows) or 1
    prev_total_views = sum((r["view_count"] or 0) for r in prev) or 1

    items = []
    for cid, group in now_b.items():
        if len(group) < min_videos:
            continue
        views = sum(r["view_count"] or 0 for r in group)
        share = views / total_views * 100
        pgroup = prev_b.get(cid, [])
        pviews = sum(r["view_count"] or 0 for r in pgroup)
        pshare = pviews / prev_total_views * 100 if prev else None
        top = sorted(group, key=lambda r: r["view_count"] or 0, reverse=True)[:3]
        channels = {r["channel_id"] for r in group}
        items.append({
            "categoryId": cid,
            "category": C.title_for(cid, region or "US"),
            "videos": len(group),
            "channels": len(channels),
            "totalViews": views,
            "medianViews": int(st.median([r["view_count"] or 0 for r in group])),
            "viewShare": round(share, 2),
            "previousViewShare": round(pshare, 2) if pshare is not None else None,
            "shareChange": round(share - pshare, 2) if pshare is not None else None,
            "viewsGrowth": (round((views - pviews) / pviews * 100, 1)
                            if prev and pviews else None),
            "medianOutlier": M.safe_median([r["outlierScore"] for r in group]),
            "medianViewsPerSubscriber": round(
                M.safe_median([r["viewsPerSubscriber"] for r in group]) or 0, 2),
            "medianEngagementRate": M.safe_median([r["engagementRate"] for r in group]),
            "shortsShare": round(sum(1 for r in group if r["isShort"]) / len(group) * 100, 1),
            **_synthetic_share(group),
            "estimatedRpmNiche": M.NICHE_RPM.get(C.rpm_niche(cid), M.NICHE_RPM["default"]),
            "topVideos": [{"videoId": r["video_id"], "title": r["title"],
                           "views": r["view_count"], "channel": r["channel_title"]}
                          for r in top],
        })
    # NexLev ranks these cards by CHANNEL count ("1,845 Channels"); ranking by
    # views is more informative but answers a different question, so both ship.
    rank_key = {"channels": "channels", "videos": "videos",
                "views": "totalViews"}.get(rank_by, "totalViews")
    items.sort(key=lambda x: x[rank_key], reverse=True)

    hint = None
    if not items:
        hint = (f"Ни одной категории с >= {min_videos} видео в окне {period}. "
                f"В окне всего {len(rows)} видео — соберите больше, возьмите период "
                f"шире, понизьте min_videos, или period_by='discovered'."
                if rows else
                f"В окне {period} по '{period_by}' нет видео вообще — сначала соберите "
                f"корпус (collect_channel / collect_niche).")

    return {
        "period": period,
        "periodBy": period_by,
        "rankedBy": rank_by,
        "comparedTo": "previous equal-length window" if compare_previous else None,
        "videosAnalysed": len(rows),
        "previousWindowVideos": len(prev) if compare_previous else None,
        "quotaUsed": 0,
        "hint": hint,
        "note": ("Computed from the local corpus. YouTube's own chart=mostPopular "
                 "has covered only Music/Movies/Gaming since 21 July 2025, so it "
                 "cannot rank general categories."),
        "categories": items[:limit],
    }


# ---------------------------------------------------- 3. Trending keywords

def _add_opportunity_scores(ranked):
    """vidIQ-style single 0-100 'opportunity score', added in place.

    There is no YouTube search-volume API to build a real vidIQ keyword score
    from (see the trendScore legend below), so this is a friendlier read of
    the same momentum/outlierLift/trendScore numbers already computed here --
    trendScore min-max scaled to 0-100 *within this result set*. It is a
    relative ranking aid for the phrases currently on screen, not a portable,
    absolute score you can compare across different queries or time windows.
    """
    scores = [r["trendScore"] for r in ranked if r.get("trendScore") is not None]
    if not scores:
        return
    lo, hi = min(scores), max(scores)
    span = hi - lo
    for r in ranked:
        ts = r.get("trendScore")
        if ts is None:
            r["opportunityScore"] = None
        else:
            r["opportunityScore"] = round(100 * (ts - lo) / span) if span > 0 else 100


def trending_keywords(period="7d", period_by="published", niche=None, region=None,
                      languages=None, category_id=None, max_subscribers=None,
                      exclude_shorts=False, source="both", ngram_max=3, min_videos=3,
                      top_n=30, sort_by="momentum", outlier_threshold=3.0,
                      compare_previous=True, keywords_mode="ngram",
                      semantic_similarity: float = 0.85) -> dict:
    """Phrases rising in this window, each with a performance lift.

    source: "titles" | "tags" | "both". Tags are still returned by the API to
    non-owners, but most creators leave them empty -- low tag coverage in the
    output means creators didn't set tags, not that we failed to read them.

    keywords_mode="semantic" (stage 10) merges phrases whose embeddings read
    as near-duplicates (paraphrases an n-gram model can't see) via
    domain.keywords.merge_semantic_synonyms -- the default "ngram" mode is
    byte-identical to before this existed.
    """
    common = dict(niche=niche, region=region, languages=languages,
                  category_id=category_id, max_subscribers=max_subscribers,
                  exclude_shorts=exclude_shorts, period_by=period_by)
    rows = load_window(period=period, **common)
    prev = load_window(period=period, offset=1, **common) if compare_previous else []

    def shape(rs):
        return [{"video_id": r["video_id"], "title": r["title"], "tags": r["tags"],
                 "views": r["view_count"] or 0,
                 "outlier": r["outlierScore"] or r["outlierScoreNexlev"]} for r in rs]

    use_tags = source in ("tags", "both")
    use_title = source in ("titles", "both")
    stats, total, base_rate = K.aggregate(shape(rows), use_tags, use_title,
                                          ngram_max, outlier_threshold)
    prev_stats, prev_total = ({}, 0)
    if compare_previous and prev:
        prev_stats, prev_total, _ = K.aggregate(shape(prev), use_tags, use_title,
                                                ngram_max, outlier_threshold)

    if keywords_mode == "semantic" and stats:
        try:
            import infrastructure.embeddings.fastembed_provider as emb
            # A real corpus can have tens of thousands of distinct n-gram
            # phrases; embedding+pairwise-comparing all of them is neither
            # fast nor useful (score() below only ever keeps ones with
            # >= min_videos anyway). Apply that same filter *before*
            # merging so semantic mode stays fast at real scale.
            candidates = {p: s for p, s in stats.items() if s["videos"] >= min_videos}
            below_threshold = {p: s for p, s in stats.items() if s["videos"] < min_videos}
            phrases = list(candidates.keys())
            phrase_vectors = dict(zip(phrases, emb.embed(phrases))) if phrases else {}
            merged = K.merge_semantic_synonyms(candidates, phrase_vectors,
                                               similarity_threshold=semantic_similarity)
            cluster_map = {name: s["mergedFrom"] for name, s in merged.items()}
            stats = {**merged, **below_threshold}
            if prev_stats:
                prev_stats = K.realign_prev_stats(prev_stats, cluster_map)
        except Exception:
            pass  # embeddings unavailable -- silently fall back to ngram stats

    ranked = K.score(stats, total, base_rate, prev_stats or None, prev_total,
                     min_videos=min_videos, top_n=top_n, sort_by=sort_by)
    _add_opportunity_scores(ranked)
    tagged = sum(1 for r in rows if r["tags"] and r["tags"] not in ("[]", "null"))
    hint = None
    if not ranked:
        hint = (f"Ни одной фразы, встречающейся в >= {min_videos} видео. В окне "
                f"{total} видео — понизьте min_videos (до 2 или 1), возьмите период "
                f"шире, или соберите больше." if total else
                f"В окне {period} по '{period_by}' нет видео — сначала соберите корпус.")

    return {
        "period": period,
        "periodBy": period_by,
        "videosAnalysed": total,
        "previousWindowVideos": prev_total,
        "outlierBaseRate": round(base_rate * 100, 2),
        "tagCoveragePercent": round(tagged / total * 100, 1) if total else 0,
        "source": source,
        "keywordsMode": keywords_mode,
        "sortBy": sort_by,
        "quotaUsed": 0,
        "hint": hint,
        "legend": {
            "momentum": "share this period / share last period (Laplace-smoothed); >2 is rising fast",
            "outlierLift": f"P(outlier>={outlier_threshold} | phrase) / P(outlier>={outlier_threshold}); >1.5 means the phrase correlates with breakouts",
            "trendScore": "log(1+videos) * outlierLift * momentum",
            "opportunityScore": "trendScore min-max scaled to 0-100 within this result set -- "
                                "a relative ranking aid, not a vidIQ-style absolute keyword score "
                                "(no YouTube search-volume API exists to build a real one from)",
        },
        "keywords": ranked,
    }


def top_tags_by_category(period="7d", period_by="published", niche=None, region=None,
                         languages=None, exclude_shorts=False, min_videos=3, top_n=15,
                         outlier_threshold=3.0) -> dict:
    """Literal YouTube tags -- exactly as the creator set them, never split
    into words -- ranked per category by how many videos use them and how
    much that tag correlates with an outlier result.

    Unlike trending_keywords(source="tags"), which N-grams tag text into
    topical phrases, this is for "what tags do winning videos in category X
    actually use" -- one call, every category with a qualifying tag,
    ranked busiest-category-first.
    """
    rows = load_window(period=period, niche=niche, region=region, languages=languages,
                       exclude_shorts=exclude_shorts, period_by=period_by)
    by_category = defaultdict(list)
    for r in rows:
        by_category[str(r["category_id"]) if r["category_id"] else "unknown"].append(r)

    def shape(rs):
        return [{"video_id": r["video_id"], "title": r["title"], "tags": r["tags"],
                 "views": r["view_count"] or 0,
                 "outlier": r["outlierScore"] or r["outlierScoreNexlev"]} for r in rs]

    categories = []
    for cid, rs in by_category.items():
        stats, total, base_rate = K.aggregate(shape(rs), phrase_fn=K.literal_tags_for_video,
                                              outlier_threshold=outlier_threshold)
        ranked = K.score(stats, total, base_rate, min_videos=min_videos, top_n=top_n,
                         sort_by="count", collapse=False)
        for r in ranked:
            r["tag"] = r.pop("keyword")
        if not ranked:
            continue
        categories.append({
            "categoryId": cid,
            "category": C.title_for(cid, region or "US"),
            "videosAnalysed": total,
            "tags": ranked,
        })
    categories.sort(key=lambda c: -c["videosAnalysed"])

    return {
        "period": period,
        "periodBy": period_by,
        "minVideos": min_videos,
        "topN": top_n,
        "quotaUsed": 0,
        "hint": None if categories else (
            f"Ни одного тега, встречающегося в >= {min_videos} видео ни в одной "
            f"категории. Понизьте min_videos или возьмите период шире."
            if rows else
            f"В окне {period} по '{period_by}' нет видео — сначала соберите корпус."),
        "categories": categories,
    }


# ------------------------------------------------------------ corpus health

def coverage(period="7d") -> dict:
    """Whether the DB actually has enough data for the requested window --
    call this first when a section comes back suspiciously empty."""
    conn = db.get_conn()
    start, _ = P.window(period)
    total = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    in_window = conn.execute(
        "SELECT COUNT(*) FROM videos WHERE published_at >= ?", (start,)
    ).fetchone()[0] if start else total
    with_cat = conn.execute(
        "SELECT COUNT(*) FROM videos WHERE category_id IS NOT NULL").fetchone()[0]
    snaps = conn.execute("SELECT COUNT(*) FROM video_stats_history").fetchone()[0]
    videos_with_hist = conn.execute(
        "SELECT COUNT(DISTINCT video_id) FROM video_stats_history").fetchone()[0]
    oldest = conn.execute(
        "SELECT MIN(captured_at) FROM video_stats_history").fetchone()[0]
    tracked = conn.execute(
        "SELECT COUNT(*) FROM tracked_channels WHERE active=1").fetchone()[0]
    calls_today = collector.search_calls_today(conn)
    calls_left = max(0, yt.SEARCH_DAILY_CALL_LIMIT - calls_today)
    conn.close()
    hint = ("Run collect_niche / collect_trending to fill the corpus, and keep "
            "the worker running (or call refresh_stats) so vph24h and "
            "viewsGained24h stop being null.")
    if calls_left == 0:
        hint = ("The 100-calls/day search.list bucket is exhausted for today "
                "(resets at midnight Pacific Time) -- collect_channel and "
                "refresh_stats still work, but collect_niche will fail until "
                "then.")
    return {
        "period": period,
        "videosTotal": total,
        "videosPublishedInPeriod": in_window,
        "videosWithCategory": with_cat,
        "statsSnapshots": snaps,
        "videosWithHistory": videos_with_hist,
        "historyStartedAt": oldest,
        "trackedChannels": tracked,
        "velocityMetricsAvailable": bool(oldest),
        "searchCallsToday": calls_today,
        "searchCallsLeftToday": calls_left,
        "hint": hint,
    }

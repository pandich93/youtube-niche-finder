"""Read-side search over the local database. Spends zero YouTube quota.

search_outliers() keeps its original signature so existing calls and configs do
not break, but every row now carries the richer metric set from metrics.py:
median-baseline outlier, age-adjusted outlier, views-per-subscriber, velocity
from our own snapshots, and NexLev's own score for comparison.
"""
import statistics as st

import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from domain import metrics as M
from application import collecting
from application import discovery as trends
from infrastructure.categories import repository as C


def search_outliers(query: str = None, niche: str = None, languages: list = None,
                    max_subscribers: int = None, max_channel_video_count: int = None,
                    min_upload_date: str = None, min_outlier_score: float = 0.0,
                    period: str = "all", region: str = None, category_id: str = None,
                    exclude_shorts: bool = False, only_shorts: bool = False,
                    min_video_length: int = None, max_video_length: int = None,
                    min_rpm: float = None, max_rpm: float = None,
                    sort_by: str = "outlier", limit: int = 25) -> list:
    rows = trends.load_window(
        period=period, niche=niche, languages=languages, region=region,
        category_id=category_id, max_subscribers=max_subscribers,
        exclude_shorts=exclude_shorts, only_shorts=only_shorts,
    )
    if min_upload_date:
        rows = [r for r in rows if (r["published_at"] or "") >= min_upload_date]
    if max_channel_video_count is not None:
        rows = [r for r in rows if (r["ch_video_count"] or 0) <= max_channel_video_count]
    if min_outlier_score:
        rows = [r for r in rows
                if (r["outlierScore"] or r["outlierScoreNexlev"] or 0) >= min_outlier_score]
    if min_video_length is not None:
        rows = [r for r in rows if (r["duration_seconds"] or 0) >= min_video_length]
    if max_video_length is not None:
        rows = [r for r in rows if (r["duration_seconds"] or 0) <= max_video_length]
    if min_rpm is not None or max_rpm is not None:
        for r in rows:
            r["_rpm"] = M.rpm_effective(C.rpm_niche(r["category_id"]))
        if min_rpm is not None:
            rows = [r for r in rows if r["_rpm"] >= min_rpm]
        if max_rpm is not None:
            rows = [r for r in rows if r["_rpm"] <= max_rpm]

    q_vec = None
    if query:
        import infrastructure.embeddings.fastembed_provider as emb
        q_vec = emb.embed(query)
        conn = db.get_conn()
        ids = [r["video_id"] for r in rows]
        vecs = {}
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            sql = ("SELECT video_id, embedding FROM videos WHERE video_id IN (%s)"
                   % ",".join("?" * len(chunk)))
            for rec in conn.execute(sql, chunk).fetchall():
                if rec["embedding"]:
                    vecs[rec["video_id"]] = emb.from_blob(rec["embedding"])
        conn.close()
        for r in rows:
            v = vecs.get(r["video_id"])
            r["semanticScore"] = round(emb.cosine(q_vec, v), 4) if v is not None else None

    if q_vec is not None:
        rows.sort(key=lambda r: (r.get("semanticScore") is not None,
                                 r.get("semanticScore") or 0), reverse=True)
    else:
        key = trends.SORTS.get(sort_by, "outlierScore")
        rows.sort(key=lambda r: (r.get(key) is not None, r.get(key) or 0), reverse=True)

    out = []
    for r in rows[:limit]:
        item = trends._video_out(r)
        item["semanticScore"] = r.get("semanticScore")
        out.append(item)
    return out


def _overview_from_rows(rows: list) -> dict:
    """Saturation/opportunity read shared by niche_overview (niche-slug
    anchored) and niche_overview_from_channel (channel anchored) -- same
    metrics, only how `rows` was selected differs."""
    channels = {}
    for r in rows:
        channels.setdefault(r["channel_id"], r)
    buckets = {"<1k": 0, "1k-10k": 0, "10k-50k": 0, "50k-200k": 0, "200k+": 0}
    for ch in channels.values():
        s = ch["subs"] or 0
        key = ("<1k" if s < 1_000 else "1k-10k" if s < 10_000 else
               "10k-50k" if s < 50_000 else "50k-200k" if s < 200_000 else "200k+")
        buckets[key] += 1

    outliers = [r["outlierScore"] for r in rows if r["outlierScore"]]
    subs = [r["subs"] for r in rows if r["subs"] is not None]
    views = [r["view_count"] or 0 for r in rows]
    cats = {}
    for r in rows:
        cid = str(r["category_id"]) if r["category_id"] else "unknown"
        cats[cid] = cats.get(cid, 0) + 1
    top_cats = sorted(cats.items(), key=lambda kv: -kv[1])[:5]

    small_breakouts = [r for r in rows
                       if (r["subs"] or 0) <= 10000 and r["viewsPerSubscriber"] >= 5]
    synthetic_known = [r["containsSyntheticMedia"] for r in rows
                       if r.get("containsSyntheticMedia") is not None]

    return {
        "video_count": len(rows),
        "channel_count": len(channels),
        "median_outlier_score": round(st.median(outliers), 2) if outliers else None,
        "median_subscribers": int(st.median(subs)) if subs else None,
        "median_views_per_video": int(st.median(views)) if views else None,
        "avg_views_per_video": int(st.mean(views)) if views else None,
        "viral_skew": M.skew(views),
        "shorts_share_percent": round(
            sum(1 for r in rows if r["isShort"]) / len(rows) * 100, 1),
        "channel_size_distribution": buckets,
        "saturation_hint": (
            "dominated by big channels -- hard to break in"
            if buckets["200k+"] > len(channels) * 0.4 else
            "plenty of small channels performing -- room to enter"
            if (buckets["<1k"] + buckets["1k-10k"]) > len(channels) * 0.4 else
            "mixed field"),
        "top_categories": [{"categoryId": c, "category": C.title_for(c), "videos": n}
                           for c, n in top_cats],
        "small_channel_breakouts": len(small_breakouts),
        "synthetic_content_share_percent": (
            round(sum(1 for k in synthetic_known if k) / len(synthetic_known) * 100, 1)
            if synthetic_known else None),
        "synthetic_disclosure_coverage_percent": round(len(synthetic_known) / len(rows) * 100, 1),
        "top_videos_by_outlier_score": [
            {"videoId": r["video_id"], "title": r["title"], "views": r["view_count"],
             "channel": r["channel_title"], "subscribers": r["subs"],
             "outlierScore": r["outlierScore"],
             "viewsPerSubscriber": r["viewsPerSubscriber"]}
            for r in sorted(rows, key=lambda r: r["outlierScore"] or 0, reverse=True)[:5]
        ],
    }


def niche_overview(niche: str, period: str = "all") -> dict:
    conn = db.get_conn()
    row_niche = conn.execute("SELECT * FROM niches WHERE slug = ?", (niche,)).fetchone()
    conn.close()
    rows = trends.load_window(period=period, niche=niche)
    if not rows:
        return {"niche": niche, "found": False,
                "hint": "nothing collected under this slug yet -- run collect_niche"}

    overview = _overview_from_rows(rows)
    overview.update({
        "niche": niche,
        "found": True,
        "period": period,
        "query": row_niche["query"] if row_niche else None,
        "last_collected_at": row_niche["last_collected_at"] if row_niche else None,
    })
    return overview


def niche_overview_from_channel(channel_id: str, limit: int = 15,
                                min_videos_embedded: int = 1,
                                period: str = "all") -> dict:
    """Channel-anchored niche read, NexLev's get_niche_overview(channelId)
    workflow: find this channel's closest peers via similar_channels
    (embedding centroid, FREE/local) and run the same saturation/opportunity
    analysis niche_overview does over the channel + its peers -- no
    pre-collected niche slug required.
    """
    sim = similar_channels(channel_id, limit=limit, min_videos_embedded=min_videos_embedded)
    if not sim["similar"]:
        return {"channel_id": channel_id, "found": False,
                "hint": sim.get("hint", "no similar channels found in the local corpus yet")}

    channel_ids = [channel_id] + [s["channelId"] for s in sim["similar"]]
    rows = trends.load_window(period=period, channel_ids=channel_ids)
    if not rows:
        return {"channel_id": channel_id, "found": False,
                "hint": "similar channels found, but no videos in the requested period"}

    overview = _overview_from_rows(rows)
    overview.update({
        "channel_id": channel_id,
        "found": True,
        "period": period,
        "peer_channel_count": len(sim["similar"]),
        "peers": sim["similar"],
    })
    return overview


def list_niches() -> list:
    conn = db.get_conn()
    rows = conn.execute(
        """
        SELECT n.*, COUNT(DISTINCT vn.video_id) AS video_count
        FROM niches n LEFT JOIN video_niches vn ON vn.niche_slug = n.slug
        GROUP BY n.slug ORDER BY n.last_collected_at DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_stats() -> dict:
    conn = db.get_conn()
    def one(sql):
        return conn.execute(sql).fetchone()[0]
    calls_today = collecting.search_calls_today(conn)
    blocked_until = db.get_meta(conn, "worker_quota_blocked_until")
    out = {
        "channels": one("SELECT COUNT(*) FROM channels"),
        "videos": one("SELECT COUNT(*) FROM videos"),
        "niches": one("SELECT COUNT(*) FROM niches"),
        "tracked_channels": one("SELECT COUNT(*) FROM tracked_channels WHERE active=1"),
        "video_stat_snapshots": one("SELECT COUNT(*) FROM video_stats_history"),
        "channel_stat_snapshots": one("SELECT COUNT(*) FROM channel_stats_history"),
        "chart_snapshots": one("SELECT COUNT(*) FROM chart_snapshots"),
        "title_thumbnail_changes": one("SELECT COUNT(*) FROM video_changes"),
        "videos_without_embedding": one("SELECT COUNT(*) FROM videos WHERE embedding IS NULL"),
        "oldest_video": one("SELECT MIN(published_at) FROM videos"),
        "newest_video": one("SELECT MAX(published_at) FROM videos"),
        "history_since": one("SELECT MIN(captured_at) FROM video_stats_history"),
        "db_path": db.display_dsn(),
        "search_quota": {
            "search_calls_today": calls_today,
            "search_calls_left_today": max(0, yt.SEARCH_DAILY_CALL_LIMIT - calls_today),
            "resets_at": "midnight Pacific Time",
        },
        "worker_quota_blocked_until": blocked_until,
    }
    conn.close()
    return out


def similar_videos(video_id: str, niche: str = None, limit: int = 10,
                   exclude_same_channel: bool = False) -> dict:
    """Videos whose title+description embedding reads closest to this one, in
    our own corpus (NexLev's "Similar Videos"). Local and free -- zero YouTube
    quota -- but only as good as what we've actually embedded: a video needs
    its own embedding (collect_channel/track_channel default to embed=False;
    backfill_embeddings() fills the gap for free) to be a *candidate*, but the
    target video needs one too, or there's nothing to compare against.
    """
    import infrastructure.embeddings.fastembed_provider as emb

    conn = db.get_conn()
    row = conn.execute(
        "SELECT embedding, channel_id FROM videos WHERE video_id=?", (video_id,)).fetchone()
    if not row or not row["embedding"]:
        conn.close()
        return {
            "video_id": video_id, "similar": [],
            "hint": ("this video has no embedding yet -- collect_channel/"
                     "track_channel default to embed=False; run backfill_embeddings, "
                     "or re-collect with embed=True, to make it eligible"),
        }
    target = emb.from_blob(row["embedding"])
    own_channel = row["channel_id"]

    if niche:
        sql = ("SELECT v.video_id, v.embedding, v.channel_id, v.title, v.view_count, "
               "v.published_at FROM videos v "
               "JOIN video_niches vn ON vn.video_id = v.video_id "
               "WHERE vn.niche_slug=? AND v.embedding IS NOT NULL AND v.video_id != ?")
        params = (niche, video_id)
    else:
        sql = ("SELECT video_id, embedding, channel_id, title, view_count, published_at "
               "FROM videos WHERE embedding IS NOT NULL AND video_id != ?")
        params = (video_id,)
    rows = conn.execute(sql, params).fetchall()

    scored = []
    for r in rows:
        if exclude_same_channel and r["channel_id"] == own_channel:
            continue
        vec = emb.from_blob(r["embedding"])
        scored.append((r, emb.cosine(target, vec)))
    scored.sort(key=lambda t: t[1], reverse=True)

    out = []
    channel_cache = {}
    for r, score in scored[:limit]:
        cid = r["channel_id"]
        if cid not in channel_cache:
            ch = conn.execute(
                "SELECT title, subscriber_count FROM channels WHERE channel_id=?",
                (cid,)).fetchone()
            channel_cache[cid] = dict(ch) if ch else {}
        ch = channel_cache[cid]
        out.append({
            "videoId": r["video_id"], "title": r["title"], "views": r["view_count"],
            "publishedAt": r["published_at"], "channelId": cid,
            "channelTitle": ch.get("title"), "channelSubscribers": ch.get("subscriber_count"),
            "sameChannel": cid == own_channel,
            "similarity": round(score, 4),
        })
    conn.close()
    return {"video_id": video_id, "candidatesConsidered": len(rows), "similar": out}


def similar_channels(channel_id: str, niche: str = None, limit: int = 10,
                     min_videos_embedded: int = 1) -> dict:
    """Channels whose collected videos read as semantically closest to this
    one, via the same title+description embeddings search_outliers uses.

    Each channel's vector is the mean of its stored per-video embeddings, so
    it only works for channels that actually have embedded videos.
    collect_channel/track_channel default to embed=False (cheap collection);
    pass embed=True there, or use collect_niche, to make a channel eligible.
    niche optionally limits the comparison pool to one collected niche
    instead of the whole corpus.
    """
    import infrastructure.embeddings.fastembed_provider as emb

    conn = db.get_conn()
    target_rows = conn.execute(
        "SELECT embedding FROM videos WHERE channel_id=? AND embedding IS NOT NULL",
        (channel_id,)).fetchall()
    if not target_rows:
        conn.close()
        return {
            "channel_id": channel_id, "similar": [],
            "hint": ("no embedded videos for this channel -- collect_channel/"
                     "track_channel default to embed=False; re-run with "
                     "embed=True, or via collect_niche, to populate embeddings"),
        }
    target = sum(emb.from_blob(r["embedding"]) for r in target_rows) / len(target_rows)

    if niche:
        sql = ("SELECT v.channel_id, v.embedding FROM videos v "
               "JOIN video_niches vn ON vn.video_id = v.video_id "
               "WHERE vn.niche_slug=? AND v.embedding IS NOT NULL AND v.channel_id != ?")
        params = (niche, channel_id)
    else:
        sql = ("SELECT channel_id, embedding FROM videos "
               "WHERE embedding IS NOT NULL AND channel_id != ?")
        params = (channel_id,)
    rows = conn.execute(sql, params).fetchall()

    by_channel = {}
    for r in rows:
        by_channel.setdefault(r["channel_id"], []).append(emb.from_blob(r["embedding"]))

    scored = []
    for cid, vecs in by_channel.items():
        if len(vecs) < min_videos_embedded:
            continue
        centroid = sum(vecs) / len(vecs)
        scored.append((cid, emb.cosine(target, centroid), len(vecs)))
    scored.sort(key=lambda t: t[1], reverse=True)

    out = []
    for cid, score, n in scored[:limit]:
        ch = conn.execute(
            "SELECT title, subscriber_count, thumbnail FROM channels WHERE channel_id=?",
            (cid,)).fetchone()
        out.append({
            "channelId": cid, "title": ch["title"] if ch else None,
            "subscriberCount": ch["subscriber_count"] if ch else None,
            "thumbnail": ch["thumbnail"] if ch else None,
            "similarity": round(score, 4), "videosEmbedded": n,
        })
    conn.close()
    return {"channel_id": channel_id, "videosEmbedded": len(target_rows),
            "candidatesConsidered": len(by_channel), "similar": out}

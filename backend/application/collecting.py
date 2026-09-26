"""Everything that spends YouTube API quota lives here.

Quota strategy after the 1 June 2026 granular-quota change:
  * search.list is capped at 100 CALLS/DAY in its own bucket -- treat it as the
    scarce resource and use it only for genuinely new topic discovery.
  * channels.list -> uploads playlist -> playlistItems.list costs 1 unit per 50
    videos out of the roomy 10,000-unit pool, so ALL channel enumeration and all
    stats refreshing goes through that path instead.
  * videos.batchGetStats (June 2026) refreshes counters at 1 unit per call.

Every collector returns a `quota` dict so the caller can see what it spent.
"""
import json
import re

import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from domain import metrics as M
from domain import periods as P

try:
    from langdetect import LangDetectException
    from langdetect import detect as _detect_lang
except Exception:  # pragma: no cover
    _detect_lang = None
    LangDetectException = Exception

_EMB = None


def _embeddings():
    """Import fastembed lazily -- it pulls torch-free ONNX runtime but still
    costs a second or two and downloads a model on first use."""
    global _EMB
    if _EMB is None:
        import infrastructure.embeddings.fastembed_provider as emb
        _EMB = emb
    return _EMB


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w]+", "-", (text or "").strip().lower(), flags=re.UNICODE)
    return slug.strip("-")[:64] or "niche"


def detect_language(title: str, description: str) -> str:
    if not _detect_lang:
        return ""
    try:
        return _detect_lang(f"{title} {description or ''}"[:200])
    except LangDetectException:
        return ""


def _topics(resource) -> str:
    """topicDetails.topicCategories is a list of Wikipedia URLs -- a free,
    YouTube-assigned semantic label (topicIds has been dead since 2016)."""
    cats = (resource.get("topicDetails") or {}).get("topicCategories") or []
    names = [c.rstrip("/").rsplit("/", 1)[-1].replace("_", " ") for c in cats]
    return json.dumps(names, ensure_ascii=False) if names else None


def _thumb(snippet) -> str:
    t = snippet.get("thumbnails", {})
    for size in ("maxres", "standard", "high", "medium", "default"):
        if t.get(size, {}).get("url"):
            return t[size]["url"]
    return None


# ------------------------------------------------------------------ storing

def store_channels(conn, channel_items, now=None) -> dict:
    now = now or db.now_iso()
    stored = {}
    for c in channel_items:
        sn = c.get("snippet", {}) or {}
        stt = c.get("statistics", {}) or {}
        cd = c.get("contentDetails", {}) or {}
        bs = (c.get("brandingSettings") or {}).get("channel", {}) or {}
        hidden = bool(stt.get("hiddenSubscriberCount"))
        row = {
            "channel_id": c["id"],
            "title": sn.get("title"),
            "custom_url": sn.get("customUrl"),
            "country": sn.get("country") or bs.get("country"),
            "description": (sn.get("description") or "")[:2000],
            "default_language": sn.get("defaultLanguage") or bs.get("defaultLanguage"),
            "subscriber_count": None if hidden else int(stt.get("subscriberCount", 0) or 0),
            "video_count": int(stt.get("videoCount", 0) or 0),
            "view_count": int(stt.get("viewCount", 0) or 0),
            "thumbnail": _thumb(sn),
            "updated_at": now,
            "published_at": sn.get("publishedAt"),
            "topic_categories": _topics(c),
            "keywords": bs.get("keywords"),
            "uploads_playlist": (cd.get("relatedPlaylists") or {}).get("uploads"),
            "hidden_subs": 1 if hidden else 0,
        }
        db.upsert_channel(conn, row)
        db.record_channel_stats(conn, c["id"], row["subscriber_count"],
                                row["video_count"], row["view_count"], now)
        stored[c["id"]] = row
    return stored


def store_videos(conn, video_items, niche_slug=None, region=None, embed=True,
                 now=None) -> int:
    now = now or db.now_iso()
    emb = _embeddings() if embed else None
    count = 0
    for v in video_items:
        sn = v.get("snippet", {}) or {}
        stt = v.get("statistics", {}) or {}
        cd = v.get("contentDetails", {}) or {}
        status = v.get("status", {}) or {}
        title = sn.get("title", "") or ""
        desc = sn.get("description", "") or ""
        duration = yt.parse_duration(cd.get("duration"))
        lang = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage")
                or detect_language(title, desc))
        vec = None
        if emb is not None:
            vec = emb.to_blob(emb.embed(f"{title}\n{desc[:500]}"))
        # likeCount is absent (not zero) when the creator hides likes
        likes = int(stt["likeCount"]) if "likeCount" in stt else None
        comments = int(stt["commentCount"]) if "commentCount" in stt else None
        views = int(stt.get("viewCount", 0) or 0)
        thumb = _thumb(sn)
        row = {
            "video_id": v["id"],
            "channel_id": sn.get("channelId"),
            "title": title,
            "description": desc[:1000],
            "published_at": sn.get("publishedAt") or (v.get("snippet") or {}).get("publishTime"),
            "duration_seconds": duration,
            "view_count": views,
            "like_count": likes,
            "comment_count": comments,
            "thumbnail": thumb,
            "tags": json.dumps(sn.get("tags", []), ensure_ascii=False),
            "default_language": lang,
            "embedding": vec,
            "updated_at": now,
            "category_id": sn.get("categoryId"),
            "region": region,
            "is_short": 1 if M.is_short(duration) else 0,
            "topic_categories": _topics(v),
            "live_content": sn.get("liveBroadcastContent"),
            # official AI-disclosure flag (added 30 Oct 2024). The creator sets
            # this voluntarily -- it measures DISCLOSED synthetic content, not
            # actual AI use, and status is missing entirely for very old videos.
            "contains_synthetic_media": (
                1 if status.get("containsSyntheticMedia") else
                (0 if "containsSyntheticMedia" in status else None)
            ),
        }
        db.upsert_video(conn, row)
        db.record_video_stats(conn, v["id"], views, likes, comments, title, thumb, now)
        if niche_slug:
            db.link_video_niche(conn, v["id"], niche_slug)
        count += 1
    return count


# ------------------------------------------------------------ collect_niche

def collect_niche(api_key: str, query: str, label: str = None, language: str = None,
                  min_upload_date: str = None, pages: int = 1,
                  order: str = "viewCount", video_duration: str = None,
                  region: str = None, category_id: str = None,
                  period: str = None, embed: bool = True) -> dict:
    """Discover videos for a topic and store them under a niche slug.

    `period` is a convenience alias for min_upload_date: period="24h" collects
    only what was published in the last 24 hours, which is what you want when
    feeding the trending sections.

    Cost: `pages` search calls out of the 100/day search budget, plus ~1 unit
    per 50 videos and per 50 channels from the shared pool.

    The niche row and every search call already spent are committed as they
    happen (not only at the very end), so a mid-loop failure -- most commonly
    `QuotaExceeded` once the 100/day search.list bucket runs out -- never makes
    it look like "nothing was added": the niche still exists, just empty or
    partially filled, ready to resume once the budget resets.
    """
    if period and not min_upload_date:
        c = P.cutoff(period)
        if c:
            min_upload_date = P.to_rfc3339(c)

    slug = slugify(label or query)
    conn = db.get_conn()
    try:
        db.upsert_niche(conn, slug, query, label or query)
        conn.commit()

        calls_today = search_calls_today(conn)
        if calls_today >= yt.SEARCH_DAILY_CALL_LIMIT:
            raise yt.QuotaExceeded(
                f"Дневной лимит поиска исчерпан ({calls_today}/{yt.SEARCH_DAILY_CALL_LIMIT}). "
                "search.list сбрасывается в полночь по Тихоокеанскому времени. "
                f"Ниша '{slug}' уже создана (пока пустая) -- запустите тот же поиск "
                "позже, или используйте collect_channel/collect_trending, которые "
                "не тратят эту квоту."
            )

        video_ids, token = [], None
        search_calls = 0
        for _ in range(max(1, pages)):
            resp = yt.search_videos(
                api_key, query, published_after=min_upload_date,
                relevance_language=language, region_code=region,
                video_category_id=category_id, order=order, page_token=token,
                video_duration=video_duration,
            )
            search_calls += 1
            calls_today = _record_search_calls(conn, 1)
            conn.commit()
            for item in resp.get("items", []):
                vid = (item.get("id") or {}).get("videoId")
                if vid:
                    video_ids.append(vid)
            token = resp.get("nextPageToken")
            if not token:
                break

        video_ids = list(dict.fromkeys(video_ids))
        if not video_ids:
            return {"niche": slug, "query": query, "videos_found": 0,
                    "quota": _quota(search_calls, 0, 0, calls_today=calls_today),
                    "min_upload_date": min_upload_date}

        video_items = yt.videos_list(api_key, video_ids)
        channel_ids = list({(v.get("snippet") or {}).get("channelId")
                            for v in video_items if v.get("snippet")})
        channel_items = yt.channels_list(api_key, [c for c in channel_ids if c])

        now = db.now_iso()
        store_channels(conn, channel_items, now)
        stored = store_videos(conn, video_items, slug, region, embed, now)
        conn.commit()
        return {
            "niche": slug, "query": query, "min_upload_date": min_upload_date,
            "videos_found": len(video_ids), "videos_stored": stored,
            "channels_stored": len(channel_items),
            "quota": _quota(search_calls, len(video_ids), len(channel_ids), calls_today=calls_today),
        }
    finally:
        conn.close()


def _record_search_calls(conn, n: int) -> int:
    """Persist search.list calls against today's Pacific-Time quota day (that is
    when Google actually resets the 100-calls/day bucket) and return the new
    running total for the day."""
    if n <= 0:
        return search_calls_today(conn)
    key = f"search_calls_{P.pacific_date_key()}"
    total = int(db.get_meta(conn, key) or 0) + n
    db.set_meta(conn, key, total)
    return total


def search_calls_today(conn) -> int:
    """How many search.list calls have already been spent today (Pacific Time)."""
    key = f"search_calls_{P.pacific_date_key()}"
    return int(db.get_meta(conn, key) or 0)


def units_today(conn) -> int:
    """Units spent today (Pacific Time) from the shared 10,000/day pool, as
    counted by the YouTube client on every request it sends."""
    return int(db.get_meta(conn, yt.units_meta_key()) or 0)


def _quota(search_calls, n_videos, n_channels, playlist_calls=0, other=0, calls_today=None):
    units = (n_videos + 49) // 50 + (n_channels + 49) // 50 + playlist_calls + other
    calls_today = search_calls if calls_today is None else calls_today
    return {
        "search_calls": search_calls,
        "search_calls_today": calls_today,
        "search_calls_left_today": max(0, yt.SEARCH_DAILY_CALL_LIMIT - calls_today),
        "units_from_shared_pool": units + search_calls,
        "note": "search.list has its own 100-calls/day bucket since 1 June 2026, "
                "resetting at midnight Pacific Time; everything else shares "
                "10,000 units/day",
    }


# --------------------------------------------------------- collect_trending

def collect_trending(api_key: str, regions=("US",), category_ids=(None,),
                     pages: int = 2, embed: bool = False) -> dict:
    """Snapshot YouTube's own mostPopular chart -- 1 unit per page, very cheap.

    Reality check: since 21 July 2025 this chart carries Trending Music, Movies
    and Gaming only, so it is a strong signal for those verticals and close to
    useless elsewhere. It is still worth snapshotting because the ranks over
    time give a real, YouTube-blessed velocity signal for those categories.
    """
    conn = db.get_conn()
    total_videos, snapshots, units = 0, [], 0
    for region in regions:
        for cid in category_ids:
            try:
                items = yt.most_popular(api_key, region_code=region,
                                        video_category_id=cid, pages=pages)
            except RuntimeError as e:
                snapshots.append({"region": region, "categoryId": cid, "error": str(e)[:200]})
                continue
            units += pages
            if not items:
                snapshots.append({"region": region, "categoryId": cid, "videos": 0,
                                  "note": "empty -- category is not one of the "
                                          "surviving Music/Movies/Gaming charts"})
                continue
            snap_id = db.new_chart_snapshot(conn, region, str(cid) if cid else "0",
                                            "mostPopular")
            now = db.now_iso()
            channel_ids = list({(v.get("snippet") or {}).get("channelId") for v in items})
            store_channels(conn, yt.channels_list(api_key, [c for c in channel_ids if c]), now)
            units += (len(channel_ids) + 49) // 50
            store_videos(conn, items, niche_slug=f"trending-{region.lower()}",
                         region=region, embed=embed, now=now)
            db.upsert_niche(conn, f"trending-{region.lower()}",
                            f"mostPopular chart {region}", f"Trending {region}")
            for rank, v in enumerate(items, 1):
                db.link_video_niche(conn, v["id"], f"trending-{region.lower()}")
                db.add_chart_entry(conn, snap_id, v["id"], rank,
                                   int((v.get("statistics") or {}).get("viewCount", 0) or 0))
            total_videos += len(items)
            snapshots.append({"region": region, "categoryId": cid, "videos": len(items),
                              "snapshotId": snap_id})
    conn.commit()
    conn.close()
    return {"snapshots": snapshots, "videos_stored": total_videos,
            "quota": {"units_from_shared_pool": units, "search_calls": 0}}


# ---------------------------------------------------------- collect_channel

CHANNEL_ID_RE = re.compile(r"(UC[\w-]{22})")
HANDLE_RE = re.compile(r"@([\w.\-]+)")


def resolve_channel(api_key: str, ref: str):
    """Accept a raw UC id, an @handle, or any youtube.com channel URL."""
    ref = (ref or "").strip()
    m = CHANNEL_ID_RE.search(ref)
    if m:
        items = yt.channels_list(api_key, [m.group(1)])
        return items[0] if items else None
    m = HANDLE_RE.search(ref)
    if m:
        return yt.channel_by_handle(api_key, m.group(1))
    if ref and not ref.startswith("http"):
        return yt.channel_by_handle(api_key, ref)
    return None


def collect_channel(api_key: str, channel_ref: str, max_videos: int = 100,
                    embed: bool = False, niche: str = None) -> dict:
    """Pull a channel's recent uploads through the uploads playlist.

    1 unit per 50 videos and no 500-result cap, versus burning the daily search
    budget for a worse, capped result.
    """
    ch = resolve_channel(api_key, channel_ref)
    if not ch:
        return {"error": f"channel not found: {channel_ref}"}
    playlist = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if not playlist:
        return {"error": "channel has no uploads playlist", "channelId": ch["id"]}

    entries, playlist_calls = yt.playlist_items(api_key, playlist, max_items=max_videos)
    ids = [e["video_id"] for e in entries]
    items = yt.videos_list(api_key, ids) if ids else []

    niche_slug = slugify(niche) if niche else None

    conn = db.get_conn()
    now = db.now_iso()
    store_channels(conn, [ch], now)
    if niche_slug:
        # Without this, video_niches gets rows under niche_slug but no
        # matching niches row -- the Niches screen and list_niches never
        # see the niche (#12 bug 2).
        db.upsert_niche(conn, niche_slug, niche, niche)
    stored = store_videos(conn, items, niche_slug=niche_slug, embed=embed, now=now)
    conn.commit()
    conn.close()
    return {
        "channelId": ch["id"],
        "channelTitle": (ch.get("snippet") or {}).get("title"),
        "videos_found": len(ids), "videos_stored": stored,
        "quota": _quota(0, len(ids), 1, playlist_calls=playlist_calls),
    }


# --------------------------------------------- discover_new_videos_via_rss

def discover_new_videos_via_rss(api_key: str, channel_ids=None, max_new_per_channel: int = 15,
                                max_total: int = 300, embed: bool = False) -> dict:
    """Cheapest possible way to notice a tracked channel's new upload.

    YouTube's per-channel RSS feed (rss.fetch_channel_feed) costs no API key
    and no quota at all -- it just doesn't carry view counts, so this is a
    *novelty detector*, not a stats refresh. For every channel we already
    know, diff the feed's video ids against what's stored; whatever is new
    gets a single videos.list call (1 unit per 50), same as any other path.

    Channels that published more than the feed's ~15-entry window since the
    last check will have some new videos missed here -- that's an accepted
    trade-off; collect_channel's full playlist walk remains the way to
    backfill a channel from scratch or catch up after a long gap.
    """
    import infrastructure.youtube.rss as rss

    conn = db.get_conn()
    if channel_ids:
        ids = list(dict.fromkeys(channel_ids))
    else:
        ids = [r["channel_id"] for r in conn.execute(
            "SELECT channel_id FROM tracked_channels WHERE active=1").fetchall()]

    channels_checked, channels_failed = 0, 0
    all_new_ids = []
    per_channel_new = {}
    for channel_id in ids:
        known = {r["video_id"] for r in conn.execute(
            "SELECT video_id FROM videos WHERE channel_id=?", (channel_id,)).fetchall()}
        try:
            fresh = rss.new_video_ids(channel_id, known)
        except Exception:
            channels_failed += 1
            continue
        channels_checked += 1
        fresh = fresh[:max_new_per_channel]
        if fresh:
            per_channel_new[channel_id] = fresh
            all_new_ids.extend(fresh)
        if len(all_new_ids) >= max_total:
            break
    conn.close()

    all_new_ids = all_new_ids[:max_total]
    if not all_new_ids:
        return {"channels_checked": channels_checked, "channels_failed": channels_failed,
                "new_videos_found": 0, "videos_stored": 0,
                "quota": {"units_from_shared_pool": 0, "search_calls": 0}}

    items = yt.videos_list(api_key, all_new_ids)
    conn = db.get_conn()
    now = db.now_iso()
    stored = store_videos(conn, items, embed=embed, now=now)
    conn.commit()
    conn.close()
    return {
        "channels_checked": channels_checked, "channels_failed": channels_failed,
        "channels_with_new_videos": len(per_channel_new),
        "new_videos_found": len(all_new_ids), "videos_stored": stored,
        "quota": {"units_from_shared_pool": (len(all_new_ids) + 49) // 50, "search_calls": 0},
        "note": "RSS itself is free; the units above are only the videos.list "
                "call for the newly discovered ids",
    }


# ------------------------------------------------------------ refresh_stats

def refresh_stats(api_key: str, scope: str = "recent", period: str = "30d",
                  limit: int = 1000, niche: str = None) -> dict:
    """Append a fresh stats snapshot for stored videos -- this is what makes
    vph24h, viewsGained24h, acceleration and title-change detection possible.

    scope: "recent" (published within `period`), "tracked" (uploads of tracked
    channels), "niche", or "all".
    """
    conn = db.get_conn()
    params, sql = [], "SELECT v.video_id FROM videos v"
    if scope == "tracked":
        sql += " JOIN tracked_channels t ON t.channel_id = v.channel_id AND t.active = 1"
    elif scope == "niche" and niche:
        sql += " JOIN video_niches vn ON vn.video_id = v.video_id AND vn.niche_slug = ?"
        params.append(niche)
    if scope in ("recent", "tracked", "niche"):
        start, _ = P.window(period)
        if start:
            sql += " WHERE v.published_at >= ?" if "WHERE" not in sql else " AND v.published_at >= ?"
            params.append(start)
    sql += " ORDER BY v.published_at DESC LIMIT ?"
    params.append(limit)
    ids = [r["video_id"] for r in conn.execute(sql, params).fetchall()]
    conn.close()

    if not ids:
        return {"refreshed": 0, "scope": scope, "quota": {"units_from_shared_pool": 0}}

    items, failed = yt.videos_batch_get_stats(api_key, ids)
    conn = db.get_conn()
    now = db.now_iso()
    updated, changes = 0, 0
    for it in items:
        stt = it.get("statistics", {}) or {}
        sn = it.get("snippet", {}) or {}
        views = int(stt.get("viewCount", 0) or 0)
        likes = int(stt["likeCount"]) if "likeCount" in stt else None
        comments = int(stt["commentCount"]) if "commentCount" in stt else None
        title = sn.get("title")
        thumb = _thumb(sn) if sn.get("thumbnails") else None
        before = conn.execute(
            "SELECT COUNT(*) FROM video_changes WHERE video_id=?", (it["id"],)
        ).fetchone()[0]
        db.record_video_stats(conn, it["id"], views, likes, comments, title, thumb, now)
        conn.execute(
            "UPDATE videos SET view_count=?, like_count=COALESCE(?, like_count), "
            "comment_count=COALESCE(?, comment_count), title=COALESCE(?, title), "
            "updated_at=? WHERE video_id=?",
            (views, likes, comments, title, now, it["id"]),
        )
        after = conn.execute(
            "SELECT COUNT(*) FROM video_changes WHERE video_id=?", (it["id"],)
        ).fetchone()[0]
        changes += after - before
        updated += 1
    conn.commit()
    conn.close()
    return {
        "scope": scope, "period": period, "requested": len(ids), "refreshed": updated,
        "failed": len(failed), "title_or_thumbnail_changes_detected": changes,
        "quota": {"units_from_shared_pool": (len(ids) + 49) // 50, "search_calls": 0},
    }


def refresh_channels(api_key: str, channel_ids=None, only_tracked=True) -> dict:
    """Snapshot channel-level stats (subs / views / video count) for growth
    tracking. 1 unit per 50 channels."""
    conn = db.get_conn()
    if channel_ids:
        ids = list(channel_ids)
    elif only_tracked:
        ids = [r["channel_id"] for r in conn.execute(
            "SELECT channel_id FROM tracked_channels WHERE active=1").fetchall()]
    else:
        ids = [r["channel_id"] for r in conn.execute(
            "SELECT channel_id FROM channels").fetchall()]
    conn.close()
    if not ids:
        return {"refreshed": 0, "quota": {"units_from_shared_pool": 0}}

    items = yt.channels_list(api_key, ids)
    conn = db.get_conn()
    now = db.now_iso()
    store_channels(conn, items, now)
    conn.execute(
        "UPDATE tracked_channels SET last_refreshed_at=? WHERE active=1", (now,))
    conn.commit()
    conn.close()
    return {"refreshed": len(items),
            "quota": {"units_from_shared_pool": (len(ids) + 49) // 50, "search_calls": 0}}


# --------------------------------------------------------- video_comments

def video_comments(api_key: str, video_id: str, max_results: int = 100,
                   order: str = "relevance", search_terms: str = None) -> dict:
    """Top-level comments for one video, live from the API. 1 unit, not stored
    locally -- a competitive signal (complaints, requests, reactions) that
    nothing else in this server surfaces. Returns raw text for the calling
    model to read; the server does not run any sentiment/classification
    itself, same as everywhere else in this project."""
    items = yt.comment_threads(api_key, video_id, max_results=max_results,
                               order=order, search_terms=search_terms)
    comments = []
    for it in items:
        top = ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        comments.append({
            "author": top.get("authorDisplayName"),
            "text": top.get("textDisplay"),
            "likeCount": top.get("likeCount"),
            "publishedAt": top.get("publishedAt"),
            "replyCount": (it.get("snippet") or {}).get("totalReplyCount"),
        })
    return {
        "video_id": video_id, "count": len(comments), "comments": comments,
        "quota": {"units_from_shared_pool": 1, "search_calls": 0},
    }


# --------------------------------------------------- backfill_embeddings

def backfill_embeddings(limit: int = 1000, batch_size: int = 256) -> dict:
    """Compute embeddings for already-collected videos that don't have one yet.

    collect_channel/track_channel default to embed=False (cheap collection),
    so most of the corpus built that way has embedding IS NULL -- this fills
    it in from title+description already in Postgres. No YouTube quota spent:
    it's pure local compute, same text and model as store_videos() uses.
    """
    emb = _embeddings()
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT video_id, title, description FROM videos "
        "WHERE embedding IS NULL ORDER BY published_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    embedded = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        texts = [f"{r['title'] or ''}\n{(r['description'] or '')[:500]}" for r in chunk]
        vecs = emb.embed(texts)
        for r, vec in zip(chunk, vecs):
            blob = emb.to_blob(vec)
            conn.execute("UPDATE videos SET embedding=? WHERE video_id=?",
                        (blob, r["video_id"]))
            db.sync_embedding_v(conn, r["video_id"], blob)
            embedded += 1
    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM videos WHERE embedding IS NULL").fetchone()[0]
    conn.close()
    return {"scanned": len(rows), "embedded": embedded, "remaining_without_embedding": remaining}

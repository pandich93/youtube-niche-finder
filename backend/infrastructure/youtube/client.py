"""Wrapper around the YouTube Data API v3.

QUOTA MODEL AS OF 1 JUNE 2026 (this changed and most blog posts are stale):
  * search.list      -- 1 unit, but its OWN bucket capped at 100 CALLS/DAY.
                        This is now the scarcest resource in the whole system.
  * videos.insert    -- own bucket, 100 calls/day (unused here).
  * everything else  -- 1 unit each out of a shared 10,000 units/day pool.
  * videos.batchGetStats (added 3 June 2026) -- 1 unit, cheapest way to refresh
                        view/like/comment counts in bulk.

Consequence, and the reason this module has playlist_items(): enumerating a
channel through channels.list(contentDetails) -> uploads playlist ->
playlistItems.list costs 1 unit per 50 videos and is unlimited, whereas
search.list(channelId=...) burns the daily 100-call discovery budget and is
capped at ~500 results. Never use search for something a playlist can answer.

ALSO NOTE: since 21 July 2025 chart=mostPopular no longer mirrors the retired
"Trending Now" tab -- it returns the Trending Music / Movies / Gaming charts
only. General-interest trends must be computed from your own corpus.
"""
import os
import re
import time

import requests

from domain import periods as P

BASE = "https://www.googleapis.com/youtube/v3"

ISO8601_DURATION_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)

# quota units per call, for the accounting returned by the collectors
COST = {
    "search": 1, "videos": 1, "channels": 1, "playlistItems": 1,
    "videoCategories": 1, "commentThreads": 1, "videos:batchGetStats": 1,
}
SEARCH_DAILY_CALL_LIMIT = 100
DAILY_UNIT_LIMIT = int(os.environ.get("YOUTUBE_DAILY_UNIT_LIMIT") or 10000)


def units_meta_key() -> str:
    """meta key of today's shared-pool counter -- per Pacific-Time day, when
    Google resets the 10,000 units."""
    return f"yt_units_{P.pacific_date_key()}"


def _record_units(path: str):
    """Add one sent request's cost to today's counter. Google charges every
    request, failed ones included, so this runs after each requests.get.
    Accounting must never break collection: any DB error is swallowed."""
    try:
        # resolved at call time, so tests that swap infrastructure.postgres
        # for a double in sys.modules are honoured
        import infrastructure.postgres as db
        conn = db.get_conn()
        try:
            db.incr_meta(conn, units_meta_key(), COST.get(path, 1))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


class QuotaExceeded(RuntimeError):
    pass


def parse_duration(duration: str) -> int:
    """ISO 8601 duration (PT12M34S, P1DT2H) -> seconds."""
    if not duration:
        return 0
    m = ISO8601_DURATION_RE.match(duration)
    if not m:
        return 0
    p = {k: int(v) if v else 0 for k, v in m.groupdict().items()}
    return p["days"] * 86400 + p["hours"] * 3600 + p["minutes"] * 60 + p["seconds"]


def _get(path: str, api_key: str, retries: int = 3, **params):
    params = {k: v for k, v in params.items() if v is not None}
    params["key"] = api_key
    last = None
    for attempt in range(retries):
        resp = requests.get(f"{BASE}/{path}", params=params, timeout=30)
        _record_units(path)
        if resp.status_code == 200:
            return resp.json()
        last = resp
        if resp.status_code == 403 and "quota" in resp.text.lower():
            raise QuotaExceeded(f"YouTube API quota exceeded: {resp.text[:300]}")
        if resp.status_code in (429, 500, 503):
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    raise RuntimeError(f"YouTube API error {last.status_code}: {last.text[:500]}")


def _batched(ids, size=50):
    ids = list(ids)
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


# ------------------------------------------------------------------ search

def search_videos(api_key: str, query: str, published_after: str = None,
                  published_before: str = None, relevance_language: str = None,
                  region_code: str = None, video_category_id: str = None,
                  order: str = "viewCount", max_results: int = 50,
                  page_token: str = None, video_duration: str = None,
                  channel_id: str = None):
    """search.list -- 1 unit but only 100 CALLS/DAY. Use sparingly.

    Results are capped at roughly 500 per query no matter how you page; slice by
    published_after/published_before to go deeper.
    """
    return _get(
        "search", api_key,
        part="snippet", q=query, type="video", order=order,
        maxResults=min(max_results, 50),
        publishedAfter=published_after, publishedBefore=published_before,
        relevanceLanguage=relevance_language, regionCode=region_code,
        videoCategoryId=video_category_id, pageToken=page_token,
        videoDuration=video_duration, channelId=channel_id,
    )


# ------------------------------------------------------------------ videos

VIDEO_PARTS = "snippet,statistics,contentDetails,topicDetails,status"


def videos_list(api_key: str, video_ids: list, parts: str = VIDEO_PARTS):
    """videos.list -- 1 unit per call, up to 50 ids per call."""
    items = []
    for batch in _batched(video_ids):
        data = _get("videos", api_key, part=parts, id=",".join(batch))
        items.extend(data.get("items", []))
    return items


def videos_batch_get_stats(api_key: str, video_ids: list,
                           parts: str = "id,snippet,statistics,contentDetails"):
    """videos.batchGetStats (June 2026) -- 1 unit, purpose-built for refreshing
    counters in bulk. Falls back to videos.list on any error so the tool keeps
    working against older/regional API deployments."""
    items, failed = [], []
    for batch in _batched(video_ids, 50):
        try:
            data = _get("videos:batchGetStats", api_key, part=parts, id=",".join(batch))
            items.extend(data.get("items", []))
            failed.extend((data.get("summary") or {}).get("failedVideoIds", []) or [])
        except Exception:
            items.extend(videos_list(api_key, batch, parts="snippet,statistics,contentDetails"))
    return items, failed


def most_popular(api_key: str, region_code: str = "US", video_category_id: str = None,
                 max_results: int = 50, pages: int = 1):
    """videos.list?chart=mostPopular -- 1 unit/page.

    Since 21 July 2025 this is the Trending Music / Movies / Gaming chart, NOT
    general trending. Non-charted categories return few or zero items.
    """
    items, token = [], None
    for _ in range(max(1, pages)):
        data = _get(
            "videos", api_key,
            part=VIDEO_PARTS, chart="mostPopular", regionCode=region_code,
            videoCategoryId=video_category_id, maxResults=min(max_results, 50),
            pageToken=token,
        )
        items.extend(data.get("items", []))
        token = data.get("nextPageToken")
        if not token:
            break
    return items


# ---------------------------------------------------------------- channels

CHANNEL_PARTS = "snippet,statistics,contentDetails,topicDetails,brandingSettings"


def channels_list(api_key: str, channel_ids: list, parts: str = CHANNEL_PARTS):
    """channels.list -- 1 unit per call, up to 50 ids."""
    items = []
    for batch in _batched(set(channel_ids)):
        data = _get("channels", api_key, part=parts, id=",".join(batch))
        items.extend(data.get("items", []))
    return items


def channel_by_handle(api_key: str, handle: str, parts: str = CHANNEL_PARTS):
    """Resolve @handle -> channel resource (1 unit), with a legacy username fallback."""
    h = handle.lstrip("@")
    data = _get("channels", api_key, part=parts, forHandle=f"@{h}")
    items = data.get("items", [])
    if items:
        return items[0]
    data = _get("channels", api_key, part=parts, forUsername=h)
    items = data.get("items", [])
    return items[0] if items else None


# ------------------------------------------------------------ playlistItems

def playlist_items(api_key: str, playlist_id: str, max_items: int = 200):
    """playlistItems.list -- 1 unit per 50 videos. The cheap way to enumerate a
    channel's uploads (vs 1 of only 100 daily search calls, capped at 500)."""
    out, token, calls = [], None, 0
    while len(out) < max_items:
        data = _get(
            "playlistItems", api_key,
            part="contentDetails", playlistId=playlist_id,
            maxResults=min(50, max_items - len(out)), pageToken=token,
        )
        calls += 1
        for it in data.get("items", []):
            cd = it.get("contentDetails", {})
            if cd.get("videoId"):
                out.append({"video_id": cd["videoId"],
                            "published_at": cd.get("videoPublishedAt")})
        token = data.get("nextPageToken")
        if not token:
            break
    return out, calls


# -------------------------------------------------------------- categories

def video_categories(api_key: str, region_code: str = "US", hl: str = "en_US"):
    """videoCategories.list -- 1 unit. id -> title, plus the `assignable` flag
    (several returned ids are legacy and cannot be used as a real category)."""
    data = _get("videoCategories", api_key, part="snippet", regionCode=region_code, hl=hl)
    out = []
    for it in data.get("items", []):
        sn = it.get("snippet", {})
        out.append({"id": it["id"], "title": sn.get("title"),
                    "assignable": bool(sn.get("assignable"))})
    return out


# ---------------------------------------------------------------- comments

def comment_threads(api_key: str, video_id: str, max_results: int = 100,
                    order: str = "relevance", search_terms: str = None):
    """commentThreads.list -- 1 unit for up to 100 top-level threads."""
    try:
        data = _get("commentThreads", api_key, part="snippet", videoId=video_id,
                    maxResults=min(max_results, 100), order=order,
                    searchTerms=search_terms, textFormat="plainText")
    except RuntimeError as e:
        if "commentsDisabled" in str(e):
            return []
        raise
    return data.get("items", [])

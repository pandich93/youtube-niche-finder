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
import logging
import os
import re
import time

import requests

from domain import periods as P

log = logging.getLogger(__name__)

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
# multi-user plan P0-05: units of the shared pool only the worker (and the
# operator, with no signed-in owner) may spend -- stats snapshots, alerts and
# acceleration keep coming when users have used up the rest
WORKER_RESERVE_UNITS = int(os.environ.get("WORKER_RESERVE_UNITS") or 1500)


def user_units_key(user_id) -> str:
    """plan 15 (5.5): a signed-in user's share of today's units."""
    return f"yt_units_{P.pacific_date_key()}_u{user_id}"


def user_search_key(user_id) -> str:
    return f"search_calls_{P.pacific_date_key()}_u{user_id}"


def user_limits(user_id=None) -> dict:
    """A signed-in user's daily budget in multi-user mode: from their tariff
    (application/plans.py registers the lookup in quota_owner), else from
    .env. None = no limit, 0 = not at all; 0 in .env has always meant "no
    limit"."""
    from infrastructure import quota_owner
    if user_id is not None:
        got = quota_owner.limits_for(user_id)
        if got is not None:
            return got

    def num(name, default):
        try:
            value = max(0, int(os.environ.get(name) or default))
        except ValueError:
            value = default
        return value or None
    return {"units": num("NF_USER_DAILY_UNITS", 2000),
            "searchCalls": num("NF_USER_DAILY_SEARCH_CALLS", 20)}


def user_usage(user_id) -> dict:
    import infrastructure.postgres as db
    conn = db.get_conn()
    try:
        units = int(db.get_meta(conn, user_units_key(user_id)) or 0)
        searches = int(db.get_meta(conn, user_search_key(user_id)) or 0)
    finally:
        conn.close()
    limits = user_limits(user_id)
    return {"units": units, "searchCalls": searches, "limits": limits,
            "unitsLeft": None if limits["units"] is None else max(0, limits["units"] - units),
            "searchCallsLeft": (None if limits["searchCalls"] is None
                                else max(0, limits["searchCalls"] - searches)),
            "resetsAt": "midnight Pacific Time"}


def units_meta_key() -> str:
    """meta key of today's shared-pool counter -- per Pacific-Time day, when
    Google resets the 10,000 units."""
    return f"yt_units_{P.pacific_date_key()}"


def search_meta_key() -> str:
    """meta key of today's search.list calls, the 100-a-day bucket."""
    return f"search_calls_{P.pacific_date_key()}"


def _charges(path: str, uid) -> list:
    """What one request takes: [(meta key, amount, ceiling or None, kind)].
    Personal counters first, so a user over their own share hears about that.
    A signed-in user's request may not eat into the worker's reserve of the
    shared pool (multi-user plan P0-05); the worker and single-user mode
    (no owner) may use all of it."""
    cost = COST.get(path, 1)
    out = []
    if uid is not None:
        limits = user_limits(uid)
        out.append((user_units_key(uid), cost, limits["units"], "user-units"))
        if path == "search":
            out.append((user_search_key(uid), 1, limits["searchCalls"], "user-search"))
    reserve = WORKER_RESERVE_UNITS if uid is not None else 0
    out.append((units_meta_key(), cost, max(0, DAILY_UNIT_LIMIT - reserve), "pool"))
    if path == "search":
        out.append((search_meta_key(), 1, SEARCH_DAILY_CALL_LIMIT, "search-pool"))
    return out


def _refusal(kind: str, used: int, limit: int, for_users: bool):
    if kind == "user-units":
        return UserQuotaExceeded(
            f"your daily YouTube budget on this server is spent ({used} of {limit} units); "
            "it resets at midnight Pacific Time")
    if kind == "user-search":
        return UserQuotaExceeded(
            f"your daily search budget on this server is spent ({used} of {limit} searches); "
            "it resets at midnight Pacific Time")
    if kind == "search-pool":
        return SharedPoolExhausted(
            f"the server's {limit} YouTube searches for today are spent; "
            "they reset at midnight Pacific Time")
    return SharedPoolExhausted(
        f"the server's shared YouTube budget for today is spent ({used} of {limit} units"
        f"{' open to users' if for_users else ''}); it resets at midnight Pacific Time")


def _reserve(path: str) -> list:
    """Take this request's cost from every counter before it is sent, all or
    nothing, in one transaction: each counter goes up only if it stays within
    its ceiling (multi-user plan P0-05). Returns [(key, amount)] for _refund.
    A database failure is not swallowed: no reserve, no request."""
    # resolved at call time, so tests that swap infrastructure.postgres
    # for a double in sys.modules are honoured
    import infrastructure.postgres as db
    from infrastructure import quota_owner
    uid = quota_owner.current()
    charges = _charges(path, uid)
    conn = db.get_conn()
    try:
        for key, n, limit, kind in charges:
            if limit is None:
                db.incr_meta(conn, key, n)
            elif not db.incr_meta_capped(conn, key, n, limit):
                conn.rollback()
                raise _refusal(kind, int(db.get_meta(conn, key) or 0), limit, uid is not None)
        conn.commit()
    except QuotaExceeded:
        raise
    except Exception:
        log.warning("YouTube budget could not be reserved; the request is not sent",
                    exc_info=True)
        raise
    finally:
        conn.close()
    return [(key, n) for key, n, _limit, _kind in charges]


def _refund(taken: list):
    """Give back a reserve whose request never reached Google (network error
    before an answer): Google charged nothing. Best effort -- a failed refund
    over-counts, the safe side."""
    try:
        import infrastructure.postgres as db
        conn = db.get_conn()
        try:
            for key, n in taken:
                db.incr_meta(conn, key, -n)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.warning("YouTube budget reserve could not be refunded", exc_info=True)


class QuotaExceeded(RuntimeError):
    pass


class UserQuotaExceeded(QuotaExceeded):
    """One signed-in user's daily share is spent; everyone else can go on."""


class SharedPoolExhausted(QuotaExceeded):
    """The server's shared daily budget is spent -- for signed-in users the
    pool minus the worker's reserve, or the 100 searches a day (P0-05)."""


class YouTubeUnreachable(RuntimeError):
    """A network-level failure (DNS, TLS, timeout) after the retries; the
    message names only the error type, never the request URL."""


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
    # The key goes in the X-Goog-Api-Key header, never in the URL: requests
    # puts the full URL into its exception text, and on 2026-10-18 that
    # printed the key into the worker log.
    headers = {"X-Goog-Api-Key": api_key}
    last = None
    for attempt in range(retries):
        # reserved before every attempt: Google charges each answered one,
        # failed answers included; an attempt that never got an answer is refunded
        taken = _reserve(path)
        try:
            resp = requests.get(f"{BASE}/{path}", params=params, timeout=30, headers=headers)
        except requests.RequestException as e:
            _refund(taken)
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            # only the error's type: its text carries the URL
            raise YouTubeUnreachable(f"cannot reach YouTube: {type(e).__name__}") from None
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

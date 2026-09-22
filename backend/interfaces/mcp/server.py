"""niche-finder MCP server -- a self-hosted alternative to NexLev / vidIQ /
ViewStats, built on the free YouTube Data API v3 plus our own history database.

Tool groups:
  COLLECT   spend YouTube quota to fill the local corpus
  DISCOVER  the period-scoped sections (viral small channels, categories,
            keywords, outliers) -- all free, all read from Postgres
  TRACK     watchlist + deep channel analysis, growth, velocity, patterns

Quota reality since 1 June 2026: search.list is limited to 100 CALLS/DAY in its
own bucket, everything else shares 10,000 units/day. So collect_niche is the
expensive door and collect_channel / refresh_stats are nearly free -- prefer
them. Judgement calls that need a model ("is this faceless", "does this fit my
niche") are left to whichever Claude session calls these tools: the server
returns raw titles, descriptions and thumbnails and never calls a paid LLM API.
"""
import os
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

import infrastructure.postgres as db
from infrastructure.categories import repository as C
from application import collecting as collector
from application import search as q
from application import discovery as trends
from application import channel_tracking as T

load_dotenv()
API_KEY = os.environ.get("YOUTUBE_API_KEY")

db.init_db()
C.seed_fallback()

mcp = MCPServer("niche-finder")


def _require_key():
    if not API_KEY:
        raise RuntimeError(
            "YOUTUBE_API_KEY is not set. Put it in .env next to server.py "
            "(YOUTUBE_API_KEY=your_key), or pass it to the container with "
            "-e YOUTUBE_API_KEY=..., then restart."
        )


# ============================================================ COLLECT

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True))
def collect_niche(query: str, label: str = None, language: str = None,
                  min_upload_date: str = None, period: str = None, pages: int = 1,
                  order: str = "viewCount", video_duration: str = None,
                  region: str = None, category_id: str = None) -> dict:
    """Discover videos for a topic via YouTube search and store them locally.

    COSTS ONE OF YOUR 100 DAILY SEARCH CALLS PER PAGE -- this is the scarcest
    resource in the system, so prefer collect_channel for anything you can reach
    through a channel.

    query: search text, e.g. "гипотезы о мозге" or "faceless space explainer"
    label: niche bucket name (defaults to query); reuse it to keep growing one dataset
    period: convenience window for freshness -- "24h", "48h", "7d", "30d"
        (sets min_upload_date for you; this is how you feed the 24h sections)
    language: relevance hint, ISO 639-1, e.g. "ru"
    order: viewCount | date | relevance | rating
    video_duration: short (<4m) | medium | long (>20m)
    region / category_id: ISO country code and YouTube category id filters
    """
    _require_key()
    return collector.collect_niche(
        API_KEY, query, label=label, language=language, min_upload_date=min_upload_date,
        pages=pages, order=order, video_duration=video_duration, region=region,
        category_id=category_id, period=period)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True))
def collect_trending(regions: list = None, category_ids: list = None,
                     pages: int = 2) -> dict:
    """Snapshot YouTube's own mostPopular chart into the local DB. 1 unit/page.

    HONEST LIMITATION: since 21 July 2025 this chart only covers Trending Music,
    Movies and Gaming -- YouTube retired the general Trending tab. For any other
    vertical it returns little or nothing, and general trends must come from
    collect_niche(period="24h") plus the local sections below.
    """
    _require_key()
    return collector.collect_trending(API_KEY, regions=tuple(regions or ["US"]),
                                      category_ids=tuple(category_ids or [None]),
                                      pages=pages)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True))
def collect_channel(channel: str, max_videos: int = 100, niche: str = None) -> dict:
    """Pull a channel's recent uploads into the local DB, cheaply.

    Accepts a UC... id, an @handle, or any channel URL. Goes through the uploads
    playlist: 1 quota unit per 50 videos, no 500-result cap, and it does NOT
    touch the daily search budget. This is the right way to build a corpus.
    """
    _require_key()
    return collector.collect_channel(API_KEY, channel, max_videos=max_videos, niche=niche)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True))
def refresh_stats(scope: str = "recent", period: str = "30d", limit: int = 1000,
                  niche: str = None) -> dict:
    """Re-read view/like/comment counts for stored videos and append a snapshot.

    This is what turns a snapshot API into a time series: vph24h, viewsGained24h,
    acceleration and title/thumbnail-change detection all come from these rows.
    Run it at least daily (the Docker worker does it for you).
    scope: recent | tracked | niche | all. Cost ~1 unit per 50 videos.
    """
    _require_key()
    return collector.refresh_stats(API_KEY, scope=scope, period=period,
                                   limit=limit, niche=niche)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True))
def refresh_channels(channel_ids: list = None, only_tracked: bool = True) -> dict:
    """Snapshot subscriber/view/video counts for channels, for growth tracking.
    1 unit per 50 channels. Note the API rounds subscriberCount to 3 significant
    figures, so subscriber deltas are only meaningful below ~100k subs."""
    _require_key()
    return collector.refresh_channels(API_KEY, channel_ids=channel_ids,
                                      only_tracked=only_tracked)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True))
def refresh_categories(regions: list = None, hl: str = "en_US") -> dict:
    """Fetch the live category id -> title map per region (1 unit per region).
    Optional: a built-in fallback map ships with the server."""
    _require_key()
    return C.refresh_categories(API_KEY, regions=tuple(regions or ["US"]), hl=hl)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def backfill_embeddings(limit: int = 1000) -> dict:
    """Compute embeddings for already-collected videos that don't have one yet.
    0 YouTube quota -- pure local compute over title/description already in
    Postgres, no API key needed.

    collect_channel/track_channel default to embed=False (cheap collection),
    so most of the corpus lacks embeddings until this runs. Needed before
    similar_channels or search_outliers(query=...) can see a given channel.
    """
    return collector.backfill_embeddings(limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True))
def video_comments(video_id: str, max_results: int = 100, order: str = "relevance",
                   search_terms: str = None) -> dict:
    """Top-level comments for one video, live from the API. 1 unit, not stored
    locally.

    A competitive signal nothing else here surfaces: what viewers actually
    praise, complain about or ask for. Returns raw author/text/likeCount --
    judging tone or extracting themes is left to whichever Claude session
    calls this, same as everywhere else in this server.
    order: relevance | time. search_terms filters to comments containing a phrase.
    """
    _require_key()
    return collector.video_comments(API_KEY, video_id, max_results=max_results,
                                    order=order, search_terms=search_terms)


# ============================================================ DISCOVER

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def viral_videos_small_channels(period: str = "7d", period_by: str = "published",
                                max_subscribers: int = 10000,
                                min_views: int = 10000,
                                min_views_per_subscriber: float = 1.0,
                                min_outlier_score: float = None, niche: str = None,
                                languages: list = None, region: str = None,
                                category_id: str = None,
                                max_channel_video_count: int = None,
                                exclude_shorts: bool = True, only_shorts: bool = False,
                                sort_by: str = "viral", limit: int = 25,
                                preset: str = None) -> dict:
    """Videos that went far beyond their channel's size, in a time window. FREE.

    period: 24h | 48h | 7d | 30d | 90d | all
    period_by: "published" (what came out in the window, the default) or
        "discovered" (what WE first saw in the window). NexLev's own "Last 24
        Hours" list is the second kind -- that is why it is full of year-old
        videos -- so use "discovered" to reproduce their behaviour.
    sort_by: viral (age-normalised views per subscriber, default) | vsr | views |
        outlier | outlier_adjusted | vph | velocity | engagement | acceleration |
        published
    preset: "niche_all" drops max_subscribers/min_views/min_views_per_subscriber
        entirely -- every video collected under `niche`, not just the small-channel
        breakouts. Requires niche.

    Each result carries viewsPerSubscriber, an age-adjusted outlier score against
    the channel's own median, and -- once history exists -- vph24h and
    acceleration. If everything comes back empty, call data_coverage first: the
    window probably has no collected videos yet.
    """
    return trends.viral_videos_small_channels(
        period=period, period_by=period_by, max_subscribers=max_subscribers,
        min_views=min_views,
        min_views_per_subscriber=min_views_per_subscriber,
        min_outlier_score=min_outlier_score, niche=niche, languages=languages,
        region=region, category_id=category_id,
        max_channel_video_count=max_channel_video_count,
        exclude_shorts=exclude_shorts, only_shorts=only_shorts,
        sort_by=sort_by, limit=limit, preset=preset)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def most_popular_categories(period: str = "7d", period_by: str = "published",
                            niche: str = None, region: str = None,
                            languages: list = None, max_subscribers: int = None,
                            exclude_shorts: bool = False, compare_previous: bool = True,
                            rank_by: str = "views", min_videos: int = 3,
                            limit: int = 25) -> dict:
    """Category ranking for a window, with the shift versus the previous window. FREE.

    Returns per category: videos, channels, total and median views, share of all
    views, share change vs the previous equal-length window, median outlier,
    median views-per-subscriber, Shorts share and an RPM band.

    rank_by: "views" (default, says where the attention is) or "channels"
        (how NexLev ranks these cards -- says where the crowding is).
    period_by: "published" or "discovered", same meaning as elsewhere.

    Computed from the local corpus deliberately -- YouTube's own mostPopular
    chart has covered only Music/Movies/Gaming since July 2025 and cannot rank
    Education, Howto, People & Blogs and the rest at all.
    """
    return trends.most_popular_categories(
        period=period, period_by=period_by, niche=niche, region=region,
        languages=languages, max_subscribers=max_subscribers,
        exclude_shorts=exclude_shorts, compare_previous=compare_previous,
        rank_by=rank_by, min_videos=min_videos, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def trending_keywords(period: str = "24h", period_by: str = "published",
                      niche: str = None, region: str = None,
                      languages: list = None, category_id: str = None,
                      max_subscribers: int = None, exclude_shorts: bool = False,
                      source: str = "both", ngram_max: int = 3, min_videos: int = 3,
                      top_n: int = 30, sort_by: str = "momentum",
                      outlier_threshold: float = 3.0,
                      compare_previous: bool = True) -> dict:
    """Phrases rising in a window, each with a breakout-correlation score. FREE.

    momentum     share now / share in the previous equal window (smoothed)
    outlierLift  P(video is an outlier | phrase present) / base rate --
                 above ~1.5 the phrase actually correlates with breakouts
    trendScore   log(1+videos) * outlierLift * momentum

    source: titles | tags | both. sort_by: momentum | trend | lift | count | views.
    There is no such thing as YouTube search volume in the public API; anything
    advertising one is reselling Google Trends or scraping autocomplete.
    """
    return trends.trending_keywords(
        period=period, period_by=period_by, niche=niche, region=region,
        languages=languages,
        category_id=category_id, max_subscribers=max_subscribers,
        exclude_shorts=exclude_shorts, source=source, ngram_max=ngram_max,
        min_videos=min_videos, top_n=top_n, sort_by=sort_by,
        outlier_threshold=outlier_threshold, compare_previous=compare_previous)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def top_tags_by_category(period: str = "7d", period_by: str = "published",
                         niche: str = None, region: str = None,
                         exclude_shorts: bool = False, min_videos: int = 3,
                         top_n: int = 15) -> dict:
    """Literal YouTube tags -- exactly as the creator set them -- ranked per
    category by frequency and breakout correlation. FREE.

    Unlike trending_keywords(source="tags"), which N-grams tag text into
    topical phrases, a tag here is never split: "faceless channel automation"
    stays one unit. Answers "what tags do winning videos in category X
    actually use", not "what topics are trending". One category per response
    for every category with a tag used on >= min_videos videos, busiest
    category first.
    """
    return trends.top_tags_by_category(
        period=period, period_by=period_by, niche=niche, region=region,
        exclude_shorts=exclude_shorts, min_videos=min_videos, top_n=top_n)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def recently_added_outlier_channels(period: str = "24h",
                                    period_by: str = "discovered",
                                    min_multiplier: float = 2.0,
                                    max_subscribers: int = None,
                                    min_subscribers: int = None,
                                    niche: str = None, category_id: str = None,
                                    region: str = None, exclude_shorts: bool = True,
                                    limit: int = 25) -> dict:
    """Channels that entered the corpus recently AND are outperforming. FREE.

    The channel-level counterpart to viral_videos_small_channels: instead of a
    single breakout video it ranks whole channels by their best age-adjusted
    multiplier, with a 0-4 strength band (<2x, 2-3x, 3-5x, 5-10x, >10x).
    Defaults to period_by="discovered" because "recently added" is about when we
    first saw the channel, not when it last uploaded."""
    return T.recently_added_outlier_channels(
        period=period, period_by=period_by, min_multiplier=min_multiplier,
        max_subscribers=max_subscribers, min_subscribers=min_subscribers,
        niche=niche, category_id=category_id, region=region,
        exclude_shorts=exclude_shorts, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def high_future_competition(period: str = "30d", period_by: str = "published",
                            niche: str = None, region: str = None,
                            category_id: str = None, min_videos: int = 2,
                            limit: int = 25) -> dict:
    """Who is about to become your competition: young, fast-uploading channels
    whose recent videos already outperform, rolled up by category. FREE.

    competitionScore = medianMultiplier * log2(1 + uploads in window) * youth,
    where youth rewards channels under a year old -- an established channel
    doing well is a competitor you already have; a six-month-old one doing the
    same is one you are about to get."""
    return T.high_future_competition(
        period=period, period_by=period_by, niche=niche, region=region,
        category_id=category_id, min_videos=min_videos, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def search_outliers(query: str = None, niche: str = None, languages: list = None,
                    max_subscribers: int = None, max_channel_video_count: int = None,
                    min_upload_date: str = None, min_outlier_score: float = 3.0,
                    period: str = "all", region: str = None, category_id: str = None,
                    exclude_shorts: bool = False, only_shorts: bool = False,
                    min_video_length: int = None, max_video_length: int = None,
                    min_rpm: float = None, max_rpm: float = None,
                    sort_by: str = "outlier", limit: int = 25) -> list:
    """Search the local database for outlier videos. FREE, no quota, unlimited.

    Pass `query` for semantic ranking against local multilingual embeddings.
    outlierScore here is against the channel's own rolling median (the
    ViewStats/1of10 definition); outlierScoreNexlev is NexLev's lifetime-mean
    version, kept so numbers stay comparable with their UI.

    min_rpm/max_rpm filter on estimatedRpm, a NexLev-style RPM estimate
    derived from the video's category via the same static niche-RPM table
    channel revenue estimates use (domain/metrics.py NICHE_RPM) -- an
    approximation, not a measured payout. min_video_length/max_video_length
    are in seconds.
    """
    return q.search_outliers(
        query=query, niche=niche, languages=languages, max_subscribers=max_subscribers,
        max_channel_video_count=max_channel_video_count, min_upload_date=min_upload_date,
        min_outlier_score=min_outlier_score, period=period, region=region,
        category_id=category_id, exclude_shorts=exclude_shorts, only_shorts=only_shorts,
        min_video_length=min_video_length, max_video_length=max_video_length,
        min_rpm=min_rpm, max_rpm=max_rpm, sort_by=sort_by, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def niche_overview(niche: str, period: str = "all") -> dict:
    """Saturation and opportunity read on a collected niche: channel-size
    distribution, median outlier, viral skew, Shorts share, top categories and
    how many small channels are breaking out."""
    return q.niche_overview(niche, period=period)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def similar_channels(channel_id: str, niche: str = None, limit: int = 10,
                     min_videos_embedded: int = 1) -> dict:
    """Channels whose collected content reads as semantically closest to this
    one. FREE, no quota, searches only what is already in the local DB.

    Needs both this channel and the candidates to have embedded videos --
    collect_channel/track_channel default to embed=False (cheap collection),
    so re-run those with embed=True, or use collect_niche, first. Optionally
    scope the comparison pool with `niche` instead of the whole corpus.
    """
    return q.similar_channels(channel_id, niche=niche, limit=limit,
                              min_videos_embedded=min_videos_embedded)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def similar_videos(video_id: str, niche: str = None, limit: int = 10,
                   exclude_same_channel: bool = False) -> dict:
    """Videos whose title+description embedding reads closest to this one
    (NexLev's "Similar Videos"). FREE, no quota, local corpus only.

    Both the target video and the candidates need an embedding --
    collect_channel/track_channel default to embed=False; backfill_embeddings
    fills in videos collected that way. Optionally scope the comparison pool
    with `niche`, or exclude the video's own channel to surface competitors
    instead of the creator's own back-catalogue.
    """
    return q.similar_videos(video_id, niche=niche, limit=limit,
                            exclude_same_channel=exclude_same_channel)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def niche_overview_from_channel(channel_id: str, limit: int = 15,
                                min_videos_embedded: int = 1,
                                period: str = "all") -> dict:
    """NexLev-style get_niche_overview(channelId): the same saturation/
    opportunity read as niche_overview, but anchored on a channel instead of
    a pre-collected niche slug. Finds the channel's closest peers via
    similar_channels (embedding centroid, FREE/local) and runs the analysis
    over the channel + its peers. FREE, no quota -- needs the channel to have
    embedded videos, same requirement as similar_channels.
    """
    return q.niche_overview_from_channel(channel_id, limit=limit,
                                         min_videos_embedded=min_videos_embedded,
                                         period=period)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_niches() -> list:
    """Every niche collected so far (slug, query, last collected, video count)."""
    return q.list_niches()


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def db_stats() -> dict:
    """What is stored locally: channels, videos, niches, snapshots, DB path."""
    return q.db_stats()


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def data_coverage(period: str = "24h") -> dict:
    """Can the corpus actually answer a question about this window? Call this
    whenever a section returns fewer results than expected -- it distinguishes
    "nothing is trending" from "nothing has been collected yet"."""
    return trends.coverage(period)


# ============================================================ TRACK

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True))
def track_channel(channel: str, note: str = None, collect: bool = True,
                  max_videos: int = 100) -> dict:
    """Add a channel to the watchlist so its stats get snapshotted over time.

    Accepts a UC id, @handle or URL. With collect=True it also pulls the recent
    uploads immediately (cheap: uploads playlist, ~1 unit per 50 videos).
    """
    _require_key()
    if collect:
        res = collector.collect_channel(API_KEY, channel, max_videos=max_videos)
        cid = res.get("channelId")
        if not cid:
            return res
    else:
        # collect_channel above always resolves through the YouTube API, so
        # channelId there is already a real UC id. Without it, a raw @handle
        # or URL would otherwise land in tracked_channels as-is and the
        # worker could never poll it (#12 bug 1).
        conn = db.get_conn()
        try:
            cid = T.resolve_channel_id(conn, API_KEY, channel)
        except ValueError as e:
            return {"error": str(e)}
        finally:
            conn.close()
        res = {"channelId": cid}
    T.track(cid, note)
    return {**res, "tracked": True, "note": note}


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=True,
    idempotent_hint=True, open_world_hint=False))
def untrack_channel(channel_id: str) -> dict:
    """Stop tracking a channel (history already collected is kept)."""
    return T.untrack(channel_id)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_tracked_channels() -> list:
    """The watchlist, with how many snapshots exist per channel."""
    return T.list_tracked()


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def channel_analytics(channel_id: str, period: str = "30d") -> dict:
    """Full analysis of one channel: profile, upload cadence, performance
    (median vs average views, viral skew), growth over 24h/7d/30d/90d, momentum
    and a Social-Blade-style grade, linear projections, two revenue models, and
    its top outlier videos scored against its own rolling median.

    Growth fields are null until enough snapshots exist -- run refresh_channels /
    the worker for a few days first."""
    return T.channel_analytics(channel_id, period=period)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def compare_channels(channel_ids: list, period: str = "30d") -> dict:
    """Side-by-side comparison of several channels on the same metrics,
    ranked by median views per subscriber (size-independent)."""
    return T.compare_channels(channel_ids, period=period)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def channel_velocity(channel_id: str, period: str = "30d", limit: int = 25) -> dict:
    """Per-video view velocity: lifetime VPH, true 24h VPH from our snapshots,
    views gained in the last day, and whether each video is heating up or cooling."""
    return T.channel_velocity(channel_id, period=period, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def title_changes(period: str = "7d", channel_id: str = None, limit: int = 50) -> dict:
    """Videos whose title or thumbnail changed between snapshots -- usually a
    creator reacting to underperformance, and a useful competitive signal."""
    return T.title_changes(period=period, channel_id=channel_id, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def best_time_to_publish(niche: str = None, channel_id: str = None,
                         period: str = "90d", min_samples: int = 3,
                         timezone_offset_hours: int = 0) -> dict:
    """Score all 168 weekday/hour slots by the median age-adjusted outlier of
    videos published in them. Correlation, not causation -- but it is the same
    idea TubeBuddy charges for, and it needs a few hundred collected videos."""
    return T.best_time_to_publish(niche=niche, channel_id=channel_id, period=period,
                                  min_samples=min_samples,
                                  timezone_offset_hours=timezone_offset_hours)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def title_patterns(niche: str = None, channel_id: str = None, period: str = "90d",
                   outlier_threshold: float = 3.0, min_videos: int = 4,
                   top_n: int = 25) -> dict:
    """Which title phrases correlate with breakouts in a niche or on a channel,
    ranked by lift over the base outlier rate. Gives you the niche's actual
    title formulas instead of guesses."""
    return T.title_patterns(niche=niche, channel_id=channel_id, period=period,
                            outlier_threshold=outlier_threshold,
                            min_videos=min_videos, top_n=top_n)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def calibrate_maturity_curve(min_videos: int = 30) -> dict:
    """Measure the real view-accumulation curve from our own snapshots and print
    a replacement for metrics.MATURITY_CURVE, so age-adjusted outlier scores stop
    relying on the shipped default. Needs ~30 videos watched from publication."""
    return T.calibrate_maturity_curve(min_videos=min_videos)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=False))
def save_item(kind: str, ref_id: str, payload: dict = None, note: str = None,
             folder: str = None) -> dict:
    """Swipe file: save a video or channel id you noticed, with an optional
    snapshot of the metrics it had at the time (pass the dict another tool
    just returned, e.g. inspect_video's result). Zero quota, local only."""
    from application import library as lib
    return lib.save_item(kind, ref_id, payload=payload, note=note, folder=folder)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_saved_items(kind: str = None, folder: str = None, limit: int = 200) -> list:
    """List the swipe file, optionally filtered by kind ('video'/'channel')
    and/or folder."""
    from application import library as lib
    return lib.list_items(kind=kind, folder=folder, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=True,
    idempotent_hint=True, open_world_hint=False))
def delete_saved_item(item_id: int) -> dict:
    """Remove one swipe-file entry by id."""
    from application import library as lib
    return lib.delete_item(item_id)


# ---------------------------------------------------- metadata review (8.8)

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def review_metadata(title: str, description: str = "", tags: list = None,
                    niche: str = None, channel_id: str = None, is_short: bool = False,
                    period: str = "180d") -> dict:
    """Check a draft title/description/tags against your own corpus for this
    niche and/or channel -- signals only (length, structure, tag overlap,
    near-duplicate topics), each with its own sample size, never a single
    made-up score. Zero quota, local only. Pass niche and/or channel_id, or
    every signal comes back marked unreliable by design. Wording suggestions
    are not generated here -- once you see which signals are off, ask me
    (the model) to propose actual title text based on what this returned;
    that's the point of exposing facts instead of a canned rewrite."""
    from application import metadata_review as mr
    return mr.review_metadata(title, description=description, tags=tags or [],
                              niche=niche, channel_id=channel_id, is_short=is_short,
                              period=period)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=False))
def save_draft(title: str, description: str = "", tags: list = None, niche: str = None,
              channel_id: str = None, is_short: bool = False, review: dict = None) -> dict:
    """Save a metadata draft (optionally with the review_metadata snapshot
    attached) so it can be linked to the real video_id after publishing and
    checked against actual outcomes later via draft_outcomes."""
    from application import metadata_review as mr
    return mr.save_draft(title, description=description, tags=tags or [], niche=niche,
                         channel_id=channel_id, is_short=is_short, review=review)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_drafts(channel_id: str = None, unpublished_only: bool = False,
                limit: int = 100) -> list:
    """List saved metadata drafts, optionally only the ones not yet linked
    to a published video."""
    from application import metadata_review as mr
    return mr.list_drafts(channel_id=channel_id, unpublished_only=unpublished_only,
                          limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def link_draft(draft_id: int, video_id: str) -> dict:
    """Call once a saved draft has actually been published, so draft_outcomes
    can later compare what the review predicted to what really happened."""
    from application import metadata_review as mr
    return mr.link_draft(draft_id, video_id)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def draft_outcomes(min_age_days: float = 7.0) -> list:
    """For linked drafts old enough to have real view counts, return the
    review snapshot next to the actual outcome -- the only honest way to
    learn whether these signals predict anything for YOUR channel. Does not
    itself judge right/wrong; hands both numbers back."""
    from application import metadata_review as mr
    return mr.draft_outcomes(min_age_days=min_age_days)


# ------------------------------------------------------------- alerts (8.9)

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def scan_for_alerts() -> dict:
    """Run the alert scan right now instead of waiting for the worker's own
    schedule: new outlier (x>=3) on a tracked channel, a video accelerating
    (x>=2), a title changed, or a channel posting again after a silent
    stretch. Zero quota -- reads only what's already collected. Idempotent:
    re-running never creates duplicate events for the same occurrence."""
    from application import alerts as alerts_mod
    return alerts_mod.scan()


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_events(unseen_only: bool = False, kind: str = None, limit: int = 100) -> list:
    """List alert events, optionally filtered to unseen ones or one kind
    ('outlier'/'acceleration'/'title_change'/'silence_break')."""
    from application import alerts as alerts_mod
    return alerts_mod.list_events(unseen_only=unseen_only, kind=kind, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def mark_events_seen(ids: list = None, all_unseen: bool = False) -> dict:
    """Mark specific event ids (or every unseen event, with all_unseen=True)
    as seen."""
    from application import alerts as alerts_mod
    return alerts_mod.mark_seen(ids=ids, all_unseen=all_unseen)


def run():
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport, host=os.environ.get("MCP_HOST", "0.0.0.0"),
                port=int(os.environ.get("MCP_PORT", "8765")))


if __name__ == "__main__":
    run()

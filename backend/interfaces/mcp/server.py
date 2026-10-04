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
from application import channel_tracking as T
from application import collecting as collector
from application import discovery as trends
from application import enrichment as enrich_uc
from application import hook_score as hook_uc
from application import niche_clusters as clusters_uc
from application import search as q
from application import tags as tags_uc
from application import transcripts as transcripts_uc
from infrastructure.categories import repository as C

load_dotenv()
API_KEY = os.environ.get("YOUTUBE_API_KEY")

db.init_db()
C.seed_fallback()

def _build_server():
    """stdio: local, user 1, no sign-in. HTTP with NF_MULTI_USER=1: bearer API
    tokens required, tools act as the token's user (plan 15, 5.8)."""
    from interfaces.mcp import auth as mcp_auth
    if mcp_auth.http_auth_enabled():
        return MCPServer("niche-finder", token_verifier=mcp_auth.ApiTokenVerifier(),
                         auth=mcp_auth.auth_settings(),
                         middleware=[mcp_auth.quota_owner_middleware])
    return MCPServer("niche-finder")


mcp = _build_server()


def _uid() -> int:
    """Whose personal data a tool works on: the API token's user over HTTP,
    the local user over stdio."""
    from interfaces.mcp import auth as mcp_auth
    return mcp_auth.current_user_id()


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
    Videos the API no longer returns are recorded as "gone" candidates
    (`gone` in the result) for the video_gone alert.
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
    figures, so subscriber deltas are only meaningful below ~100k subs.
    Channels the API no longer returns are recorded as "gone" candidates
    (`gone` in the result) for the channel_gone alert."""
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


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True))
def comment_insights(video_id: str, max_comments: int = 200,
                     force_refresh: bool = False) -> dict:
    """Pains/requests/video ideas mined from a video's comments via LLM.
    Cached for LLM_INSIGHTS_TTL_DAYS (7) -- a repeat call in that window is
    free. On a cache miss: 1 YouTube quota unit + an LLM call (costs real
    money once LLM_PROVIDER=openrouter is configured; a Null provider
    returns a clear hint instead of silently doing nothing)."""
    _require_key()
    return enrich_uc.comment_insights(API_KEY, video_id, max_comments=max_comments,
                                      force_refresh=force_refresh)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True))
def content_gaps(niche: str, top_videos: int = 10, use_llm: bool = None,
                 fetch: bool = False, limit: int = 20) -> dict:
    """Demand without supply: questions and requests from the comments of a
    niche's top videos (by views) that no collected video or pasted transcript
    covers yet, ranked by demand (askers, likes, how many videos they asked
    under) x (1 - coverage). Each gap has example comments, the source videos
    and the nearest existing video/transcript; status free / partial, with the
    covered ones counted apart. With an LLM configured (use_llm=None means "if
    configured") questions come from comment_insights; without one, from rules
    (question mark or explicit request, English and Russian) -- noisier, and
    the answer says so. fetch=false (default) reads only cached comments, zero
    quota; fetch=true spends 1 YouTube unit per uncached video (plus an LLM
    call each in LLM mode); videos not read are listed in skippedVideos with
    the reason. Coverage is checked against the local corpus only
    (coverageBase says how big it is). Next step: build_brief(video_id of a
    source video, gap_topic=the gap's topic)."""
    if fetch:
        _require_key()
    from application import content_gaps as gaps_mod
    return gaps_mod.content_gaps(API_KEY, niche, top_videos=top_videos, use_llm=use_llm,
                                 fetch=fetch, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=False))
def explain_outlier(video_id: str, force_refresh: bool = False) -> dict:
    """Why this video beat its channel's own baseline: up to 3 hooks, a
    title pattern, a timing factor, a short replicable formula, and a
    confidence score -- grounded only in this video's real numbers, never
    invented. Cached for LLM_WHY_VIRAL_TTL_DAYS (14). Zero YouTube quota;
    costs an LLM call on a cache miss."""
    return enrich_uc.explain_outlier(video_id, force_refresh=force_refresh)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def request_transcript(video_id: str, reason: str = None, compare_group: str = None) -> dict:
    """Queue a video for a manually-pasted transcript (stage 19) -- we never
    fetch subtitles automatically, someone has to copy the text off
    YouTube's own transcript panel and paste it via the dashboard's
    Транскрипты screen. Shows up in list_transcript_queue(status='pending')
    until then."""
    return transcripts_uc.request_transcript(video_id, reason=reason, user_id=_uid(),
                                             compare_group=compare_group,
                                             requested_by="claude-mcp")


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_transcript_queue(status: str = None) -> list:
    """status: 'pending' | 'ready' | 'error', or omit for everything."""
    return transcripts_uc.list_transcript_queue(status=status, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def search_transcripts(query: str, niche: str = None, compare_group: str = None,
                       k: int = 10) -> dict:
    """Hybrid search (vector + Postgres full-text, RRF-merged) over every
    saved transcript's chunks. Each result: text fragment, video/channel,
    a youtu.be link with ?t=<seconds> when the chunk has a timestamp,
    views. FREE, no quota -- only searches what's already been pasted in."""
    try:
        return transcripts_uc.search_transcripts(query, niche=niche, user_id=_uid(),
                                                  compare_group=compare_group, k=k)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def hook_report(video_id: str, niche: str = None, llm: bool = False,
                force_refresh: bool = False) -> dict:
    """Score 0-100 of the first ~30 seconds of a saved transcript -- an
    assessment of the intro's TEXT, not of the visual hook. Deterministic:
    question to the viewer, concrete number, promised payoff, intrigue,
    addressing "you", first-sentence length, pace, minus filler
    ("welcome back", "subscribe"); returns what hit, the penalties and up to
    3 tips. With `niche`, compares with that niche's outlier hooks (or says
    there is too little data). Zero YouTube quota. llm=True sends the intro
    text to the LLM provider and costs money (cached 30 days in
    video_insights, the only reason this tool is not read-only); with the
    default llm=False nothing is written. A correlation, not a cause."""
    return hook_uc.hook_report(video_id, niche=niche, llm=llm, force_refresh=force_refresh)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def niche_hook_benchmark(niche: str) -> dict:
    """How the intros (first ~30 seconds, text only -- not the visual hook)
    of a niche's outliers differ from its ordinary videos: mean score and
    the features more common among outliers. Uses only videos with a pasted
    transcript, so it usually reports "insufficient-data" until 10 outlier
    and 10 ordinary transcripts exist. Zero quota, no LLM."""
    return hook_uc.niche_hook_benchmark(niche)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def score_hook_text(text: str, niche: str = None) -> dict:
    """Score a draft intro the author is writing (text of the first ~30
    seconds, not the visual hook), optionally against a niche's benchmark.
    Zero quota; the draft is scored in memory and is NOT stored, logged or
    sent to any LLM."""
    try:
        return hook_uc.score_hook_text(text, niche=niche)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def score_titles(candidates: list, niche: str = None, channel_id: str = None) -> dict:
    """Score title candidates 0-100 against this niche/channel's actual
    title patterns (from title_patterns) and near-duplicate check against
    already-published videos. Works with no LLM at all (deterministic
    score: length, digits, matched patterns, duplicates); with LLM_PROVIDER
    configured also grounds each score with strengths/risks/an improved
    rewrite. Zero YouTube quota; costs an LLM call only if configured."""
    try:
        return enrich_uc.score_titles(candidates, niche_slug=niche, channel_id=channel_id)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=False))
def suggest_titles(topic: str, niche: str = None, channel_id: str = None, n: int = 10) -> dict:
    """Generate up to n title candidates for `topic` in the style of the
    niche/channel's best-performing titles, then score them via
    score_titles. Requires LLM_PROVIDER configured -- generation has no
    deterministic fallback (unlike scoring)."""
    try:
        return enrich_uc.suggest_titles(topic, niche_slug=niche, channel_id=channel_id, n=n)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def niche_map() -> dict:
    """Stage 08: informal niches discovered by k-means clustering channels'
    video embeddings (no manual niche definition needed) -- name,
    description, audience, channel count, median outlier score, total view
    velocity, faceless share, and a competition count (channels over 100k
    subs), sorted by opportunity (high outlier, low competition first). Each
    cluster also carries `saturation` (the niche_overview trend over its
    channels); it is shown, not folded into the order.
    Recomputed daily by the worker, or on demand via
    POST /api/niche-clusters/recompute."""
    return clusters_uc.niche_map()


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def niche_comment_insights(niche: str, top_n: int = 5) -> dict:
    """Merges whichever of a niche's top-viewed videos already have a cached
    comment_insights() result into one niche-level summary. Never fetches
    comments or spends YouTube quota itself -- call comment_insights on the
    videos you care about first."""
    return enrich_uc.niche_comment_insights(niche, top_n=top_n)


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
                      compare_previous: bool = True, keywords_mode: str = "ngram",
                      semantic_similarity: float = 0.85) -> dict:
    """Phrases rising in a window, each with a breakout-correlation score. FREE.

    momentum     share now / share in the previous equal window (smoothed)
    outlierLift  P(video is an outlier | phrase present) / base rate --
                 above ~1.5 the phrase actually correlates with breakouts
    trendScore   log(1+videos) * outlierLift * momentum

    source: titles | tags | both. sort_by: momentum | trend | lift | count | views.
    keywords_mode="semantic" (stage 10) merges paraphrases an n-gram model
    can't see ("cold shower" / "cold showers") via local embeddings --
    default "ngram" mode is unchanged from before this existed.
    There is no such thing as YouTube search volume in the public API; anything
    advertising one is reselling Google Trends or scraping autocomplete.
    """
    return trends.trending_keywords(
        period=period, period_by=period_by, niche=niche, region=region,
        languages=languages,
        category_id=category_id, max_subscribers=max_subscribers,
        exclude_shorts=exclude_shorts, source=source, ngram_max=ngram_max,
        min_videos=min_videos, top_n=top_n, sort_by=sort_by,
        outlier_threshold=outlier_threshold, compare_previous=compare_previous,
        keywords_mode=keywords_mode, semantic_similarity=semantic_similarity)


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
                                    limit: int = 25, min_ypp_status: str = None) -> dict:
    """Channels that entered the corpus recently AND are outperforming. FREE.

    The channel-level counterpart to viral_videos_small_channels: instead of a
    single breakout video it ranks whole channels by their best age-adjusted
    multiplier, with a 0-4 strength band (<2x, 2-3x, 3-5x, 5-10x, >10x).
    Defaults to period_by="discovered" because "recently added" is about when we
    first saw the channel, not when it last uploaded.

    min_ypp_status keeps only channels past public YouTube Partner Program
    thresholds they VISIBLY meet: "subscribers-met" (500+ subscribers) or
    "shorts-path-met" (a tier fully met through Shorts views we collected).
    This is NOT a monetization status -- YouTube does not publish one; watch
    hours are not in the API, and channels with a hidden count never pass."""
    return T.recently_added_outlier_channels(
        period=period, period_by=period_by, min_multiplier=min_multiplier,
        max_subscribers=max_subscribers, min_subscribers=min_subscribers,
        niche=niche, category_id=category_id, region=region,
        exclude_shorts=exclude_shorts, limit=limit, min_ypp_status=min_ypp_status)


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
                    sort_by: str = "outlier", limit: int = 25,
                    min_ypp_status: str = None) -> list:
    """Search the local database for outlier videos. FREE, no quota, unlimited.

    Pass `query` for semantic ranking against local multilingual embeddings.
    outlierScore here is against the channel's own rolling median (the
    ViewStats/1of10 definition); outlierScoreNexlev is NexLev's lifetime-mean
    version, kept so numbers stay comparable with their UI.

    min_rpm/max_rpm filter on estimatedRpm, a NexLev-style RPM estimate
    derived from the video's category via the same static niche-RPM table
    channel revenue estimates use (domain/metrics.py NICHE_RPM) -- an
    approximation, not a measured payout. estimatedRpm is the middle of
    estimatedRpmRange (half to double it, low confidence: public estimates
    for one niche disagree by up to 7x); quote the range, not the middle.
    min_video_length/max_video_length are in seconds.

    min_ypp_status keeps only channels past public YouTube Partner Program
    thresholds they VISIBLY meet: "subscribers-met" (500+ subscribers) or
    "shorts-path-met" (a tier fully met through Shorts views we collected).
    This is NOT a monetization status -- YouTube does not publish one; watch
    hours are not in the API, and channels with a hidden count never pass.
    """
    return q.search_outliers(
        query=query, niche=niche, languages=languages, max_subscribers=max_subscribers,
        max_channel_video_count=max_channel_video_count, min_upload_date=min_upload_date,
        min_outlier_score=min_outlier_score, period=period, region=region,
        category_id=category_id, exclude_shorts=exclude_shorts, only_shorts=only_shorts,
        min_video_length=min_video_length, max_video_length=max_video_length,
        min_rpm=min_rpm, max_rpm=max_rpm, sort_by=sort_by, limit=limit,
        min_ypp_status=min_ypp_status)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def check_ideas(ideas: list, niche: str = None, min_similarity: float = 0.55,
                recent_days: float = 90, proven_outlier: float = 2.0,
                flop_outlier: float = 0.5) -> dict:
    """Batch-check up to 50 content ideas (free text, e.g. "car wash") against
    what's already collected: free (nobody's covered it), recent (covered
    within recent_days -- skip), proven (covered longer ago with a strong
    outlier -- demand validated), flopped (covered longer ago without a
    strong outlier). Uses local semantic embeddings when available, always
    falls back to a title-substring match too (lower accuracy, still works
    with zero embeddings). FREE, no quota."""
    try:
        return q.check_ideas(ideas, niche=niche, min_similarity=min_similarity,
                             recent_days=recent_days, proven_outlier=proven_outlier,
                             flop_outlier=flop_outlier)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def niche_overview(niche: str, period: str = "all") -> dict:
    """Saturation and opportunity read on a collected niche: channel-size
    distribution, median outlier, viral skew, Shorts share, top categories and
    how many small channels are breaking out. saturation_v2 is the trend: the
    last 30 days against the 90 before (always its own 120-day window, not
    `period`) -- status growing / stable / cooling / saturated /
    insufficient-data (under 20 videos in either window), from supply (videos),
    demand (median views projected to day 30), entrants (channels created in
    the window) and newcomers breaking out, each as a reason with its numbers;
    confidence "low" when few of the videos were seen young, i.e. the trend may
    be how they were collected. Zero quota."""
    return q.niche_overview(niche, period=period)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def niche_videos(niche: str, period: str = "all", channel_ids: list = None,
                 include_shorts: bool = True) -> dict:
    """Flat per-video list for a niche: date, views, channel, both outlier
    bases from stage 14 (rolling and period), duration. channel_ids narrows
    to a subset of the niche's channels; include_shorts=False drops Shorts."""
    return q.niche_videos(niche, period=period, channel_ids=channel_ids,
                          include_shorts=include_shorts)


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
    over the channel + its peers, saturation_v2 trend included. FREE, no
    quota -- needs the channel to have embedded videos, same requirement as
    similar_channels.
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
    T.track(cid, note, user_id=_uid())
    return {**res, "tracked": True, "note": note}


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=True,
    idempotent_hint=True, open_world_hint=False))
def untrack_channel(channel_id: str) -> dict:
    """Stop tracking a channel (history already collected is kept)."""
    return T.untrack(channel_id, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_tracked_channels() -> list:
    """The watchlist, with how many snapshots exist per channel."""
    return T.list_tracked(user_id=_uid())


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
    """Videos whose title changed between snapshots -- usually a creator
    reacting to underperformance, and a useful competitive signal. Thumbnail
    swaps are not visible here (the API's thumbnail URL never changes); use
    packaging_changes for those."""
    return T.title_changes(period=period, channel_id=channel_id, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def packaging_changes(period: str = "30d", channel_id: str = None, field: str = None,
                      video_id: str = None, limit: int = 50) -> dict:
    """Repackaging: title and thumbnail swaps after publishing, newest first,
    with before/after (titles as text, thumbnails as archived image paths on
    the dashboard) and views per hour in the 48h before vs after -- an
    observed effect, not a cause. Thumbnail swaps come from the worker's
    image fingerprints of TRACKED channels' recent videos (the API cannot see
    them). field: title | thumbnail_image. Pass video_id for one video's full
    history and all its archived thumbnail versions. Zero quota."""
    from application import packaging as packaging_mod
    if video_id:
        return packaging_mod.packaging_history(video_id)
    return packaging_mod.packaging_feed(period=period, channel_id=channel_id,
                                        field=field, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def sponsor_map(niche: str, period: str = "all", top_n: int = 10) -> dict:
    """Who pays creators in a niche: the share of its videos with a named
    sponsor or promo code, the top brands (videos, channels, last seen, example
    videos with the description line as evidence), affiliate brands listed
    apart (a commission link is not a paid integration), and average/median
    views with vs without a sponsor. A lower bound: only what is written in
    descriptions -- a sponsor spoken only in the video is invisible. Read from
    what the worker already scanned (scanCoverage says how much). Zero quota."""
    from application import sponsors as sponsors_mod
    return sponsors_mod.sponsor_map(niche, period=period, top_n=top_n)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def channel_sponsors(channel_id: str, period: str = "all", top_n: int = 10) -> dict:
    """The sponsor picture of one channel: which brands it names in
    descriptions (sponsor / promo code), its affiliate links apart, and how many
    of its videos carry them. A lower bound: only what is written in
    descriptions. Zero quota."""
    from application import sponsors as sponsors_mod
    return sponsors_mod.channel_sponsors(channel_id, period=period, top_n=top_n)


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
    """Measure the real view-accumulation curve from our own snapshots, with
    the per-age sample counts and what is still missing. Read-only: the worker
    re-checks daily and switches every age-adjusted score over to the measured
    curve by itself once it passes (30+ videos watched from publication to
    28+ days); db_stats shows which curve is in use."""
    return T.calibrate_maturity_curve(min_videos=min_videos)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def tag_videos(items: list, source: str, replace: bool = False) -> dict:
    """Attach curated tags to videos (theme/trigger/format/... -- your own
    tag_group names). items: [{"video_id", "tag_group", "tags": [...]}, ...].
    source must be 'manual', 'claude-mcp' or 'llm' -- an 'llm' write never
    overwrites or deletes a tag set by 'manual' or 'claude-mcp'.
    replace=True makes an item's tags the full set for that video+group
    (still subject to the same protection). Zero quota, local only."""
    try:
        return tags_uc.tag_videos(items, source=source, replace=replace)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_video_tags(niche: str = None, video_id: str = None) -> list:
    """List curated tags, either every tagged video in a niche or every tag
    on one video_id -- pass exactly one of the two."""
    try:
        return tags_uc.list_video_tags(niche=niche, video_id=video_id)
    except ValueError as e:
        return [{"error": str(e)}]


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def tag_stats(niche: str, tag_group: str, outlier_threshold: float = 3.0,
              exclude_recent_days: int = 30) -> dict:
    """Per-tag outlier-hit rate within one tag_group in one niche: videos,
    hits, hitRate, lift (relative to the whole niche's hit rate, not just
    the tagged subset), medianViews, medianOutlier. exclude_recent_days
    drops videos too young to have a stable outlier signal yet."""
    return tags_uc.tag_stats(niche, tag_group, outlier_threshold=outlier_threshold,
                             exclude_recent_days=exclude_recent_days)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_proposed_tags(niche: str) -> list:
    """Stage 03 LLM tags that missed the niche's taxonomy and need a human
    accept/reject before they count in tag_stats (see resolve_proposed_tag)."""
    return tags_uc.list_proposed_tags(niche)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=True,
    idempotent_hint=True, open_world_hint=False))
def resolve_proposed_tag(video_id: str, tag_group: str, tag: str, accept: bool) -> dict:
    """Accept (joins the niche's taxonomy, counted by tag_stats from now on)
    or reject (deleted outright) one row from list_proposed_tags."""
    return tags_uc.resolve_proposed_tag(video_id, tag_group, tag, accept)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=False))
def enrich_channels(limit: int = 50) -> dict:
    """Background AI labeling (stage 03): classify up to `limit` channels
    that were never labeled or were labeled more than LLM_RELABEL_DAYS ago --
    is_faceless, content_format, topic, language, ... Costs LLM budget (the
    worker also runs this automatically); no-op if LLM_PROVIDER=none or the
    daily budget is already spent."""
    return enrich_uc.classify_channels(limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=False))
def tag_new_videos(limit: int = 100) -> dict:
    """Background AI tagging (stage 03): auto-tag up to `limit` videos in
    niches whose tag taxonomy is trained enough (>=20 manual/claude-mcp tags
    in that tag_group). Writes with source='llm' (never overwrites a manual
    or claude-mcp tag); a tag outside the existing taxonomy is written with
    proposed=true and excluded from tag_stats until accepted via
    resolve_proposed_tag. Costs LLM budget; no-op if LLM_PROVIDER=none or
    the daily budget is already spent."""
    return enrich_uc.tag_new_videos(limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=False))
def save_item(kind: str, ref_id: str, payload: dict = None, note: str = None,
             folder: str = None) -> dict:
    """Swipe file: save a video or channel id you noticed, with an optional
    snapshot of the metrics it had at the time (pass the dict another tool
    just returned, e.g. inspect_video's result). Zero quota, local only."""
    from application import library as lib
    return lib.save_item(kind, ref_id, payload=payload, note=note, folder=folder, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_saved_items(kind: str = None, folder: str = None, limit: int = 200) -> list:
    """List the swipe file, optionally filtered by kind ('video'/'channel')
    and/or folder."""
    from application import library as lib
    return lib.list_items(kind=kind, folder=folder, limit=limit, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=True,
    idempotent_hint=True, open_world_hint=False))
def delete_saved_item(item_id: int) -> dict:
    """Remove one swipe-file entry by id."""
    from application import library as lib
    return lib.delete_item(item_id, user_id=_uid())


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
                         channel_id=channel_id, is_short=is_short, review=review,
                         user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_drafts(channel_id: str = None, unpublished_only: bool = False,
                limit: int = 100) -> list:
    """List saved metadata drafts, optionally only the ones not yet linked
    to a published video."""
    from application import metadata_review as mr
    return mr.list_drafts(channel_id=channel_id, unpublished_only=unpublished_only,
                          limit=limit, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def link_draft(draft_id: int, video_id: str) -> dict:
    """Call once a saved draft has actually been published, so draft_outcomes
    can later compare what the review predicted to what really happened."""
    from application import metadata_review as mr
    return mr.link_draft(draft_id, video_id, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def draft_outcomes(min_age_days: float = 7.0) -> list:
    """For linked drafts old enough to have real view counts, return the
    review snapshot next to the actual outcome -- the only honest way to
    learn whether these signals predict anything for YOUR channel. Does not
    itself judge right/wrong; hands both numbers back."""
    from application import metadata_review as mr
    return mr.draft_outcomes(min_age_days=min_age_days, user_id=_uid())


# ------------------------------------------------------------- alerts (8.9)

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def scan_for_alerts() -> dict:
    """Run the alert scan right now instead of waiting for the worker's own
    schedule: new outlier (x>=3) on a tracked channel, a video accelerating
    (x>=2), a title changed, a channel posting again after a silent
    stretch, or a tracked channel / already-alerted outlier video that the
    API stopped returning (confirmed after two misses 6h+ apart). Zero
    quota -- reads only what's already collected. Idempotent: re-running
    never creates duplicate events for the same occurrence."""
    from application import alerts as alerts_mod
    return alerts_mod.scan()


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=False))
def build_brief(video_id: str, niche: str = None, use_llm: bool = True,
                save: bool = True, gap_topic: str = None) -> dict:
    """Turn one outlier video into a working brief for YOUR OWN video: its
    numbers and hook (first ~75 words of a pasted transcript), why it worked
    (LLM), niche title patterns and best publish time, whether the topic is
    already covered (the source video itself excluded), title candidates
    (LLM) and similar videos as thumbnail references. Every part that could
    not run is listed in `skipped` with the reason -- without an LLM there is
    no angle and no new titles, and no template pretends otherwise. save=true
    stores a draft (drafts.source_video_id = the outlier) and queues a missing
    transcript; save=false writes nothing. Research, not a script: choose your
    own angle. Zero YouTube quota; use_llm=true may spend LLM budget.
    gap_topic: a viewer question from content_gaps -- the overlap check and
    title candidates are then about that question, the video stays the
    reference for hook and numbers."""
    from application import briefs as briefs_mod
    return briefs_mod.build_brief(video_id, niche=niche, use_llm=use_llm, save=save,
                                  gap_topic=gap_topic, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def template_risk(channel_id: str, last_n: int = 30) -> dict:
    """How much a channel's recent uploads look like one template repeated --
    the pattern behind YouTube's "inauthentic content" demonetisations.
    Scores the last_n uploads (long-form and Shorts are never mixed) 0-100
    from title similarity (embeddings), shared title openings/endings,
    uniform video length and a metronome upload rhythm, and says which of
    them pushed it up. Needs 10+ videos, otherwise level is
    insufficient-data. A heuristic over public patterns, NOT YouTube's
    verdict: series, podcasts and music channels can score high. Zero quota."""
    from application import template_risk as trk_mod
    return trk_mod.template_risk(channel_id, last_n=last_n)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def niche_template_risk(niche: str, last_n: int = 30, top_n: int = 10) -> dict:
    """template_risk for every channel with videos in a niche: how many are
    low / medium / high risk, the share of high-risk channels among the
    scored ones, and the top_n most templated channels. A niche where most
    channels are high risk is one where copycats are about to be swept --
    and where copying the format is dangerous. Same heuristic caveats as
    template_risk. Zero quota."""
    from application import template_risk as trk_mod
    return trk_mod.niche_template_risk(niche, last_n=last_n, top_n=top_n)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def daily_digest(period: str = "24h", top_n: int = 5) -> dict:
    """The daily digest's content, without sending it: new outliers and
    accelerating videos on tracked channels, channels that just entered the
    database already outperforming, title/thumbnail swaps, and channels or
    videos that disappeared -- each section's total plus its top_n strongest
    items. The worker sends the same summary to Telegram/webhook once a day
    when NOTIFY_MODE is digest or both. Zero quota."""
    from application import digest as digest_mod
    return digest_mod.build_digest(period=period, top_n=top_n, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_events(unseen_only: bool = False, kind: str = None, limit: int = 100) -> list:
    """List alert events, optionally filtered to unseen ones or one kind
    ('outlier'/'acceleration'/'title_change'/'silence_break'/'channel_gone'/
    'video_gone'/'milestone'/'topic_match')."""
    from application import alerts as alerts_mod
    return alerts_mod.list_events(unseen_only=unseen_only, kind=kind, limit=limit, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def mark_events_seen(ids: list = None, all_unseen: bool = False) -> dict:
    """Mark specific event ids (or every unseen event, with all_unseen=True)
    as seen."""
    from application import alerts as alerts_mod
    return alerts_mod.mark_seen(ids=ids, all_unseen=all_unseen, user_id=_uid())


# ------------------------------------------------- score catalog (plan 23)

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def explain_scores(key: str = None) -> dict:
    """What a number in these tools' answers is: YouTube data or an estimate
    of niche-finder (source), the formula, its inputs and the minimum sample
    it needs to mean anything. Without `key`, the whole catalog (outlierScore,
    vsr, vph, acceleration, revenueRange, trendScore, nicheTrend,
    templateRisk, hookScore, titleScore, ideaVerdict, ypp, milestones,
    repeatability, ...). Quote it when the user asks "how is this computed?"
    and never present an estimate as YouTube's own figure."""
    from domain import score_catalog as sc
    return sc.catalog(key)


# ------------------------------------------------- video trajectory (plan 20)

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def video_trajectory(video_ids: list) -> dict:
    """Views by age (hours since publishing) for 1-5 videos from the worker's
    snapshots, each next to the curve its channel would make (median views x
    maturity curve), with marks for title/thumbnail swaps. Shows whether a
    video took off on day one or grew slowly, and how yours compares with an
    outlier at the same age. A video found late has no start of its curve
    (observedFromHours). Zero quota."""
    from application import trajectory as tj
    return tj.video_trajectory(video_ids)


# --------------------------------------------- format repeatability (plan 21)

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def format_repeatability(video_id: str, min_similarity: float = 0.6, niche: str = None) -> dict:
    """Did this video's format work for OTHER channels too, or was it one
    channel's luck? Takes the video's embedding neighbours on other channels
    (cosine >= min_similarity), scores each with the usual outlier baseline,
    and counts each channel once by its best one: 'repeatable' (3+ channels
    got >= 2x), 'mixed', 'one_off' (nobody else did), or 'unknown' (too few
    similar videos collected -- not "it never worked"). Also says how many
    other channels start their titles the same way. Run it before copying an
    outlier. Zero quota."""
    from application import repeatability as rp
    return rp.format_repeatability(video_id, min_similarity=min_similarity, niche=niche)


# ------------------------------------------------------ topic alerts (plan 19)

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=False))
def watch_topic(text: str, threshold: float = 0.6) -> dict:
    """Watch a topic in plain words ("ai agents for small business"): every
    video the database collects from now on -- tracked channels' RSS, niche
    collections, trending -- whose title+description embedding has cosine
    similarity >= threshold raises a personal `topic_match` alert (dashboard,
    extension, Telegram/webhook). Zero quota. Not all of YouTube: only what
    gets collected. 0.6 is a reasonable start; raise it if matches are loose."""
    from application import topic_watch as tw
    return tw.add_topic(text, threshold=threshold, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def list_watched_topics() -> list:
    """Your watched topics: id, text, threshold, paused. Their matches are
    list_events(kind='topic_match')."""
    from application import topic_watch as tw
    return tw.list_topics(user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=True,
    idempotent_hint=True, open_world_hint=False))
def unwatch_topic(topic_id: int) -> dict:
    """Stop watching a topic (its past matches stay in the alert feed)."""
    from application import topic_watch as tw
    return tw.remove_topic(topic_id, user_id=_uid())


# --------------------------------------------------------- thumbnails (plan 13)

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def similar_thumbnails(video_id: str, niche: str = None, limit: int = 12,
                       exclude_same_channel: bool = False) -> dict:
    """Videos whose THUMBNAIL looks most like this one's (CLIP image vectors,
    cosine; HNSW in pgvector): who else packages a video like this outlier,
    and whether a look is already overused. Compares style and content, not
    the words on the thumbnail. Needs thumbnail vectors (WORKER_THUMB_EMBED or
    embed_thumbnails). Zero quota."""
    from application import thumbnail_search as ts
    return ts.similar_thumbnails(video_id, niche=niche, limit=limit,
                                 exclude_same_channel=exclude_same_channel)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def search_thumbnails(query: str, niche: str = None, limit: int = 12) -> dict:
    """Find thumbnails by a short visual description ("red arrow, shocked
    face", "dark map with a glowing route") -- CLIP puts text and images in
    one space. English works best. The text model (~0.25 GB) downloads on
    first use. Zero quota."""
    from application import thumbnail_search as ts
    return ts.search_thumbnails(query, niche=niche, limit=limit)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def thumbnail_styles(niche: str, k: int = None) -> dict:
    """The visual styles of a niche's thumbnails (k-means over their CLIP
    vectors) and how each performs: videos, share, median outlier score and
    views, best examples -- "which look works here". Needs 12+ thumbnail
    vectors in the niche. A correlation, not a cause. Zero quota."""
    from application import thumbnail_search as ts
    return ts.thumbnail_styles(niche, k=k)


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True))
def embed_thumbnails(limit: int = 200, niche: str = None) -> dict:
    """Turn up to `limit` thumbnails without a vector (newest first, or one
    niche's) into CLIP vectors -- what WORKER_THUMB_EMBED does in the
    background. Downloads thumbnails from i.ytimg.com (not the Data API, zero
    quota; plan-05 archived images are reused), keeps only the vector. The
    image model (~0.34 GB) downloads on first use; ~1 s per thumbnail."""
    from application import thumbnail_search as ts
    return ts.embed_thumbnails(limit=limit, niche=niche)


# --------------------------------------------------------- own channels (plan 14)

@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def own_channels() -> dict:
    """Your own channels connected through Google OAuth (dashboard: "Мои
    каналы" -> connect) with their real last-28-day numbers from YouTube
    Analytics: views, revenue, RPM, median retention. `status` says whether
    OAuth is configured and what is missing. Impressions and thumbnail CTR are
    not in the Analytics API. Zero Data API quota."""
    from application import own_channels as own
    return {"status": own.status(user_id=_uid()), **own.list_channels(user_id=_uid())}


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def own_vs_niche(channel_id: str, niche: str) -> dict:
    """Your connected channel's videos (real lifetime views and retention)
    against the videos of a niche we collected: median views both sides, the
    ratio, the share of your videos above the niche median, your top videos
    with their RPM."""
    from application import own_channels as own
    return own.own_vs_niche(channel_id, niche, user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def rpm_calibration() -> dict:
    """Your real 28-day RPM (revenue per 1,000 views, as in YouTube Studio)
    next to the low / mid / high range niche-finder estimates for the same
    channel from public data -- below, inside or above, and real/mid.
    Unknown without the monetary scope or on an unmonetized channel."""
    from application import own_channels as own
    return own.rpm_calibration(user_id=_uid())


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True))
def sync_own_channels(channel_id: str = None) -> dict:
    """Pull fresh YouTube Analytics numbers for your connected channels (or
    one): per video, the last 28 days and lifetime, ending 3 days ago (the
    Analytics lag). Uses the Analytics API quota of your own OAuth client, not
    the Data API key's; the worker does this daily."""
    from application import own_channels as own
    return own.sync(user_id=_uid(), channel_id=channel_id)


# Ready-made scenarios (plan 11) -- registered after every tool they name.
from interfaces.mcp import prompts as _prompts  # noqa: E402

_prompts.register(mcp)


def run():
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport, host=os.environ.get("MCP_HOST", "0.0.0.0"),
                port=int(os.environ.get("MCP_PORT", "8765")))


if __name__ == "__main__":
    run()

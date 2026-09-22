"""HTTP API для дашборда. Тонкая обёртка над теми же модулями, что и MCP-сервер --
никакой логики здесь нет, только маршруты и раздача статики фронта.

    uvicorn api:app --host 127.0.0.1 --port 8080
    docker compose up -d web

Разделение по методам осмысленное, а не косметическое: GET ничего не стоит и
читает локальную базу, POST тратит квоту YouTube. Сервис слушает только
127.0.0.1 -- внутри лежит ваш API-ключ, наружу его выставлять незачем.
"""
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from infrastructure.categories import repository as C
from application import collecting as collector
from application import search as Q
from application import discovery as trends
from application import channel_tracking as T
from application import inspection as I
from application import library as L
from application import metadata_review as MR
from application import alerts as AL
from application import tags as TG
from application import enrichment as EN

API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR")
                    or (Path(__file__).parent.parent.parent.parent / "frontend"))
# NOTE: this module now lives two directories deeper than the old flat
# api.py (backend/interfaces/http/ vs backend/), so the fallback path -- only
# used when FRONTEND_DIR is not set, e.g. `make local-run` -- climbs two extra
# levels to keep resolving to <project root>/frontend. Docker always sets
# FRONTEND_DIR=/frontend explicitly, so this only matters for local runs.

db.init_db()
C.seed_fallback()

app = FastAPI(title="niche-finder", docs_url="/api/docs", openapi_url="/api/openapi.json")

# --------------------------------------------------------------- rate limiting
#
# Сервис слушает только 127.0.0.1, поэтому это не защита от чужого трафика, а
# предохранитель от зациклившегося клиента: расширение или скрипт, ушедший в
# бесконечный ретрай, иначе молча жжёт CPU и коннекты к Postgres.
#
# Дефолт подобран под реальное поведение расширения, а не наугад: оно шлёт до
# 40 id за один /api/inspect/videos с флашем раз в 500 мс (extension/content.js)
# и до четырёх параллельных запросов на панель канала (extension/background.js).
# При быстрой прокрутке выдачи это ~120-200 запросов в минуту, так что 600
# оставляет тройной запас и всё равно ловит цикл, который делает тысячи.
# RATE_LIMIT_PER_MINUTE=0 выключает лимитер совсем.
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "600") or 0)

# client host -> времена запросов за последнюю минуту. На 127.0.0.1 ключей
# всегда один-два, так что чистить словарь целиком незачем.
_rate_hits = defaultdict(deque)


@app.middleware("http")
async def rate_limit(request, call_next):
    """Скользящее окно в одну минуту на /api/*. Статику фронта не трогаем:
    одна загрузка дашборда стоит десятка файлов и съедала бы бюджет."""
    if RATE_LIMIT_PER_MINUTE <= 0 or not request.url.path.startswith("/api/"):
        return await call_next(request)

    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    hits = _rate_hits[client]
    while hits and hits[0] <= now - 60.0:
        hits.popleft()

    if len(hits) >= RATE_LIMIT_PER_MINUTE:
        retry_after = max(1, int(60.0 - (now - hits[0])) + 1)
        return JSONResponse(
            status_code=429,
            content={"detail": f"Больше {RATE_LIMIT_PER_MINUTE} запросов в минуту "
                               f"к /api. Повторите через {retry_after} с или "
                               f"поднимите RATE_LIMIT_PER_MINUTE в .env."},
            headers={"Retry-After": str(retry_after)},
        )

    hits.append(now)
    return await call_next(request)


# Лимитер объявлен ВЫШЕ CORS намеренно: последний добавленный middleware в
# Starlette оказывается внешним, поэтому так CORS оборачивает лимитер и ответ
# 429 тоже уезжает с нужными заголовками -- иначе расширение увидело бы вместо
# честного 429 непрозрачную ошибку CORS.
# Расширение для Chrome ходит сюда со своего origin (chrome-extension://...).
# Service worker с host_permissions обошёлся бы и без CORS, но с заголовками
# запросы можно отлаживать прямо из консоли страницы. Сервис слушает только
# 127.0.0.1, поэтому наружу это ничего не открывает.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["*"],
    allow_headers=["*"],
)


def _need_key():
    if not API_KEY:
        raise HTTPException(
            status_code=428,
            detail="YOUTUBE_API_KEY не задан. Впишите его в .env рядом с "
                   "docker-compose.yml и перезапустите: docker compose up -d web",
        )


@app.exception_handler(yt.QuotaExceeded)
async def _quota_exceeded(request, exc):
    return JSONResponse(status_code=429, content={"error": "QuotaExceeded",
                                                  "detail": str(exc)[:800]})


@app.exception_handler(Exception)
async def _unhandled(request, exc):  # pragma: no cover
    msg = str(exc)
    if API_KEY:
        msg = msg.replace(API_KEY, "<KEY>")
    return JSONResponse(status_code=500, content={"error": type(exc).__name__,
                                                  "detail": msg[:800]})


# ------------------------------------------------------------------ статус

@app.get("/api/health")
def health():
    s = Q.db_stats()
    conn = db.get_conn()
    try:
        calls_today = collector.search_calls_today(conn)
    finally:
        conn.close()
    return {
        "ok": True,
        "hasApiKey": bool(API_KEY),
        "db": s,
        "historyAvailable": bool(s["history_since"]),
        "searchQuota": {
            "callsToday": calls_today,
            "dailyLimit": yt.SEARCH_DAILY_CALL_LIMIT,
            "callsLeft": max(0, yt.SEARCH_DAILY_CALL_LIMIT - calls_today),
        },
    }


@app.get("/api/stats")
def stats():
    return Q.db_stats()


@app.get("/api/coverage")
def coverage(period: str = "24h"):
    return trends.coverage(period)


# ------------------------------------------------------------------ разделы

@app.get("/api/viral")
def viral(period: str = "7d", period_by: str = "published",
          max_subscribers: int = 10000, min_views: int = 10000,
          min_views_per_subscriber: float = 1.0,
          min_outlier_score: float = None, niche: str = None,
          region: str = None, category_id: str = None,
          exclude_shorts: bool = True, only_shorts: bool = False,
          sort_by: str = "viral", limit: int = 24, preset: str = None):
    return trends.viral_videos_small_channels(
        period=period, period_by=period_by, max_subscribers=max_subscribers,
        min_views=min_views, min_views_per_subscriber=min_views_per_subscriber,
        min_outlier_score=min_outlier_score, niche=niche, region=region,
        category_id=category_id, exclude_shorts=exclude_shorts,
        only_shorts=only_shorts, sort_by=sort_by, limit=limit, preset=preset)


@app.get("/api/categories")
def categories(period: str = "7d", period_by: str = "published", niche: str = None,
               region: str = None, rank_by: str = "views",
               exclude_shorts: bool = False, min_videos: int = 1, limit: int = 25):
    return trends.most_popular_categories(
        period=period, period_by=period_by, niche=niche, region=region,
        rank_by=rank_by, exclude_shorts=exclude_shorts, min_videos=min_videos,
        limit=limit)


@app.get("/api/keywords")
def keywords(period: str = "24h", period_by: str = "published", niche: str = None,
             region: str = None, category_id: str = None, source: str = "both",
             sort_by: str = "momentum", min_videos: int = 2, top_n: int = 30):
    return trends.trending_keywords(
        period=period, period_by=period_by, niche=niche, region=region,
        category_id=category_id, source=source, sort_by=sort_by,
        min_videos=min_videos, top_n=top_n)


@app.get("/api/tags/top-by-category")
def top_tags_by_category(period: str = "7d", period_by: str = "published", niche: str = None,
                         region: str = None, exclude_shorts: bool = False,
                         min_videos: int = 3, top_n: int = 15):
    return trends.top_tags_by_category(
        period=period, period_by=period_by, niche=niche, region=region,
        exclude_shorts=exclude_shorts, min_videos=min_videos, top_n=top_n)


@app.get("/api/outlier-channels")
def outlier_channels(period: str = "24h", period_by: str = "discovered",
                     min_multiplier: float = 2.0, max_subscribers: int = None,
                     min_subscribers: int = None,
                     niche: str = None, limit: int = 25):
    return T.recently_added_outlier_channels(
        period=period, period_by=period_by, min_multiplier=min_multiplier,
        max_subscribers=max_subscribers, min_subscribers=min_subscribers,
        niche=niche, limit=limit)


@app.get("/api/competition")
def competition(period: str = "30d", niche: str = None, limit: int = 15):
    return T.high_future_competition(period=period, niche=niche, limit=limit)


@app.get("/api/search")
def search(query: str = None, niche: str = None, period: str = "all",
           min_outlier_score: float = 0.0, max_subscribers: int = None,
           exclude_shorts: bool = False, only_shorts: bool = False,
           min_video_length: int = None, max_video_length: int = None,
           min_rpm: float = None, max_rpm: float = None,
           sort_by: str = "outlier", limit: int = 30):
    return {"results": Q.search_outliers(
        query=query or None, niche=niche, period=period,
        min_outlier_score=min_outlier_score, max_subscribers=max_subscribers,
        exclude_shorts=exclude_shorts, only_shorts=only_shorts,
        min_video_length=min_video_length, max_video_length=max_video_length,
        min_rpm=min_rpm, max_rpm=max_rpm,
        sort_by=sort_by, limit=limit)}


@app.post("/api/ideas/check")
def check_ideas(payload: dict = Body(...)):
    """Stage 17: batch verdicts (free/recent/proven/flopped) for a list of
    content ideas, against whatever is already collected locally."""
    try:
        return Q.check_ideas(
            payload.get("ideas") or [], niche=payload.get("niche"),
            min_similarity=payload.get("minSimilarity", 0.55),
            recent_days=payload.get("recentDays", 90),
            proven_outlier=payload.get("provenOutlier", 2.0),
            flop_outlier=payload.get("flopOutlier", 0.5))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/overview")
def overview(period: str = "24h", niche: str = None):
    """Всё для главной одним запросом -- иначе страница делает шесть.

    Периоды разные намеренно: 24h для того, что действительно обновляется за
    сутки, и более широкие окна для срезов, которым нужна статистика.
    """
    wide = "30d" if period in ("1h", "6h", "24h", "48h") else period
    return {
        "period": period,
        "widePeriod": wide,
        "coverage": trends.coverage(period),
        "stats": Q.db_stats(),
        "outlierChannels": T.recently_added_outlier_channels(
            period=period, period_by="discovered", min_multiplier=1.5,
            niche=niche, limit=6),
        "competition": T.high_future_competition(period=wide, niche=niche, limit=6),
        "keywords": trends.trending_keywords(
            period=period, niche=niche, min_videos=2, top_n=12, sort_by="trend"),
        "categories": trends.most_popular_categories(
            period=period, niche=niche, rank_by="channels", min_videos=1, limit=8),
        "viral": trends.viral_videos_small_channels(
            period=period, period_by="discovered", niche=niche,
            max_subscribers=100000, min_views=1000,
            min_views_per_subscriber=0.5, limit=8),
    }


# ------------------------------------------------------------------ каналы

@app.get("/api/niches")
def niches():
    return {"niches": Q.list_niches()}


@app.get("/api/niches/{slug}")
def niche_detail(slug: str, period: str = "all", top_n: int = 5):
    return Q.niche_overview(slug, period=period, top_n=top_n)


@app.get("/api/niches/{slug}/videos")
def niche_videos(slug: str, period: str = "all", channels: str = None,
                 include_shorts: bool = True):
    """Flat per-video list for the stage 15 scatter chart. channels is a
    comma-separated list of channel_id to narrow the niche to (frontend
    channel filter); omit for every channel in the niche."""
    channel_ids = [c for c in (channels or "").split(",") if c] or None
    return Q.niche_videos(slug, period=period, channel_ids=channel_ids,
                          include_shorts=include_shorts)


@app.get("/api/channels/tracked")
def tracked(faceless: bool = None, content_format: str = None, topic: str = None):
    return {"channels": T.list_tracked(faceless=faceless, content_format=content_format,
                                       topic=topic)}


@app.get("/api/channels/{channel_id}")
def channel(channel_id: str, period: str = "30d"):
    res = T.channel_analytics(channel_id, period=period)
    if not res.get("found"):
        raise HTTPException(status_code=404, detail=res.get("hint", "канал не найден"))
    return res


@app.get("/api/channels/{channel_id}/velocity")
def channel_velocity(channel_id: str, period: str = "30d", limit: int = 25):
    return T.channel_velocity(channel_id, period=period, limit=limit)


@app.get("/api/channels/{channel_id}/history")
def channel_history(channel_id: str, limit: int = 400):
    return T.channel_history(channel_id, limit=limit)


@app.get("/api/channels/{channel_id}/similar")
def similar_channels(channel_id: str, niche: str = None, limit: int = 10):
    return Q.similar_channels(channel_id, niche=niche, limit=limit)


@app.get("/api/channels/{channel_id}/niche-overview")
def channel_niche_overview(channel_id: str, period: str = "all", limit: int = 15):
    return Q.niche_overview_from_channel(channel_id, limit=limit, period=period)


@app.get("/api/videos/{video_id}/similar")
def similar_videos(video_id: str, niche: str = None, limit: int = 10,
                   exclude_same_channel: bool = False):
    return Q.similar_videos(video_id, niche=niche, limit=limit,
                            exclude_same_channel=exclude_same_channel)


# ------------------------------------------------------------ swipe file

@app.get("/api/saved")
def saved_items(kind: str = None, folder: str = None, limit: int = 200):
    return {"items": L.list_items(kind=kind, folder=folder, limit=limit)}


@app.get("/api/saved/folders")
def saved_folders():
    return {"folders": L.list_folders()}


@app.post("/api/saved")
def save_item(payload: dict = Body(...)):
    try:
        return L.save_item(payload.get("kind"), payload.get("refId") or payload.get("ref_id"),
                           payload=payload.get("payload"), note=payload.get("note"),
                           folder=payload.get("folder"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/saved/{item_id}")
def delete_saved_item(item_id: int):
    return L.delete_item(item_id)


# ------------------------------------------------------------ video tags (16)

@app.get("/api/tags")
def list_video_tags(niche: str = None, video_id: str = None):
    try:
        return {"tags": TG.list_video_tags(niche=niche, video_id=video_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/tags")
def tag_videos(payload: dict = Body(...)):
    try:
        return TG.tag_videos(payload.get("items") or [], source=payload.get("source"),
                             replace=bool(payload.get("replace", False)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/tags/stats")
def tag_stats(niche: str, tag_group: str, outlier_threshold: float = 3.0,
             exclude_recent_days: int = 30):
    return TG.tag_stats(niche, tag_group, outlier_threshold=outlier_threshold,
                        exclude_recent_days=exclude_recent_days)


@app.get("/api/tags/proposed")
def proposed_tags(niche: str):
    return {"proposed": TG.list_proposed_tags(niche)}


@app.post("/api/tags/proposed/resolve")
def resolve_proposed_tag(payload: dict = Body(...)):
    return TG.resolve_proposed_tag(payload.get("videoId") or payload.get("video_id"),
                                   payload.get("tagGroup") or payload.get("tag_group"),
                                   payload.get("tag"), bool(payload.get("accept")))


# ------------------------------------------------------------ AI enrichment (03)

@app.post("/api/enrich/channels")
def enrich_channels(limit: int = 50):
    return EN.classify_channels(limit=limit)


@app.post("/api/enrich/videos")
def enrich_videos(limit: int = 100):
    return EN.tag_new_videos(limit=limit)


# ---------------------------------------------------- metadata review (8.8)

@app.post("/api/metadata/review")
def metadata_review(payload: dict = Body(...)):
    return MR.review_metadata(
        payload.get("title") or "",
        description=payload.get("description") or "",
        tags=payload.get("tags") or [],
        niche=payload.get("niche"),
        channel_id=payload.get("channelId") or payload.get("channel_id"),
        is_short=bool(payload.get("isShort") or payload.get("is_short") or False),
        period=payload.get("period") or "180d",
    )


@app.post("/api/drafts")
def create_draft(payload: dict = Body(...)):
    return MR.save_draft(
        payload.get("title") or "",
        description=payload.get("description") or "",
        tags=payload.get("tags") or [],
        niche=payload.get("niche"),
        channel_id=payload.get("channelId") or payload.get("channel_id"),
        is_short=bool(payload.get("isShort") or payload.get("is_short") or False),
        review=payload.get("review"),
    )


@app.get("/api/drafts")
def get_drafts(channel_id: str = None, unpublished_only: bool = False, limit: int = 100):
    return {"drafts": MR.list_drafts(channel_id=channel_id,
                                     unpublished_only=unpublished_only, limit=limit)}


@app.post("/api/drafts/{draft_id}/link")
def link_draft(draft_id: int, payload: dict = Body(...)):
    video_id = payload.get("videoId") or payload.get("video_id")
    if not video_id:
        raise HTTPException(status_code=400, detail="videoId required")
    return MR.link_draft(draft_id, video_id)


@app.get("/api/drafts/outcomes")
def draft_outcomes(min_age_days: float = 7.0):
    return {"outcomes": MR.draft_outcomes(min_age_days=min_age_days)}


# ------------------------------------------------------------- alerts (8.9)

@app.get("/api/events")
def get_events(unseen_only: bool = False, kind: str = None, limit: int = 100):
    return {"events": AL.list_events(unseen_only=unseen_only, kind=kind, limit=limit),
           "unseenCount": AL.unseen_count()}


@app.post("/api/events/seen")
def mark_events_seen(payload: dict = Body(default={})):
    ids = payload.get("ids")
    all_unseen = bool(payload.get("all") or not ids)
    return AL.mark_seen(ids=ids, all_unseen=all_unseen)


@app.post("/api/events/scan")
def scan_events():
    """Manual trigger -- the worker already runs this on WORKER_ALERTS_INTERVAL_MIN,
    this is for "check right now" from the dashboard/popup without waiting."""
    return AL.scan()


@app.get("/api/title-changes")
def title_changes(period: str = "7d", channel_id: str = None, limit: int = 50):
    return T.title_changes(period=period, channel_id=channel_id, limit=limit)


@app.get("/api/best-time")
def best_time(niche: str = None, channel_id: str = None, period: str = "90d",
              min_samples: int = 2, timezone_offset_hours: int = 0):
    return T.best_time_to_publish(niche=niche, channel_id=channel_id, period=period,
                                  min_samples=min_samples,
                                  timezone_offset_hours=timezone_offset_hours)


@app.get("/api/title-patterns")
def title_patterns(niche: str = None, channel_id: str = None, period: str = "90d",
                   min_videos: int = 3, top_n: int = 20):
    return T.title_patterns(niche=niche, channel_id=channel_id, period=period,
                            min_videos=min_videos, top_n=top_n)


# ------------------------------------------- операции, которые тратят квоту

@app.post("/api/collect/channel")
def collect_channel(payload: dict = Body(...)):
    _need_key()
    ref = (payload.get("channel") or "").strip()
    if not ref:
        raise HTTPException(status_code=400, detail="нужно поле channel")
    res = collector.collect_channel(API_KEY, ref,
                                    max_videos=int(payload.get("max_videos", 100)),
                                    niche=payload.get("niche") or None)
    if res.get("error"):
        raise HTTPException(status_code=404, detail=res["error"])
    if payload.get("track"):
        T.track(res["channelId"], payload.get("note"))
        res["tracked"] = True
    return res


@app.post("/api/collect/niche")
def collect_niche(payload: dict = Body(...)):
    _need_key()
    q = (payload.get("query") or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="нужно поле query")
    return collector.collect_niche(
        API_KEY, q, label=payload.get("label") or None,
        language=payload.get("language") or None,
        period=payload.get("period") or None,
        region=payload.get("region") or None,
        pages=int(payload.get("pages", 1)))


@app.post("/api/refresh")
def refresh(payload: dict = Body(default={})):
    _need_key()
    stats_res = collector.refresh_stats(API_KEY, scope=payload.get("scope", "recent"),
                                        period=payload.get("period", "30d"),
                                        limit=int(payload.get("limit", 1000)))
    chan_res = collector.refresh_channels(API_KEY, only_tracked=True)
    return {"videos": stats_res, "channels": chan_res}


@app.post("/api/videos/{video_id}/comments")
def video_comments(video_id: str, payload: dict = Body(default={})):
    _need_key()
    return collector.video_comments(
        API_KEY, video_id, max_results=int(payload.get("max_results", 100)),
        order=payload.get("order", "relevance"))


@app.post("/api/videos/{video_id}/insights")
def comment_insights(video_id: str, payload: dict = Body(default={})):
    """Stage 04: by-click only (never automatic) -- costs 1 YouTube quota
    unit plus an LLM call on a cache miss."""
    _need_key()
    return EN.comment_insights(
        API_KEY, video_id, max_comments=int(payload.get("max_comments", 200)),
        force_refresh=bool(payload.get("force_refresh", False)))


@app.get("/api/niches/{slug}/insights")
def niche_comment_insights(slug: str, top_n: int = 5):
    return EN.niche_comment_insights(slug, top_n=top_n)


@app.post("/api/channels/track")
def track(payload: dict = Body(...)):
    raw = (payload.get("channel_id") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="нужно поле channel_id")
    conn = db.get_conn()
    try:
        cid = T.resolve_channel_id(conn, API_KEY or None, raw)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        conn.close()
    return T.track(cid, payload.get("note"))


@app.delete("/api/channels/tracked/{channel_id}")
def untrack(channel_id: str):
    return T.untrack(channel_id)


# ------------------------------------- разбор произвольной страницы YouTube
# Эти три маршрута обслуживают браузерное расширение: пользователь открыл
# случайный ролик, которого может не быть в базе. Сначала смотрим Postgres,
# при промахе добираем 1-2 units и сохраняем — см. application/inspection.py.

@app.get("/api/inspect/video")
def inspect_video(video_id: str, refresh: bool = False, fetch: bool = True):
    return I.inspect_video(API_KEY, video_id, refresh=refresh, fetch=fetch)


@app.get("/api/inspect/channel")
def inspect_channel(ref: str, refresh: bool = False, fetch: bool = True):
    return I.inspect_channel(API_KEY, ref, refresh=refresh, fetch=fetch)


@app.post("/api/inspect/videos")
def inspect_videos(payload: dict = Body(...)):
    ids = payload.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids должен быть списком")
    return I.inspect_videos(API_KEY, ids, fetch=bool(payload.get("fetch", True)))


# ---------------------------------------------------------------- статика

@app.middleware("http")
async def _no_cache_static(request, call_next):
    """Локальный дашборд правят на живую, поэтому кэш браузера тут только мешает:
    поправил styles.css — перезагрузил страницу и сразу видишь результат."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:  # pragma: no cover
    @app.get("/")
    def _no_frontend():
        return {"error": f"Папка фронта не найдена: {FRONTEND_DIR}",
                "hint": "Задайте FRONTEND_DIR или смонтируйте ./frontend в контейнер"}

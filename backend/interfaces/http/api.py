"""HTTP API для дашборда. Тонкая обёртка над теми же модулями, что и MCP-сервер --
никакой логики здесь нет, только маршруты и раздача статики фронта.

    uvicorn api:app --host 127.0.0.1 --port 8080
    docker compose up -d web

Разделение по методам осмысленное, а не косметическое: GET ничего не стоит и
читает локальную базу, POST тратит квоту YouTube. Сервис слушает только
127.0.0.1 -- внутри лежит ваш API-ключ, наружу его выставлять незачем.
От чужих сайтов в браузере прикрывает local_only_guard: проверка Host и
обязательный JSON или X-NF-Client у POST/PUT/PATCH/DELETE.
"""
import hmac
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from application import alerts as AL
from application import auth as AUTH
from application import briefs as BR
from application import channel_tracking as T
from application import collecting as collector
from application import content_gaps as CG
from application import digest as DG
from application import discovery as trends
from application import enrichment as EN
from application import hook_score as HK
from application import inspection as I
from application import library as L
from application import maturity_curve
from application import metadata_review as MR
from application import niche_clusters as NCL
from application import niche_export as NE
from application import notify_settings as NS
from application import own_channels as OWN
from application import packaging as PKG
from application import repeatability as RP
from application import saturation as SAT
from application import search as Q
from application import sponsors as SP
from application import tags as TG
from application import template_risk as TRK
from application import thumbnail_search as TS
from application import topic_watch as TW
from application import trajectory as TJ
from application import transcripts as TR
from domain.users import LOCAL_USER_ID, multi_user_enabled
from infrastructure import quota_owner
from infrastructure.categories import repository as C

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
    # plan 15 (5.10): a signed-in user has their own window, so several people
    # behind one address do not throttle each other (sign-in itself stays
    # limited per address, which also slows password guessing)
    user = getattr(request.state, "user", None)
    if user:
        client = f"user:{user['id']}"
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


# ------------------------------------------------ вход (план 15)
#
# NF_MULTI_USER=1: без живой сессии /api отвечает 401. Сессия -- случайный
# токен в cookie nf_session (HttpOnly, SameSite=Strict); в базе только его
# SHA-256. Открыты без входа: сам вход и «кто я», /api/health (healthcheck
# контейнера) и возврат из Google OAuth (пользователя там называет одноразовый
# state, а Strict-cookie при переходе с чужого сайта не приходит). Middleware
# стоит внутри защиты Host: чужое имя получает 400 раньше, чем 401, и ответы
# 401 тоже уезжают с заголовками CORS. Без NF_MULTI_USER всё как раньше:
# пользователь 1, без входа.

AUTH_COOKIE = "nf_session"
_PUBLIC_API = frozenset({"/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/health",
                         "/api/own/oauth/callback"})


@app.middleware("http")
async def session_guard(request, call_next):
    request.state.user_id = LOCAL_USER_ID
    request.state.user = None
    if not multi_user_enabled():
        return await call_next(request)
    token = request.cookies.get(AUTH_COOKIE, "")
    user = await run_in_threadpool(AUTH.user_for_token, token) if token else None
    request.state.via = "session" if user else None
    if not user:
        # plan 15 (5.7): the extension (and scripts) send a personal API token
        # instead of the cookie; bearer auth is not sent by browsers on their
        # own, so it carries no CSRF risk
        auth = request.headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            user = await run_in_threadpool(AUTH.user_for_api_token, auth[7:].strip())
            request.state.via = "token" if user else None
    if user:
        request.state.user_id, request.state.user = user["id"], user
    path = request.url.path
    if (path.startswith("/api/") and path not in _PUBLIC_API and not user
            and request.method != "OPTIONS"):
        return JSONResponse(status_code=401,
                            content={"detail": "Sign in first: NF_MULTI_USER is on -- "
                                               "войдите в дашборде."})
    # plan 15 (5.5): YouTube calls made for this request count against the
    # signed-in user's daily budget (infrastructure/quota_owner.py)
    owner = quota_owner.set_owner(user["id"]) if user else None
    try:
        return await call_next(request)
    finally:
        if owner is not None:
            quota_owner.reset(owner)


# ------------------------------------------------ защита от чужих сайтов
#
# Аутентификации нет, а 127.0.0.1 доступен любой открытой в браузере вкладке.
# Две дыры, которые это открывает, и чем они закрыты:
#
# 1. DNS rebinding: чужой домен начинает резолвиться в 127.0.0.1, и его
#    страница читает /api/* как свой origin. Браузер при этом шлёт Host с
#    чужим именем -- отвечаем 400 на всё, чего нет в ALLOWED_HOSTS.
# 2. "Простой" cross-origin POST (form/fetch без своих заголовков, text/plain)
#    уходит без CORS-preflight. Тела с JSON FastAPI и так отсекает по
#    content-type, но маршруты, которые берут параметры только из query
#    (enrich, events/scan, reindex, recompute), выполнились бы. Поэтому любой
#    изменяющий запрос обязан нести Content-Type JSON или X-NF-Client: оба
#    заставляют браузер сначала спросить preflight, а его CORS пропускает
#    только для chrome-extension://.
#
# Своё middleware, а не starlette TrustedHostMiddleware: тот режет Host по
# первому ":" и ломает [::1]:8080, отвечает plain text вместо нашего JSON с
# detail и фиксирует список при создании (тесты не могут его подменить).

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]",
                # имя сервиса и container_name из docker-compose.yml -- так web
                # видят другие контейнеры в сети niche-finder_default
                "web", "niche-finder-web")


def _host_name(value):
    """Имя из заголовка Host без порта, в нижнем регистре; "" -- если заголовок
    кривой (порт не число, незакрытая скобка IPv6)."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return ""
        name, rest = value[:end + 1], value[end + 1:]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return ""
        return name
    name, sep, port = value.partition(":")
    if sep and not port.isdigit():
        return ""
    return name


def _allowed_hosts(extra):
    """Локальные имена плюс NF_ALLOWED_HOSTS (через запятую, порт можно
    указывать -- он отбрасывается)."""
    hosts = set(_LOCAL_HOSTS)
    for item in (extra or "").split(","):
        name = _host_name(item)
        if name:
            hosts.add(name)
    return hosts


ALLOWED_HOSTS = _allowed_hosts(os.environ.get("NF_ALLOWED_HOSTS"))
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _is_json(content_type):
    media = (content_type or "").split(";", 1)[0].strip().lower()
    return media == "application/json" or (
        media.startswith("application/") and media.endswith("+json"))


@app.middleware("http")
async def local_only_guard(request, call_next):
    host = request.headers.get("host")
    if _host_name(host) not in ALLOWED_HOSTS:
        shown = (host or "")[:100]
        return JSONResponse(
            status_code=400,
            content={"detail": f"Недопустимый заголовок Host: {shown!r}. API отвечает "
                               f"только на 127.0.0.1, localhost и [::1]; другое имя "
                               f"добавьте в NF_ALLOWED_HOSTS в .env."})
    if (request.method in _UNSAFE_METHODS
            and not _is_json(request.headers.get("content-type"))
            and not request.headers.get("x-nf-client", "").strip()):
        return JSONResponse(
            status_code=403,
            content={"detail": "Изменяющий запрос без Content-Type: application/json "
                               "и без заголовка X-NF-Client отклонён -- так выглядит "
                               "запрос с чужого сайта. Добавьте заголовок "
                               "X-NF-Client: <имя клиента>."})
    return await call_next(request)


# Лимитер и защита объявлены ВЫШЕ CORS намеренно: последний добавленный
# middleware в Starlette оказывается внешним, поэтому так CORS оборачивает их
# обоих и ответы 429/403/400 тоже уезжают с нужными заголовками -- иначе
# расширение увидело бы вместо честной ошибки непрозрачную ошибку CORS. По той
# же причине preflight (OPTIONS) CORS отвечает сам и до защиты он не доходит.
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
        units_today = collector.units_today(conn)
        curve = maturity_curve.status(conn)
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
        "unitQuota": {
            "unitsToday": units_today,
            "dailyLimit": yt.DAILY_UNIT_LIMIT,
            "unitsLeft": max(0, yt.DAILY_UNIT_LIMIT - units_today),
        },
        "maturityCurve": curve,
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
             sort_by: str = "momentum", min_videos: int = 2, top_n: int = 30,
             keywords_mode: str = "ngram", semantic_similarity: float = 0.85):
    return trends.trending_keywords(
        period=period, period_by=period_by, niche=niche, region=region,
        category_id=category_id, source=source, sort_by=sort_by,
        min_videos=min_videos, top_n=top_n, keywords_mode=keywords_mode,
        semantic_similarity=semantic_similarity)


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
                     niche: str = None, limit: int = 25, min_ypp_status: str = None):
    try:
        return T.recently_added_outlier_channels(
            period=period, period_by=period_by, min_multiplier=min_multiplier,
            max_subscribers=max_subscribers, min_subscribers=min_subscribers,
            niche=niche, limit=limit, min_ypp_status=min_ypp_status or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/competition")
def competition(period: str = "30d", niche: str = None, limit: int = 15):
    return T.high_future_competition(period=period, niche=niche, limit=limit)


@app.get("/api/search")
def search(query: str = None, niche: str = None, period: str = "all",
           min_outlier_score: float = 0.0, max_subscribers: int = None,
           exclude_shorts: bool = False, only_shorts: bool = False,
           min_video_length: int = None, max_video_length: int = None,
           min_rpm: float = None, max_rpm: float = None,
           sort_by: str = "outlier", limit: int = 30, min_ypp_status: str = None):
    try:
        return {"results": Q.search_outliers(
            query=query or None, niche=niche, period=period,
            min_outlier_score=min_outlier_score, max_subscribers=max_subscribers,
            exclude_shorts=exclude_shorts, only_shorts=only_shorts,
            min_video_length=min_video_length, max_video_length=max_video_length,
            min_rpm=min_rpm, max_rpm=max_rpm,
            sort_by=sort_by, limit=limit, min_ypp_status=min_ypp_status or None)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@app.get("/api/niches/saturation")
def niches_saturation():
    """Plan 08: every niche's trend (growing / stable / cooling / saturated /
    insufficient-data) in one pass, for the niche list. Zero quota. Declared
    before /api/niches/{slug} so "saturation" is not taken for a slug."""
    return SAT.all_niches_saturation()


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


@app.get("/api/niches/{slug}/sponsors")
def niche_sponsors(slug: str, period: str = "all", top_n: int = 10):
    """Sponsor map (plan 09): share of the niche's videos with a named sponsor,
    top brands, affiliate brands apart. A lower bound -- descriptions only."""
    try:
        return SP.sponsor_map(slug, period=period, top_n=top_n)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/channels/tracked")
def tracked(request: Request, faceless: bool = None, content_format: str = None,
            topic: str = None):
    return {"channels": T.list_tracked(faceless=faceless, content_format=content_format,
                                       topic=topic, user_id=_uid(request))}


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


@app.get("/api/channels/{channel_id}/sponsors")
def channel_sponsors(channel_id: str, period: str = "all", top_n: int = 10):
    try:
        return SP.channel_sponsors(channel_id, period=period, top_n=top_n)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/videos/{video_id}/similar")
def similar_videos(video_id: str, niche: str = None, limit: int = 10,
                   exclude_same_channel: bool = False):
    return Q.similar_videos(video_id, niche=niche, limit=limit,
                            exclude_same_channel=exclude_same_channel)


# ------------------------------------------------------------ swipe file

@app.get("/api/saved")
def saved_items(request: Request, kind: str = None, folder: str = None, limit: int = 200):
    return {"items": L.list_items(kind=kind, folder=folder, limit=limit, user_id=_uid(request))}


@app.get("/api/saved/folders")
def saved_folders(request: Request):
    return {"folders": L.list_folders(user_id=_uid(request))}


@app.post("/api/saved")
def save_item(request: Request, payload: dict = Body(...)):
    try:
        return L.save_item(payload.get("kind"), payload.get("refId") or payload.get("ref_id"),
                           payload=payload.get("payload"), note=payload.get("note"),
                           folder=payload.get("folder"), user_id=_uid(request))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/saved/{item_id}")
def delete_saved_item(request: Request, item_id: int):
    out = L.delete_item(item_id, user_id=_uid(request))
    if "deleted" in out and out["deleted"] is None:     # not this user's item
        raise HTTPException(status_code=404, detail="saved item not found")
    return out


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
def create_draft(request: Request, payload: dict = Body(...)):
    return MR.save_draft(
        payload.get("title") or "",
        description=payload.get("description") or "",
        tags=payload.get("tags") or [],
        niche=payload.get("niche"),
        channel_id=payload.get("channelId") or payload.get("channel_id"),
        is_short=bool(payload.get("isShort") or payload.get("is_short") or False),
        review=payload.get("review"),
        user_id=_uid(request),
    )


@app.get("/api/drafts")
def get_drafts(request: Request, channel_id: str = None, unpublished_only: bool = False,
               limit: int = 100):
    return {"drafts": MR.list_drafts(channel_id=channel_id, unpublished_only=unpublished_only,
                                     limit=limit, user_id=_uid(request))}


@app.post("/api/drafts/{draft_id}/link")
def link_draft(request: Request, draft_id: int, payload: dict = Body(...)):
    video_id = payload.get("videoId") or payload.get("video_id")
    if not video_id:
        raise HTTPException(status_code=400, detail="videoId required")
    try:
        return MR.link_draft(draft_id, video_id, user_id=_uid(request))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/drafts/outcomes")
def draft_outcomes(request: Request, min_age_days: float = 7.0):
    return {"outcomes": MR.draft_outcomes(min_age_days=min_age_days, user_id=_uid(request))}


# ------------------------------------------------------------- alerts (8.9)

@app.get("/api/events")
def get_events(request: Request, unseen_only: bool = False, kind: str = None, limit: int = 100):
    uid = _uid(request)
    return {"events": AL.list_events(unseen_only=unseen_only, kind=kind, limit=limit, user_id=uid),
           "unseenCount": AL.unseen_count(user_id=uid)}


@app.post("/api/events/seen")
def mark_events_seen(request: Request, payload: dict = Body(default={})):
    ids = payload.get("ids")
    all_unseen = bool(payload.get("all") or not ids)
    return AL.mark_seen(ids=ids, all_unseen=all_unseen, user_id=_uid(request))


@app.post("/api/events/scan")
def scan_events():
    """Manual trigger -- the worker already runs this on WORKER_ALERTS_INTERVAL_MIN,
    this is for "check right now" from the dashboard/popup without waiting."""
    return AL.scan()


# ------------------------------------------------------ topic alerts (plan 19)

@app.get("/api/topics")
def get_topics(request: Request):
    return {"topics": TW.list_topics(user_id=_uid(request)), "note": TW.NOTE}


@app.post("/api/topics")
def add_topic(request: Request, payload: dict = Body(default={})):
    """Watch a topic: a new video whose embedding is close enough raises a
    personal topic_match alert. Zero quota."""
    try:
        return TW.add_topic(payload.get("text"), threshold=float(
            payload.get("threshold") or TW.DEFAULT_THRESHOLD), user_id=_uid(request))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/topics/{topic_id}/pause")
def pause_topic(request: Request, topic_id: int, payload: dict = Body(default={})):
    try:
        return TW.set_paused(topic_id, bool(payload.get("paused", True)), user_id=_uid(request))
    except ValueError:
        raise HTTPException(status_code=404, detail="topic not found")


@app.delete("/api/topics/{topic_id}")
def delete_topic(request: Request, topic_id: int):
    out = TW.remove_topic(topic_id, user_id=_uid(request))
    if not out["removed"]:
        raise HTTPException(status_code=404, detail="topic not found")
    return out


@app.get("/api/videos/trajectory")
def videos_trajectory(ids: str):
    """Plan 20: views by age for up to 5 comma-separated video ids, each with
    its channel's expected curve. Zero quota."""
    try:
        return TJ.video_trajectory(ids.split(","))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/videos/{video_id}/repeatability")
def video_repeatability(video_id: str, min_similarity: float = RP.DEFAULT_MIN_SIMILARITY,
                        niche: str = None):
    """Plan 21: did this video's format work for other channels too? Zero quota."""
    return RP.format_repeatability(video_id, min_similarity=min_similarity, niche=niche)


@app.get("/api/title-changes")
def title_changes(period: str = "7d", channel_id: str = None, limit: int = 50):
    return T.title_changes(period=period, channel_id=channel_id, limit=limit)


@app.get("/api/packaging")
def packaging_feed(period: str = "30d", channel_id: str = None, field: str = None,
                   limit: int = 50):
    """Repackaging feed (plan 05): title and thumbnail swaps with before/after
    and the views-per-hour effect. Zero quota."""
    try:
        return PKG.packaging_feed(period=period, channel_id=channel_id, field=field,
                                  limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/digest")
def digest_preview(request: Request, period: str = "24h", top_n: int = 5):
    """What the daily digest (plan 07) would contain right now -- read-only."""
    return DG.build_digest(period=period, top_n=top_n, user_id=_uid(request))


@app.post("/api/digest/send")
def digest_send(request: Request):
    """Send the digest now, ignoring DIGEST_HOUR and "already sent today" --
    for checking the Telegram/webhook setup. To the caller's own notifier."""
    return DG.send_digest(force=True, user_id=_uid(request))


# ------------------------------------------------------ notification settings (plan 15, 5.9)

@app.get("/api/settings/notifications")
def notification_settings(request: Request):
    """Whether Telegram/webhook are set and the mode -- never the secrets."""
    return NS.get(_uid(request))


@app.put("/api/settings/notifications")
def save_notification_settings(request: Request, payload: dict = Body(...)):
    try:
        return NS.save(_uid(request),
                       telegram_bot_token=payload.get("telegramBotToken"),
                       telegram_chat_id=payload.get("telegramChatId"),
                       webhook_url=payload.get("webhookUrl"),
                       mode=payload.get("mode"), clear=bool(payload.get("clear")))
    except NS.SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/settings/notifications/test")
def test_notification(request: Request):
    notifier = NS.notifier_for(_uid(request))
    if NS.target_name(_uid(request)) == "none":
        raise HTTPException(status_code=409, detail="no Telegram or webhook set up yet")
    return {"sent": bool(notifier.send("\U0001F9EA niche-finder: тестовое сообщение. "
                                       "Если вы это видите, уведомления настроены верно."))}


@app.post("/api/briefs")
def make_brief(request: Request, payload: dict = Body(...)):
    """Outlier -> brief (plan 02). save=false previews without writing anything;
    save=true also stores a draft linked to the source video. Zero quota."""
    video_id = payload.get("videoId") or payload.get("video_id")
    if not video_id:
        raise HTTPException(status_code=400, detail="videoId required")
    brief = BR.build_brief(
        video_id, niche=payload.get("niche"),
        use_llm=payload.get("useLlm", payload.get("use_llm", True)),
        save=payload.get("save", True),
        gap_topic=payload.get("gapTopic", payload.get("gap_topic")),
        user_id=_uid(request))
    if not brief.get("found", True):
        raise HTTPException(status_code=404, detail=brief.get("hint") or "video not found")
    return brief


@app.get("/api/channels/{channel_id}/template-risk")
def channel_template_risk(channel_id: str, last_n: int = 30):
    """How templated a channel's recent uploads look (plan 01) -- a heuristic
    over local data, zero quota."""
    return TRK.template_risk(channel_id, last_n=last_n)


@app.get("/api/niches/{slug}/template-risk")
def niche_template_risk(slug: str, last_n: int = 30, top_n: int = 10):
    return TRK.niche_template_risk(slug, last_n=last_n, top_n=top_n)


@app.get("/api/videos/{video_id}/packaging")
def packaging_history(video_id: str):
    return PKG.packaging_history(video_id)


# ------------------------------------------------------ sign-in (plan 15)

def _cookie_secure(request) -> bool:
    forced = os.environ.get("NF_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")
    return forced or request.url.scheme == "https"


@app.get("/api/auth/me")
def auth_me(request: Request):
    if not multi_user_enabled():
        return {"multiUser": False, "user": {"id": LOCAL_USER_ID, "email": "local", "isAdmin": True}}
    user = request.state.user
    return {"multiUser": True, "user": user,
            "quota": yt.user_usage(user["id"]) if user else None}


@app.post("/api/auth/login")
def auth_login(request: Request, response: Response, payload: dict = Body(...)):
    """Accounts are created by an admin (`cli.py create-user`). The session
    token goes only into the HttpOnly cookie, never into the JSON body."""
    try:
        s = AUTH.login(str(payload.get("email") or ""), str(payload.get("password") or ""))
    except AUTH.AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    response.set_cookie(AUTH_COOKIE, s["token"], max_age=AUTH.SESSION_DAYS * 86400, path="/",
                        httponly=True, samesite="strict", secure=_cookie_secure(request))
    return {"user": AUTH.user_for_token(s["token"]), "expiresAt": s["expiresAt"]}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    AUTH.logout(request.cookies.get(AUTH_COOKIE, ""))
    response.delete_cookie(AUTH_COOKIE, path="/")
    return {"signedOut": True}


def _uid(request) -> int:
    return getattr(request.state, "user_id", LOCAL_USER_ID)


def _session_user(request):
    """Token management needs a real sign-in: a leaked API token must not be
    able to mint more tokens or list them."""
    if not multi_user_enabled():
        raise HTTPException(status_code=409, detail="API tokens exist only with NF_MULTI_USER=1")
    if getattr(request.state, "via", None) != "session":
        raise HTTPException(status_code=403, detail="sign in to the dashboard to manage tokens")
    return request.state.user_id


@app.get("/api/auth/tokens")
def api_tokens_list(request: Request):
    return {"tokens": AUTH.list_api_tokens(_session_user(request))}


@app.post("/api/auth/tokens")
def api_tokens_create(request: Request, payload: dict = Body(default={})):
    """A personal token for the extension or MCP over HTTP, shown ONCE."""
    return AUTH.create_api_token(_session_user(request), payload.get("name"))


@app.delete("/api/auth/tokens/{token_id}")
def api_tokens_revoke(request: Request, token_id: int):
    if not AUTH.revoke_api_token(_session_user(request), token_id):
        raise HTTPException(status_code=404, detail="token not found")
    return {"revoked": token_id}


# ------------------------------------------------------ own channels (plan 14)
# Personal data of the local user. The refresh token never leaves
# application/own_channels.py; nothing below returns or logs it.

def _own_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except OWN.NotConfigured as e:
        raise HTTPException(status_code=428, detail=str(e))
    except OWN.NotConnected:
        raise HTTPException(status_code=404, detail="this channel is not connected")


@app.get("/api/own/status")
def own_status(request: Request):
    return OWN.status(user_id=_uid(request))


OAUTH_STATE_COOKIE = "nf_oauth_state"
_OAUTH_CALLBACK_PATH = "/api/own/oauth/callback"


@app.post("/api/own/connect")
def own_connect(request: Request, response: Response):
    """A Google consent URL; the browser opens it and Google sends the user
    back to /api/own/oauth/callback. The state also goes into a short-lived
    cookie of THIS browser: a consent link opened anywhere else (someone else's
    link, forwarded to a victim) cannot attach a channel to the account that
    started it. Lax, so it rides along on Google's top-level redirect back."""
    # Come back to the loopback name the dashboard is open on (localhost vs
    # 127.0.0.1 are different cookie hosts); the Host is already allow-listed.
    host = request.headers.get("host", "")
    redirect = (f"{request.url.scheme}://{host}{_OAUTH_CALLBACK_PATH}"
                if _host_name(host) in ("127.0.0.1", "localhost", "[::1]") else None)
    out = dict(_own_call(OWN.start_connect, user_id=_uid(request), redirect_uri=redirect))
    state = out.pop("state", None)
    if state:
        response.set_cookie(OAUTH_STATE_COOKIE, state, max_age=OWN.STATE_TTL_MINUTES * 60,
                            path=_OAUTH_CALLBACK_PATH, httponly=True, samesite="lax",
                            secure=_cookie_secure(request))
    return out


@app.get("/api/own/oauth/callback")
def own_oauth_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    """Google's redirect after consent. Always lands on the dashboard's "Мои
    каналы" screen with a short result -- never a stack trace or a token."""
    def done(fragment):
        resp = RedirectResponse(f"/#/own?{fragment}", status_code=303)
        resp.delete_cookie(OAUTH_STATE_COOKIE, path=_OAUTH_CALLBACK_PATH)
        return resp

    if error or not code:
        return done(f"error={quote(error or 'no code from Google', safe='')}")
    started_here = request.cookies.get(OAUTH_STATE_COOKIE, "")
    if not state or not started_here or not hmac.compare_digest(started_here, state):
        return done("error=" + quote("this sign-in link was started in another browser or has "
                                     "expired -- start the connection again from this browser", safe=""))
    try:
        out = OWN.finish_connect(state, code)
    except (OWN.ConnectError, OWN.NotConfigured) as e:
        return done(f"error={quote(str(e)[:200], safe='')}")
    return done(f"connected={quote(out.get('title') or out['channelId'], safe='')}")


@app.get("/api/own/channels")
def own_channels_list(request: Request):
    return OWN.list_channels(user_id=_uid(request))


@app.post("/api/own/sync")
def own_sync(request: Request, channel_id: str = None):
    return _own_call(OWN.sync, user_id=_uid(request), channel_id=channel_id)


@app.get("/api/own/rpm-calibration")
def own_rpm_calibration(request: Request):
    return OWN.rpm_calibration(user_id=_uid(request))


@app.get("/api/own/channels/{channel_id}/vs-niche")
def own_vs_niche(request: Request, channel_id: str, niche: str):
    return _own_call(OWN.own_vs_niche, channel_id, niche, user_id=_uid(request))


@app.delete("/api/own/channels/{channel_id}")
def own_disconnect(request: Request, channel_id: str):
    return _own_call(OWN.disconnect, channel_id, user_id=_uid(request))


@app.get("/api/thumbnails/search")
def search_thumbnails(q: str = "", niche: str = None, limit: int = 12):
    """Plan 13: thumbnails matching a short visual description (CLIP). Zero quota."""
    try:
        return TS.search_thumbnails(q, niche=niche, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/thumbnails/embed")
def embed_thumbnails(limit: int = 200, niche: str = None):
    """Plan 13: vectors for thumbnails that have none -- by click, like the
    WORKER_THUMB_EMBED step. Downloads from i.ytimg.com, zero API quota."""
    return TS.embed_thumbnails(limit=limit, niche=niche)


@app.get("/api/videos/{video_id}/similar-thumbnails")
def similar_thumbnails(video_id: str, niche: str = None, limit: int = 12,
                       exclude_same_channel: bool = False):
    return TS.similar_thumbnails(video_id, niche=niche, limit=limit,
                                 exclude_same_channel=exclude_same_channel)


@app.get("/api/niches/{slug}/thumbnail-styles")
def thumbnail_styles(slug: str, k: int = None):
    return TS.thumbnail_styles(slug, k=k)


@app.get("/api/thumbnails/{video_id}/{captured_at}.jpg")
def thumbnail_image(video_id: str, captured_at: str):
    """An archived thumbnail version -- the only copy of a "before" image once
    YouTube serves the new one at the same URL. Immutable, so cacheable."""
    data = PKG.thumbnail_image(video_id, captured_at)
    if not data:
        raise HTTPException(status_code=404, detail="no archived thumbnail for this time")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


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
def collect_channel(request: Request, payload: dict = Body(...)):
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
        T.track(res["channelId"], payload.get("note"), user_id=_uid(request))
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


@app.get("/api/niches/{slug}/content-gaps")
def niche_content_gaps(slug: str, top_videos: int = 10, limit: int = 20, use_llm: bool = None):
    """Content gaps (plan 03) from cached comments only -- zero quota. Videos
    whose comments were never read are listed in skippedVideos."""
    return CG.content_gaps(API_KEY, slug, top_videos=top_videos, use_llm=use_llm,
                           fetch=False, limit=limit)


@app.post("/api/niches/{slug}/content-gaps")
def fetch_content_gaps(slug: str, payload: dict = Body(default={})):
    """Content gaps, reading the comments of uncached videos first: 1 quota
    unit per such video (plus an LLM call each in LLM mode). By click only."""
    _need_key()
    use_llm = payload.get("useLlm", payload.get("use_llm"))
    try:
        return CG.content_gaps(
            API_KEY, slug,
            top_videos=int(payload.get("topVideos", payload.get("top_videos", 10))),
            use_llm=None if use_llm is None else bool(use_llm), fetch=True,
            limit=int(payload.get("limit", 20)))
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/transcripts/request")
def request_transcript(request: Request, payload: dict = Body(...)):
    video_id = payload.get("videoId") or payload.get("video_id")
    if not video_id:
        raise HTTPException(status_code=400, detail="videoId is required")
    return TR.request_transcript(video_id, reason=payload.get("reason"),
                                 compare_group=payload.get("compareGroup"),
                                 requested_by=payload.get("requestedBy") or "dashboard",
                                 user_id=_uid(request))


@app.get("/api/transcripts/queue")
def transcript_queue(request: Request, status: str = None):
    return {"queue": TR.list_transcript_queue(status=status, user_id=_uid(request))}


@app.post("/api/transcripts/{video_id}/save")
def save_transcript(request: Request, video_id: str, payload: dict = Body(...)):
    text = payload.get("text") or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    return TR.save_transcript(video_id, text, language=payload.get("language"),
                              user_id=_uid(request))


@app.post("/api/transcripts/{video_id}/reindex")
def reindex_transcript(request: Request, video_id: str):
    return TR.reindex_transcript(video_id, user_id=_uid(request))


@app.get("/api/transcripts/search")
def search_transcripts(request: Request, query: str, niche: str = None,
                       compare_group: str = None, k: int = 10):
    try:
        return TR.search_transcripts(query, niche=niche, compare_group=compare_group, k=k,
                                     user_id=_uid(request))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/videos/{video_id}/hook")
def video_hook(video_id: str, niche: str = None, llm: bool = False,
               force_refresh: bool = False):
    return HK.hook_report(video_id, niche=niche or None, llm=llm, force_refresh=force_refresh)


@app.get("/api/niches/{slug}/hook-benchmark")
def niche_hook_benchmark(slug: str):
    return HK.niche_hook_benchmark(slug)


@app.post("/api/hooks/score")
def score_hook_text(payload: dict = Body(...)):
    # the draft travels in the body, never in the URL, and is not stored
    try:
        return HK.score_hook_text(payload.get("text") or "", niche=payload.get("niche") or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/titles/score")
def score_titles(payload: dict = Body(...)):
    try:
        return EN.score_titles(payload.get("candidates") or [],
                               niche_slug=payload.get("niche"),
                               channel_id=payload.get("channelId"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/titles/suggest")
def suggest_titles(payload: dict = Body(...)):
    try:
        return EN.suggest_titles(payload.get("topic"), niche_slug=payload.get("niche"),
                                 channel_id=payload.get("channelId"),
                                 n=int(payload.get("n", 10)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/niche-clusters")
def niche_clusters():
    return NCL.niche_map()


@app.post("/api/niche-clusters/recompute")
def recompute_niche_clusters(k: int = None):
    return NCL.compute_clusters(k=k)


@app.get("/api/niche/{slug}/export.{fmt}")
def export_niche(slug: str, fmt: str):
    try:
        out = NE.export_niche(slug, fmt=fmt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    media_type = "text/tab-separated-values" if fmt == "tsv" else "text/csv"
    return Response(
        content=out["content"], media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{out["filename"]}"'})


@app.get("/api/video/{video_id}/why")
def why_viral(video_id: str, force_refresh: bool = False):
    """Stage 05: 204 (no body) when LLM_PROVIDER=none or the daily budget is
    exhausted -- the plan's contract for "feature is off", distinct from a
    normal empty result."""
    result = EN.explain_outlier(video_id, force_refresh=force_refresh)
    if result.get("hint") and "LLM_PROVIDER" in result["hint"]:
        return Response(status_code=204)
    return result


@app.post("/api/channels/track")
def track(request: Request, payload: dict = Body(...)):
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
    return T.track(cid, payload.get("note"), user_id=_uid(request))


@app.delete("/api/channels/tracked/{channel_id}")
def untrack(request: Request, channel_id: str):
    return T.untrack(channel_id, user_id=_uid(request))


# ------------------------------------- разбор произвольной страницы YouTube
# Эти три маршрута обслуживают браузерное расширение: пользователь открыл
# случайный ролик, которого может не быть в базе. Сначала смотрим Postgres,
# при промахе добираем 1-2 units и сохраняем — см. application/inspection.py.

@app.get("/api/inspect/video")
def inspect_video(request: Request, video_id: str, refresh: bool = False, fetch: bool = True):
    return I.inspect_video(API_KEY, video_id, refresh=refresh, fetch=fetch, user_id=_uid(request))


@app.get("/api/inspect/channel")
def inspect_channel(request: Request, ref: str, refresh: bool = False, fetch: bool = True):
    return I.inspect_channel(API_KEY, ref, refresh=refresh, fetch=fetch, user_id=_uid(request))


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

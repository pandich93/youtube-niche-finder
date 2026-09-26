"""Разбор одного произвольного видео или канала — то, что показывает браузерное
расширение поверх страниц YouTube.

Отличие от остальных сценариев приложения: сюда приходит запрос на ролик,
которого в базе может не быть вовсе — пользователь просто открыл случайное
видео. Порядок работы поэтому такой:

  1. читаем локальный Postgres — бесплатно и мгновенно;
  2. если записи нет или она протухла — добираем ровно один `videos.list`
     (1 unit квоты) и, при необходимости, один `channels.list` (ещё 1 unit);
  3. сохраняем добранное теми же функциями, что и сборщик, поэтому обычный
     просмотр YouTube попутно наполняет базу и историю снапшотов.

Цена вопроса: 1-2 unit на страницу против 100 unit у `search.list`, так что
дневные 10 000 units при обычном сёрфинге не тратятся заметно. Всё, что
дороже одного units-запроса (обход uploads-плейлиста канала), сюда НЕ
попадает — это остаётся явной кнопкой «собрать канал» в расширении.
"""
import json
from datetime import datetime, timezone

import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from application import collecting as collector
from application import maturity_curve as MC
from domain import metrics as M
from domain import periods as P

# Насколько свежей считаем запись в базе. Видео меняет счётчики быстро,
# канал — заметно медленнее, поэтому пороги разные.
VIDEO_STALE_HOURS = 6.0
CHANNEL_STALE_HOURS = 24.0
BATCH_STALE_HOURS = 24.0
MAX_BATCH_IDS = 100


# ------------------------------------------------------------------ утилиты

def _dt(iso):
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _stale(updated_at, hours: float) -> bool:
    t = _dt(updated_at)
    if t is None:
        return True
    return (datetime.now(timezone.utc) - t).total_seconds() > hours * 3600


def _tags(raw):
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return val if isinstance(val, list) else []


def _round(x, n=2):
    return round(x, n) if isinstance(x, (int, float)) else None


def _video_row(conn, video_id):
    row = conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def _channel_row(conn, channel_id):
    if not channel_id:
        return None
    row = conn.execute("SELECT * FROM channels WHERE channel_id = ?",
                       (channel_id,)).fetchone()
    return dict(row) if row else None


def _is_tracked(conn, channel_id) -> bool:
    if not channel_id:
        return False
    row = conn.execute("SELECT 1 FROM tracked_channels WHERE channel_id = ? AND active = 1",
                       (channel_id,)).fetchone()
    return bool(row)


def _stored_videos(conn, channel_id) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n, MIN(published_at) AS oldest, MAX(published_at) AS newest "
        "FROM videos WHERE channel_id = ?", (channel_id,)).fetchone()
    return {"count": (row["n"] if row else 0) or 0,
            "oldestPublishedAt": row["oldest"] if row else None,
            "newestPublishedAt": row["newest"] if row else None}


def _prior_views(conn, channel_id, published_at, is_short, n=M.DEFAULT_BASELINE_N):
    """Просмотры предыдущих загрузок того же канала и того же формата.

    Формат учитываем намеренно: сравнивать Shorts со стандартными роликами —
    это гарантированно раздутый или заниженный outlier.
    """
    if not channel_id or not published_at:
        return []
    rows = conn.execute(
        "SELECT view_count FROM videos WHERE channel_id = ? AND published_at < ? "
        "AND COALESCE(is_short, 0) = ? ORDER BY published_at DESC LIMIT ?",
        (channel_id, published_at, 1 if is_short else 0, int(n))).fetchall()
    return [(r["view_count"] or 0) for r in rows][::-1]


def _history(conn, video_id, limit=300):
    rows = conn.execute(
        "SELECT captured_at, view_count FROM video_stats_history WHERE video_id = ? "
        "ORDER BY captured_at ASC LIMIT ?", (video_id, limit)).fetchall()
    out = []
    for r in rows:
        t = _dt(r["captured_at"])
        if t:
            out.append((t, r["view_count"] or 0))
    return out


def _history_map(conn, video_ids, limit_per_video=100):
    """Same as _history() but for many ids in one query -- used by the badge
    batch so a whole results page doesn't cost one query per card."""
    ids = [i for i in video_ids if i]
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        ph = ",".join(["?"] * len(chunk))
        rows = conn.execute(
            "SELECT video_id, captured_at, view_count FROM video_stats_history "
            "WHERE video_id IN (%s) ORDER BY video_id, captured_at ASC" % ph, chunk
        ).fetchall()
        for r in rows:
            t = _dt(r["captured_at"])
            if t:
                out.setdefault(r["video_id"], []).append((t, r["view_count"] or 0))
    return out


def _ensure_channel(conn, api_key, channel_id, fetch=True, refresh=False,
                    stale_hours=CHANNEL_STALE_HOURS):
    """Возвращает (row, потрачено_units, дозагружали_ли)."""
    ch = _channel_row(conn, channel_id)
    need = refresh or ch is None or _stale(ch.get("updated_at"), stale_hours)
    if need and fetch and api_key and channel_id:
        items = yt.channels_list(api_key, [channel_id])
        if items:
            collector.store_channels(conn, items)
            conn.commit()
            return _channel_row(conn, channel_id), 1, True
        return ch, 1, False
    return ch, 0, False


# ------------------------------------------------------------------- видео

def inspect_video(api_key: str, video_id: str, refresh: bool = False,
                  fetch: bool = True, stale_hours: float = VIDEO_STALE_HOURS) -> dict:
    conn = db.get_conn()
    try:
        MC.ensure_loaded(conn)
        quota = 0
        fetched = False
        v = _video_row(conn, video_id)
        if refresh or v is None or _stale(v.get("updated_at"), stale_hours):
            if fetch and api_key:
                items = yt.videos_list(api_key, [video_id])
                quota += 1
                if items:
                    collector.store_videos(conn, items, embed=False)
                    conn.commit()
                    v = _video_row(conn, video_id)
                    fetched = True

        if not v:
            hint = "видео нет в локальной базе"
            if not api_key:
                hint += ", а YOUTUBE_API_KEY не задан — дозагрузить неоткуда"
            elif not fetch:
                hint += " (дозагрузка выключена в настройках расширения)"
            return {"videoId": video_id, "found": False, "quotaUnits": quota, "hint": hint}

        channel_id = v.get("channel_id")
        ch, ch_quota, ch_fetched = _ensure_channel(conn, api_key, channel_id, fetch=fetch,
                                                   refresh=refresh)
        quota += ch_quota
        ch = ch or {}

        views = v.get("view_count") or 0
        likes = v.get("like_count")
        comments = v.get("comment_count")
        published = v.get("published_at")
        age_days = P.days_since(published) if published else None
        short = bool(v.get("is_short"))

        # --- базы сравнения: пожизненное среднее канала (как у NexLev) и
        #     медиана последних загрузок (наш дефолт — устойчив к вирусам)
        avg_views = M.avg_channel_views(ch.get("view_count") or 0, ch.get("video_count") or 0)
        score_mean = M.outlier_score(views, ch.get("view_count") or 0,
                                     ch.get("video_count") or 0) if avg_views else None
        prior = _prior_views(conn, channel_id, published, short)
        baseline_median = M.baseline_median(prior)
        score_median = M.outlier_vs_median(views, prior)
        primary = score_median if score_median is not None else score_mean
        age_adjusted = None
        if baseline_median and age_days is not None:
            age_adjusted = M.age_adjusted_outlier(views, baseline_median, age_days)
        elif avg_views and age_days is not None:
            age_adjusted = M.age_adjusted_outlier(views, avg_views, age_days)

        # --- скорость по нашим же снапшотам
        history = _history(conn, video_id)
        vph24 = M.vph_from_history(history, 24.0)
        vph7d = M.vph_from_history(history, 24.0 * 7)
        vph_life = M.vph_lifetime(views, (age_days or 0) * 24) if age_days else None

        monthly = M.projected_30d_views(views, age_days) if age_days is not None else views

        return {
            "videoId": video_id,
            "found": True,
            "source": "youtube" if fetched else "db",
            "fetchedFromApi": fetched or ch_fetched,
            "quotaUnits": quota,
            "updatedAt": v.get("updated_at"),
            "video": {
                "title": v.get("title"),
                "publishedAt": published,
                "ageDays": _round(age_days, 2),
                "views": views,
                "likes": likes,
                "comments": comments,
                "durationSeconds": v.get("duration_seconds"),
                "isShort": short,
                "categoryId": v.get("category_id"),
                "language": v.get("default_language"),
                "tags": _tags(v.get("tags")),
                "thumbnail": v.get("thumbnail"),
                "liveContent": v.get("live_content"),
                "firstSeenAt": v.get("first_seen_at"),
                "containsSyntheticMedia": (
                    bool(v.get("contains_synthetic_media"))
                    if v.get("contains_synthetic_media") is not None else None
                ),
            },
            "channel": {
                "channelId": channel_id,
                "title": ch.get("title"),
                "handle": ch.get("custom_url"),
                "subscribers": ch.get("subscriber_count"),
                "subscribersHidden": bool(ch.get("hidden_subs")),
                "totalViews": ch.get("view_count"),
                "videoCount": ch.get("video_count"),
                "avgViews": avg_views or None,
                "createdAt": ch.get("published_at"),
                "thumbnail": ch.get("thumbnail"),
                "country": ch.get("country"),
                "tracked": _is_tracked(conn, channel_id),
                "videosStored": _stored_videos(conn, channel_id)["count"] if channel_id else 0,
            },
            "metrics": {
                "outlierScore": _round(primary),
                "outlierBand": M.outlier_band(primary),
                "outlierVsMedian": _round(score_median),
                "outlierVsChannelMean": _round(score_mean),
                "outlierAgeAdjusted": _round(age_adjusted),
                "baselineMedianViews": int(baseline_median) if baseline_median else None,
                "baselineSample": len(prior),
                "viewsPerSubscriber": _round(
                    M.views_per_subscriber(views, ch.get("subscriber_count")), 3)
                if ch.get("subscriber_count") else None,
                "vphLifetime": _round(vph_life, 1),
                "vph24h": _round(vph24, 1),
                "vph7d": _round(vph7d, 1),
                "acceleration": M.acceleration(vph24, vph7d),
                "projected30dViews": int(monthly) if monthly else None,
                "engagementRate": M.engagement_rate(views, likes, comments),
                "likeRate": M.like_rate(views, likes),
                "commentRate": M.comment_rate(views, comments),
            },
            "revenue": {
                "lifetime": M.revenue_range(views),
                "projected30d": M.revenue_range(int(monthly or 0)),
            },
            "history": {
                "points": len(history),
                "since": history[0][0].isoformat() if history else None,
            },
        }
    finally:
        conn.close()


# ------------------------------------------------------------------ канал

def _resolve_locally(conn, ref: str):
    """Пробуем узнать канал, не тратя квоту: по UC-id или по @хэндлу из базы."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.startswith("UC") and len(ref) > 20:
        row = _channel_row(conn, ref)
        if row:
            return row
        return None
    handle = ref.lstrip("@").lower()
    row = conn.execute(
        "SELECT * FROM channels WHERE lower(custom_url) IN (?, ?) LIMIT 1",
        ("@" + handle, handle)).fetchone()
    return dict(row) if row else None


def inspect_channel(api_key: str, ref: str, refresh: bool = False, fetch: bool = True,
                    stale_hours: float = CHANNEL_STALE_HOURS) -> dict:
    """`ref` — UC-id, @хэндл или ссылка на канал."""
    conn = db.get_conn()
    try:
        quota = 0
        fetched = False
        ch = _resolve_locally(conn, ref)
        need = refresh or ch is None or _stale((ch or {}).get("updated_at"), stale_hours)
        if need and fetch and api_key:
            item = collector.resolve_channel(api_key, ref)
            quota += 1
            if item:
                collector.store_channels(conn, [item])
                conn.commit()
                ch = _channel_row(conn, item["id"])
                fetched = True

        if not ch:
            hint = "канала нет в локальной базе"
            if not api_key:
                hint += ", а YOUTUBE_API_KEY не задан — дозагрузить неоткуда"
            elif not fetch:
                hint += " (дозагрузка выключена в настройках расширения)"
            return {"ref": ref, "found": False, "quotaUnits": quota, "hint": hint}

        channel_id = ch["channel_id"]
        stored = _stored_videos(conn, channel_id)
        created = _dt(ch.get("published_at"))
        age_days = ((datetime.now(timezone.utc) - created).total_seconds() / 86400) if created else None
        avg_views = M.avg_channel_views(ch.get("view_count") or 0, ch.get("video_count") or 0)

        # рост по нашим снапшотам канала
        rows = conn.execute(
            "SELECT captured_at, subscriber_count, view_count FROM channel_stats_history "
            "WHERE channel_id = ? ORDER BY captured_at ASC", (channel_id,)).fetchall()
        snaps = [(_dt(r["captured_at"]), r["subscriber_count"], r["view_count"]) for r in rows]
        snaps = [s for s in snaps if s[0]]
        growth_views = growth_subs = None
        if len(snaps) >= 2:
            first, last = snaps[0], snaps[-1]
            days = (last[0] - first[0]).total_seconds() / 86400
            if days > 0.5:
                growth_subs = M.growth(last[1], first[1], days)
                growth_views = M.growth(last[2], first[2], days)
        lifetime_view_rate = ((ch.get("view_count") or 0) / age_days) if age_days else None
        mom = M.momentum((growth_views or {}).get("per_day"), lifetime_view_rate)

        uploads_per_week = None
        if stored["count"] >= 2 and stored["oldestPublishedAt"] and stored["newestPublishedAt"]:
            a, b = _dt(stored["oldestPublishedAt"]), _dt(stored["newestPublishedAt"])
            if a and b:
                span_days = max((b - a).total_seconds() / 86400, 1.0)
                uploads_per_week = round(stored["count"] / span_days * 7, 2)

        monthly_views = ((growth_views or {}).get("per_day") or 0) * 30
        if not monthly_views and age_days:
            monthly_views = (ch.get("view_count") or 0) / age_days * 30

        return {
            "channelId": channel_id,
            "found": True,
            "source": "youtube" if fetched else "db",
            "fetchedFromApi": fetched,
            "quotaUnits": quota,
            "profile": {
                "title": ch.get("title"),
                "handle": ch.get("custom_url"),
                "country": ch.get("country"),
                "subscribers": ch.get("subscriber_count"),
                "subscribersHidden": bool(ch.get("hidden_subs")),
                "totalViews": ch.get("view_count"),
                "videoCount": ch.get("video_count"),
                "createdAt": ch.get("published_at"),
                "ageDays": round(age_days) if age_days else None,
                "thumbnail": ch.get("thumbnail"),
                "keywords": (ch.get("keywords") or "")[:300] or None,
                "updatedAt": ch.get("updated_at"),
            },
            "metrics": {
                "avgViewsPerVideo": avg_views or None,
                "uploadsPerWeek": uploads_per_week,
                "subsGrowth": growth_subs,
                "viewsGrowth": growth_views,
                "momentum": mom,
                "grade": M.grade(mom),
                "monthlyViewsEstimate": int(monthly_views) if monthly_views else None,
                "revenue": M.revenue_range(int(monthly_views or 0)),
            },
            "stored": stored,
            "tracked": _is_tracked(conn, channel_id),
            "snapshots": len(snaps),
            # ниже этого порога график выбросов и «лучшее время» бессмысленны
            "hasDeepAnalytics": stored["count"] >= 5,
        }
    finally:
        conn.close()


# ------------------------------------------- пачка видео (бейджи в выдаче)

def inspect_videos(api_key: str, video_ids, fetch: bool = True,
                   stale_hours: float = BATCH_STALE_HOURS) -> dict:
    """Короткая сводка по списку id — для значков на карточках видео.

    Стоимость: 1 unit на каждые 50 неизвестных роликов плюс 1 unit на каждые
    50 неизвестных каналов. Поэтому значки на странице поиска обходятся в
    2-3 units, а не в сотню.
    """
    ids = [i.strip() for i in (video_ids or []) if i and i.strip()][:MAX_BATCH_IDS]
    if not ids:
        return {"results": {}, "quotaUnits": 0}

    conn = db.get_conn()
    try:
        quota = 0
        placeholders = ",".join(["?"] * len(ids))
        rows = conn.execute(
            "SELECT * FROM videos WHERE video_id IN (%s)" % placeholders, tuple(ids)).fetchall()
        found = {r["video_id"]: dict(r) for r in rows}

        missing = [i for i in ids if i not in found
                   or _stale(found[i].get("updated_at"), stale_hours)]
        if missing and fetch and api_key:
            items = yt.videos_list(api_key, missing)
            quota += (len(missing) + 49) // 50
            if items:
                collector.store_videos(conn, items, embed=False)
                conn.commit()
                rows = conn.execute(
                    "SELECT * FROM videos WHERE video_id IN (%s)" % placeholders,
                    tuple(ids)).fetchall()
                found = {r["video_id"]: dict(r) for r in rows}

        channel_ids = {v.get("channel_id") for v in found.values() if v.get("channel_id")}
        channels = {}
        if channel_ids:
            ph = ",".join(["?"] * len(channel_ids))
            crows = conn.execute(
                "SELECT * FROM channels WHERE channel_id IN (%s)" % ph,
                tuple(channel_ids)).fetchall()
            channels = {r["channel_id"]: dict(r) for r in crows}
        unknown_channels = [c for c in channel_ids if c not in channels]
        if unknown_channels and fetch and api_key:
            items = yt.channels_list(api_key, unknown_channels)
            quota += (len(unknown_channels) + 49) // 50
            if items:
                collector.store_channels(conn, items)
                conn.commit()
                for item in items:
                    row = _channel_row(conn, item["id"])
                    if row:
                        channels[item["id"]] = row

        history = _history_map(conn, [v.get("video_id") for v in found.values()])
        results = {}
        for vid in ids:
            v = found.get(vid)
            if not v:
                results[vid] = {"found": False}
                continue
            ch = channels.get(v.get("channel_id"), {})
            views = v.get("view_count") or 0
            age_days = P.days_since(v["published_at"]) if v.get("published_at") else None
            score = M.outlier_score(views, ch.get("view_count") or 0,
                                    ch.get("video_count") or 0) or None
            subs = ch.get("subscriber_count")
            vph_life = M.vph_lifetime(views, (age_days or 0) * 24) if age_days else None
            vph24 = M.vph_from_history(history.get(vid) or [], 24.0)
            results[vid] = {
                "found": True,
                "views": views,
                "ageDays": _round(age_days, 2),
                "isShort": bool(v.get("is_short")),
                "outlierScore": _round(score),
                "outlierBand": M.outlier_band(score),
                "viewsPerSubscriber": _round(M.views_per_subscriber(views, subs), 2) if subs else None,
                "vphLifetime": _round(vph_life, 1),
                # null until the worker has taken >=2 snapshots for this video --
                # falls back to vphLifetime client-side, same as the video panel.
                "vph24h": _round(vph24, 1) if vph24 is not None else None,
                "channelId": v.get("channel_id"),
                "channelTitle": ch.get("title"),
                "subscribers": subs,
            }
        return {"results": results, "quotaUnits": quota,
                "fromApi": bool(missing) and fetch and bool(api_key)}
    finally:
        conn.close()

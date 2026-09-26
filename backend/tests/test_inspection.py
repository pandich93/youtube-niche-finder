"""Тесты разбора одной страницы (application/inspection.py).

В отличие от test_smoke.py здесь не нужны ни Postgres, ни ключ YouTube:
инфраструктура подменяется двойниками — sqlite вместо Postgres и заглушка
вместо HTTP-клиента. Так проверяется ровно то, что легко сломать при правке:
сами SQL-запросы, выбор базы сравнения, экономия квоты и поведение, когда в
базе пусто. Интеграция с настоящей базой остаётся за test_smoke.py.

    python3 tests/test_inspection.py        # или: pytest tests/test_inspection.py
"""
import json
import os
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from module_doubles import ModuleDoubles  # noqa: E402

NOW = datetime.now(timezone.utc)
iso = lambda dt: dt.isoformat()

SCHEMA = """
CREATE TABLE channels (channel_id TEXT PRIMARY KEY, title TEXT, custom_url TEXT,
  country TEXT, description TEXT, default_language TEXT, subscriber_count INTEGER,
  video_count INTEGER, view_count INTEGER, thumbnail TEXT, updated_at TEXT,
  published_at TEXT, topic_categories TEXT, keywords TEXT, uploads_playlist TEXT,
  first_seen_at TEXT, hidden_subs INTEGER);
CREATE TABLE videos (video_id TEXT PRIMARY KEY, channel_id TEXT, title TEXT,
  description TEXT, published_at TEXT, duration_seconds INTEGER, view_count INTEGER,
  like_count INTEGER, comment_count INTEGER, thumbnail TEXT, tags TEXT,
  default_language TEXT, updated_at TEXT, category_id TEXT, region TEXT,
  is_short INTEGER, topic_categories TEXT, first_seen_at TEXT, live_content TEXT,
  contains_synthetic_media INTEGER);
CREATE TABLE tracked_channels (channel_id TEXT PRIMARY KEY, note TEXT, added_at TEXT,
  last_refreshed_at TEXT, active INTEGER DEFAULT 1);
CREATE TABLE video_stats_history (video_id TEXT, captured_at TEXT, view_count INTEGER,
  like_count INTEGER, comment_count INTEGER, title TEXT, thumbnail TEXT,
  PRIMARY KEY (video_id, captured_at));
CREATE TABLE channel_stats_history (channel_id TEXT, captured_at TEXT,
  subscriber_count INTEGER, video_count INTEGER, view_count INTEGER,
  PRIMARY KEY (channel_id, captured_at));
"""

RAW = None
CALLS = {"videos": 0, "channels": 0, "resolve": 0}
API_VIDEOS, API_CHANNELS = {}, {}


class _Conn:
    """Обёртка в стиле infrastructure.postgres. close() ничего не делает —
    иначе in-memory база исчезала бы после первого же вызова."""

    def execute(self, sql, params=()):
        return RAW.execute(sql, params)

    def commit(self):
        RAW.commit()

    def close(self):
        pass


def reset():
    global RAW
    RAW = sqlite3.connect(":memory:")
    RAW.row_factory = sqlite3.Row
    RAW.executescript(SCHEMA)
    CALLS.update({"videos": 0, "channels": 0, "resolve": 0})
    API_VIDEOS.clear()
    API_CHANNELS.clear()


reset()

# ---------------------------------------- двойники инфраструктуры

infra_pkg = types.ModuleType("infrastructure")
fake_db = types.ModuleType("infrastructure.postgres")
fake_db.get_conn = lambda: _Conn()
fake_db.now_iso = lambda: iso(datetime.now(timezone.utc))

yt_pkg = types.ModuleType("infrastructure.youtube")
fake_yt = types.ModuleType("infrastructure.youtube.client")


def _videos_list(api_key, ids, parts=None):
    CALLS["videos"] += 1
    return [API_VIDEOS[i] for i in ids if i in API_VIDEOS]


def _channels_list(api_key, ids, parts=None):
    CALLS["channels"] += 1
    return [API_CHANNELS[i] for i in ids if i in API_CHANNELS]


fake_yt.videos_list = _videos_list
fake_yt.channels_list = _channels_list

fake_collector = types.ModuleType("application.collecting")


def _store_videos(conn, items, niche_slug=None, region=None, embed=True, now=None):
    now = now or iso(datetime.now(timezone.utc))
    for it in items:
        sn = it.get("snippet", {}) or {}
        stt = it.get("statistics", {}) or {}
        status = it.get("status", {}) or {}
        dur = int((it.get("contentDetails") or {}).get("seconds") or 0)
        csm = (1 if status.get("containsSyntheticMedia") else
               (0 if "containsSyntheticMedia" in status else None))
        conn.execute(
            "INSERT OR REPLACE INTO videos (video_id, channel_id, title, published_at,"
            " duration_seconds, view_count, like_count, comment_count, tags, updated_at,"
            " category_id, is_short, contains_synthetic_media) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (it["id"], sn.get("channelId"), sn.get("title"), sn.get("publishedAt"), dur,
             int(stt.get("viewCount", 0)), int(stt.get("likeCount", 0)),
             int(stt.get("commentCount", 0)),
             json.dumps(sn.get("tags", []), ensure_ascii=False), now,
             sn.get("categoryId"), 1 if dur and dur <= 180 else 0, csm))
        conn.execute(
            "INSERT OR REPLACE INTO video_stats_history (video_id, captured_at, view_count)"
            " VALUES (?,?,?)", (it["id"], now, int(stt.get("viewCount", 0))))
    return len(items)


def _store_channels(conn, items, now=None):
    now = now or iso(datetime.now(timezone.utc))
    for c in items:
        sn = c.get("snippet", {}) or {}
        stt = c.get("statistics", {}) or {}
        conn.execute(
            "INSERT OR REPLACE INTO channels (channel_id, title, custom_url,"
            " subscriber_count, video_count, view_count, updated_at, published_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (c["id"], sn.get("title"), sn.get("customUrl"),
             int(stt.get("subscriberCount", 0)), int(stt.get("videoCount", 0)),
             int(stt.get("viewCount", 0)), now, sn.get("publishedAt")))
    return {c["id"]: True for c in items}


def _resolve_channel(api_key, ref):
    CALLS["resolve"] += 1
    if ref in API_CHANNELS:
        return API_CHANNELS[ref]
    for item in API_CHANNELS.values():
        if (item.get("snippet") or {}).get("customUrl", "").lower() == str(ref).lower():
            return item
    return None


fake_collector.store_videos = _store_videos
fake_collector.store_channels = _store_channels
fake_collector.resolve_channel = _resolve_channel

# Двойники живут в отдельном «мире модулей» (tests/module_doubles.py): в
# sys.modules они видны только пока идут тесты этого файла, поэтому не
# протекают в Postgres-тесты, которые pytest гоняет в том же процессе.
DOUBLES = ModuleDoubles({
    "infrastructure": infra_pkg,
    "infrastructure.postgres": fake_db,
    "infrastructure.youtube": yt_pkg,
    "infrastructure.youtube.client": fake_yt,
    "application.collecting": fake_collector,
})
I, = DOUBLES.load("application.inspection")

# ----------------------------------------------------------- наполнение

CH = "UC0000000000000000000a"


def seed_channel(subs=10_000, videos=50, views=5_000_000, handle="@tinylab", fresh=True):
    RAW.execute(
        "INSERT OR REPLACE INTO channels (channel_id, title, custom_url, subscriber_count,"
        " video_count, view_count, updated_at, published_at) VALUES (?,?,?,?,?,?,?,?)",
        (CH, "Tiny AI Lab", handle, subs, videos, views,
         iso(NOW if fresh else NOW - timedelta(days=30)), iso(NOW - timedelta(days=800))))


def seed_videos(n=12, base_views=100_000, days_back=60, is_short=0):
    """n предыдущих роликов с одинаковыми просмотрами — медиана предсказуема."""
    for i in range(n):
        RAW.execute(
            "INSERT OR REPLACE INTO videos (video_id, channel_id, title, published_at,"
            " duration_seconds, view_count, like_count, comment_count, tags, updated_at, is_short)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"old{i:08d}", CH, f"Старый ролик {i}", iso(NOW - timedelta(days=days_back - i)),
             600, base_views, 1000, 100, "[]", iso(NOW), is_short))


def seed_target(views=400_000, days=5, fresh_hours=0.0, tags=("ai", "нейросети")):
    RAW.execute(
        "INSERT OR REPLACE INTO videos (video_id, channel_id, title, published_at,"
        " duration_seconds, view_count, like_count, comment_count, tags, updated_at, is_short)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("vid00000001", CH, "Целевой ролик", iso(NOW - timedelta(days=days)), 900, views,
         20_000, 900, json.dumps(list(tags), ensure_ascii=False),
         iso(NOW - timedelta(hours=fresh_hours)), 0))


def api_video(vid="vid00000001", views=400_000, channel=CH, days=5, synthetic=None):
    item = {"id": vid, "snippet": {"channelId": channel, "title": "Из API",
                                   "publishedAt": iso(NOW - timedelta(days=days)),
                                   "tags": ["из", "api"], "categoryId": "28"},
            "statistics": {"viewCount": str(views), "likeCount": "100", "commentCount": "10"},
            "contentDetails": {"seconds": 700}}
    if synthetic is not None:
        item["status"] = {"containsSyntheticMedia": bool(synthetic)}
    return item


def api_channel(cid=CH, subs=10_000, videos=50, views=5_000_000, handle="@tinylab"):
    return {"id": cid, "snippet": {"title": "Tiny AI Lab", "customUrl": handle,
                                   "publishedAt": iso(NOW - timedelta(days=800))},
            "statistics": {"subscriberCount": str(subs), "videoCount": str(videos),
                           "viewCount": str(views)}}


# ---------------------------------------------------------------- тесты

def test_video_from_db_costs_nothing():
    """Свежая запись в базе — ни одного обращения к API."""
    reset(); seed_channel(); seed_videos(); seed_target()
    d = I.inspect_video("ключ", "vid00000001")
    assert d["found"] and d["source"] == "db", d
    assert d["quotaUnits"] == 0 and CALLS["videos"] == 0
    # медиана предыдущих 10 роликов = 100 000, у целевого 400 000
    assert d["metrics"]["baselineMedianViews"] == 100_000
    assert abs(d["metrics"]["outlierScore"] - 4.0) < 0.01
    assert d["metrics"]["outlierBand"] == "strong outlier"
    assert d["metrics"]["viewsPerSubscriber"] == 40.0
    assert d["video"]["tags"] == ["ai", "нейросети"]


def test_shorts_compared_with_shorts():
    """Shorts не должен мериться со стандартными роликами канала."""
    reset(); seed_channel()
    seed_videos(n=12, base_views=1_000_000)                 # длинные
    seed_videos(n=6, base_views=10_000, days_back=40, is_short=1)  # Shorts
    RAW.execute(
        "INSERT OR REPLACE INTO videos (video_id, channel_id, title, published_at,"
        " duration_seconds, view_count, tags, updated_at, is_short)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("shrt0000001", CH, "Шортс", iso(NOW - timedelta(days=1)), 45, 30_000, "[]",
         iso(NOW), 1))
    d = I.inspect_video("ключ", "shrt0000001")
    assert d["metrics"]["baselineMedianViews"] == 10_000, d["metrics"]
    assert abs(d["metrics"]["outlierScore"] - 3.0) < 0.01


def test_missing_video_is_fetched_once():
    """Ролика нет — добираем видео и канал ровно по одному разу."""
    reset()
    API_VIDEOS["vid00000001"] = api_video()
    API_CHANNELS[CH] = api_channel()
    d = I.inspect_video("ключ", "vid00000001")
    assert d["found"] and d["source"] == "youtube" and d["fetchedFromApi"]
    assert d["quotaUnits"] == 2, d["quotaUnits"]
    assert CALLS["videos"] == 1 and CALLS["channels"] == 1
    # и всё осело в базе: повторный вызов уже бесплатный
    d2 = I.inspect_video("ключ", "vid00000001")
    assert d2["quotaUnits"] == 0 and CALLS["videos"] == 1


def test_synthetic_media_flag_roundtrips_from_api():
    """status.containsSyntheticMedia (30 Oct 2024 AI-disclosure flag) is stored
    and comes back as a clean tri-state bool/None, not the raw API 1/0/absent."""
    reset()
    API_VIDEOS["vid00000001"] = api_video(synthetic=True)
    API_CHANNELS[CH] = api_channel()
    d = I.inspect_video("ключ", "vid00000001")
    assert d["video"]["containsSyntheticMedia"] is True

    reset()
    API_VIDEOS["vid00000001"] = api_video(synthetic=False)
    API_CHANNELS[CH] = api_channel()
    d = I.inspect_video("ключ", "vid00000001")
    assert d["video"]["containsSyntheticMedia"] is False


def test_synthetic_media_flag_absent_is_none_not_false():
    """A video fetched before the flag existed must read as unknown, not 'no'
    -- most_popular_categories relies on this to report disclosure coverage
    instead of silently treating unknowns as clean content."""
    reset()
    API_VIDEOS["vid00000001"] = api_video(synthetic=None)
    API_CHANNELS[CH] = api_channel()
    d = I.inspect_video("ключ", "vid00000001")
    assert d["video"]["containsSyntheticMedia"] is None


def test_no_key_no_data_says_so():
    reset()
    d = I.inspect_video("", "vid00000001")
    assert d["found"] is False and d["quotaUnits"] == 0
    assert "YOUTUBE_API_KEY" in d["hint"]


def test_fetch_disabled_is_respected():
    """Выключенная дозагрузка в расширении не должна тратить квоту."""
    reset()
    API_VIDEOS["vid00000001"] = api_video()
    d = I.inspect_video("ключ", "vid00000001", fetch=False)
    assert d["found"] is False and CALLS["videos"] == 0
    assert "выключена" in d["hint"]


def test_velocity_from_history():
    reset(); seed_channel(); seed_videos(); seed_target()
    for hours, views in ((72, 100_000), (48, 200_000), (24, 300_000), (0, 400_000)):
        RAW.execute("INSERT OR REPLACE INTO video_stats_history (video_id, captured_at,"
                    " view_count) VALUES (?,?,?)",
                    ("vid00000001", iso(NOW - timedelta(hours=hours)), views))
    m = I.inspect_video("ключ", "vid00000001")["metrics"]
    assert abs(m["vph24h"] - 100_000 / 24) < 1, m["vph24h"]
    assert m["vph7d"] is not None and m["acceleration"] is not None


def test_channel_by_handle_without_quota():
    """@хэндл, уже известный базе, резолвится локально — 0 units."""
    reset(); seed_channel(handle="@tinylab"); seed_videos(n=8)
    d = I.inspect_channel("ключ", "@tinylab")
    assert d["found"] and d["channelId"] == CH and d["quotaUnits"] == 0
    assert CALLS["resolve"] == 0
    assert d["stored"]["count"] == 8 and d["hasDeepAnalytics"] is True
    assert d["metrics"]["avgViewsPerVideo"] == 100_000  # 5 000 000 / 50


def test_channel_unknown_handle_resolves_via_api():
    reset()
    API_CHANNELS["@tinylab"] = api_channel(handle="@tinylab")
    d = I.inspect_channel("ключ", "@tinylab")
    assert d["found"] and d["quotaUnits"] == 1 and CALLS["resolve"] == 1
    assert d["hasDeepAnalytics"] is False  # роликов в базе ещё нет


def test_channel_growth_from_snapshots():
    reset(); seed_channel()
    for days, subs, views in ((30, 8_000, 4_000_000), (0, 10_000, 5_000_000)):
        RAW.execute("INSERT OR REPLACE INTO channel_stats_history (channel_id, captured_at,"
                    " subscriber_count, video_count, view_count) VALUES (?,?,?,?,?)",
                    (CH, iso(NOW - timedelta(days=days)), subs, 50, views))
    m = I.inspect_channel("ключ", CH)["metrics"]
    assert m["subsGrowth"]["delta"] == 2000
    assert abs(m["subsGrowth"]["per_day"] - 2000 / 30) < 1
    assert m["monthlyViewsEstimate"] > 0


def test_batch_mixes_known_and_unknown():
    reset(); seed_channel(); seed_videos(n=3); seed_target()
    out = I.inspect_videos("", ["vid00000001", "old00000000", "нетуТакого"], fetch=False)
    r = out["results"]
    assert out["quotaUnits"] == 0
    assert r["vid00000001"]["found"] and r["vid00000001"]["outlierScore"] is not None
    assert r["нетуТакого"]["found"] is False
    # в пачке база сравнения — пожизненное среднее канала (100 000)
    assert abs(r["vid00000001"]["outlierScore"] - 4.0) < 0.01


def test_batch_includes_vph24h_when_history_exists():
    """VPH under a feed badge (plan item 8.3) uses the same snapshot history
    as the video panel -- null, not zero, when the worker hasn't run yet."""
    reset(); seed_channel(); seed_videos(n=3); seed_target()
    for hours, views in ((24, 100_000), (0, 400_000)):
        RAW.execute("INSERT OR REPLACE INTO video_stats_history (video_id, captured_at,"
                    " view_count) VALUES (?,?,?)",
                    ("vid00000001", iso(NOW - timedelta(hours=hours)), views))
    out = I.inspect_videos("", ["vid00000001", "old00000000"], fetch=False)
    r = out["results"]
    assert abs(r["vid00000001"]["vph24h"] - 300_000 / 24) < 1, r["vid00000001"]
    assert r["old00000000"]["vph24h"] is None
    assert r["old00000000"]["vphLifetime"] is not None


def test_batch_fetches_missing_in_one_call():
    reset(); seed_channel()
    for i in range(3):
        API_VIDEOS[f"new{i:08d}"] = api_video(vid=f"new{i:08d}", views=200_000)
    out = I.inspect_videos("ключ", [f"new{i:08d}" for i in range(3)])
    assert CALLS["videos"] == 1, "три ролика должны уйти одним запросом"
    assert out["quotaUnits"] == 1
    assert all(v["found"] for v in out["results"].values())


def test_stale_row_is_refreshed():
    """Запись старше шести часов перечитывается из API."""
    reset(); seed_channel(); seed_videos(); seed_target(fresh_hours=10)
    API_VIDEOS["vid00000001"] = api_video(views=999_000)
    API_CHANNELS[CH] = api_channel()
    d = I.inspect_video("ключ", "vid00000001")
    assert CALLS["videos"] == 1 and d["video"]["views"] == 999_000


def test_tracked_flag():
    reset(); seed_channel(); seed_videos(); seed_target()
    RAW.execute("INSERT INTO tracked_channels (channel_id, active) VALUES (?, 1)", (CH,))
    assert I.inspect_video("ключ", "vid00000001")["channel"]["tracked"] is True
    assert I.inspect_channel("ключ", CH)["tracked"] is True


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    with DOUBLES.active():
        for name, fn in tests:
            try:
                fn()
                print(f"  ok  {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} прошло")
    sys.exit(1 if failed else 0)

"""Tests for the MCP tool layer (interfaces/mcp/server.py) and the search-quota
accounting it depends on. Run with pytest, or directly: python3 tests/test_mcp_tools.py

Same throwaway-schema setup as test_smoke.py (tests/schema_scope.py: own schema
per process, dropped at exit) -- no YouTube API key and no network required
(comment_threads is monkeypatched).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db              # noqa: E402
import infrastructure.youtube.client as yt         # noqa: E402
from domain import periods as P                    # noqa: E402
from infrastructure.categories import repository as C  # noqa: E402
from application import collecting as collector    # noqa: E402
from application import discovery as trends        # noqa: E402
from application import search as query            # noqa: E402
from application import worker_cycle as worker     # noqa: E402
import interfaces.mcp.server as srv                # noqa: E402


def setup_module(_=None):
    db.init_db()
    C.seed_fallback()
    srv.API_KEY = "test-key"


# --------------------------------------------------- persistent search quota

def test_search_calls_accumulate_across_calls_same_day():
    conn = db.get_conn()
    before = collector.search_calls_today(conn)
    collector._record_search_calls(conn, 3)
    collector._record_search_calls(conn, 2)
    conn.commit()
    after = collector.search_calls_today(conn)
    conn.close()
    assert after == before + 5


def test_quota_reports_daily_total_not_just_this_call():
    conn = db.get_conn()
    collector._record_search_calls(conn, 10)
    conn.commit()
    calls_today = collector.search_calls_today(conn)
    conn.close()
    q = collector._quota(search_calls=1, n_videos=0, n_channels=0, calls_today=calls_today)
    assert q["search_calls"] == 1
    assert q["search_calls_today"] == calls_today
    assert q["search_calls_left_today"] == max(0, yt.SEARCH_DAILY_CALL_LIMIT - calls_today)


def test_db_stats_exposes_search_quota_and_worker_flag():
    s = query.db_stats()
    assert "search_quota" in s
    assert s["search_quota"]["search_calls_left_today"] <= yt.SEARCH_DAILY_CALL_LIMIT
    assert "worker_quota_blocked_until" in s


def test_db_stats_exposes_videos_without_embedding():
    s = query.db_stats()
    assert "videos_without_embedding" in s
    assert isinstance(s["videos_without_embedding"], int)


def test_data_coverage_exposes_search_quota():
    cov = trends.coverage(period="30d")
    assert "searchCallsToday" in cov
    assert "searchCallsLeftToday" in cov


# --------------------------------------------------- worker quota backoff

def test_worker_blocked_flag_is_read_not_just_written():
    conn = db.get_conn()
    db.set_meta(conn, "worker_quota_blocked_until", P.pacific_date_key())
    conn.commit()
    conn.close()
    assert worker._search_quota_blocked_today() is True

    conn = db.get_conn()
    db.set_meta(conn, "worker_quota_blocked_until", "2000-01-01")
    conn.commit()
    conn.close()
    assert worker._search_quota_blocked_today() is False


# --------------------------------------------------- video_comments MCP tool

def test_video_comments_tool_shapes_raw_comments(monkeypatch):
    fake_items = [
        {
            "snippet": {
                "totalReplyCount": 2,
                "topLevelComment": {"snippet": {
                    "authorDisplayName": "@viewer1",
                    "textDisplay": "great video",
                    "likeCount": 5,
                    "publishedAt": "2026-01-01T00:00:00Z",
                }},
            }
        },
    ]
    monkeypatch.setattr(yt, "comment_threads", lambda *a, **k: fake_items)
    out = srv.video_comments("vid123")
    assert out["video_id"] == "vid123"
    assert out["count"] == 1
    c = out["comments"][0]
    assert c == {
        "author": "@viewer1", "text": "great video", "likeCount": 5,
        "publishedAt": "2026-01-01T00:00:00Z", "replyCount": 2,
    }


# --------------------------------------------------- backfill_embeddings

def test_backfill_embeddings_fills_in_missing_vectors():
    conn = db.get_conn()
    db.upsert_channel(conn, {
        "channel_id": "UCbackfill000000000000000", "title": "Backfill Channel",
        "custom_url": None, "country": None, "description": "", "default_language": None,
        "subscriber_count": 500, "video_count": 1, "view_count": 500,
        "thumbnail": None, "published_at": None, "topic_categories": None,
        "keywords": None, "uploads_playlist": None, "hidden_subs": 0,
    })
    db.upsert_video(conn, {
        "video_id": "vbackfill1", "channel_id": "UCbackfill000000000000000",
        "title": "unembedded video", "description": "needs a vector",
        "published_at": "2026-01-01T00:00:00Z", "duration_seconds": 300,
        "view_count": 10, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    })
    conn.commit()
    before = conn.execute(
        "SELECT embedding FROM videos WHERE video_id='vbackfill1'").fetchone()
    assert before["embedding"] is None
    conn.close()

    out = srv.backfill_embeddings(limit=10000)
    assert out["embedded"] >= 1

    conn = db.get_conn()
    after = conn.execute(
        "SELECT embedding FROM videos WHERE video_id='vbackfill1'").fetchone()
    conn.close()
    assert after["embedding"] is not None


# --------------------------------------------------- similar_channels

def test_similar_channels_without_embeddings_gives_a_hint():
    conn = db.get_conn()
    db.upsert_channel(conn, {
        "channel_id": "UCnoembed00000000000000", "title": "No Embeddings Channel",
        "custom_url": None, "country": None, "description": "", "default_language": None,
        "subscriber_count": 1000, "video_count": 10, "view_count": 100000,
        "thumbnail": None, "published_at": None, "topic_categories": None,
        "keywords": None, "uploads_playlist": None, "hidden_subs": 0,
    })
    conn.commit()
    conn.close()
    out = srv.similar_channels("UCnoembed00000000000000")
    assert out["similar"] == []
    assert "hint" in out


def test_similar_channels_ranks_by_cosine_similarity():
    import infrastructure.embeddings.fastembed_provider as emb

    conn = db.get_conn()
    for cid in ("UCtarget0000000000000000", "UCclose00000000000000000",
                "UCfar000000000000000000"):
        db.upsert_channel(conn, {
            "channel_id": cid, "title": cid, "custom_url": None, "country": None,
            "description": "", "default_language": None, "subscriber_count": 1000,
            "video_count": 1, "view_count": 1000, "thumbnail": None,
            "published_at": None, "topic_categories": None, "keywords": None,
            "uploads_playlist": None, "hidden_subs": 0,
        })

    def video_row(video_id, channel_id, text):
        return {
            "video_id": video_id, "channel_id": channel_id, "title": text,
            "description": "", "published_at": "2026-01-01T00:00:00Z",
            "duration_seconds": 600, "view_count": 1000, "like_count": 10,
            "comment_count": 1, "thumbnail": None, "tags": "[]",
            "default_language": "en", "embedding": emb.to_blob(emb.embed(text)),
            "updated_at": "2026-01-01T00:00:00Z", "category_id": None,
            "region": None, "is_short": 0, "topic_categories": None,
            "live_content": None,
        }

    db.upsert_video(conn, video_row("vtarget1", "UCtarget0000000000000000",
                                    "faceless space exploration documentary"))
    db.upsert_video(conn, video_row("vclose1", "UCclose00000000000000000",
                                    "faceless space exploration documentary"))
    db.upsert_video(conn, video_row("vfar1", "UCfar000000000000000000",
                                    "recipe for chocolate chip cookies"))
    conn.commit()
    conn.close()

    out = srv.similar_channels("UCtarget0000000000000000", limit=5)
    ids = [c["channelId"] for c in out["similar"]]
    assert ids[0] == "UCclose00000000000000000"
    assert ids.index("UCclose00000000000000000") < ids.index("UCfar000000000000000000")


def test_video_comments_requires_api_key(monkeypatch):
    monkeypatch.setattr(srv, "API_KEY", None)
    try:
        srv.video_comments("vid123")
    except RuntimeError as e:
        assert "YOUTUBE_API_KEY" in str(e)
    else:
        raise AssertionError("expected RuntimeError without an API key")


# ============================================================================
# Тулы, которые ходят в YouTube: collect_* / refresh_* / track_channel.
#
# Ни один тест ниже не выходит в сеть и не требует ключа -- клиент
# infrastructure.youtube.client монкипатчится целиком, как в
# test_video_comments_tool_shapes_raw_comments выше. Проверяем именно функции
# из interfaces.mcp.server, а не слои под ними: иначе интерфейсный слой
# остаётся непокрытым.
#
# Тесты обязаны быть независимыми от порядка: самодельный раннер внизу файла
# гоняет их в алфавитном порядке, pytest -- в порядке объявления.


def _api_channel(cid, title="Fake Channel", subs=1000, videos=10, views=100000,
                 uploads="UUfake"):
    """Ответ channels.list в том виде, в каком его разбирает store_channels."""
    return {
        "id": cid,
        "snippet": {"title": title, "description": "fake channel",
                    "publishedAt": "2020-01-01T00:00:00Z",
                    "thumbnails": {"high": {"url": "https://i.ytimg.com/c.jpg"}}},
        "statistics": {"subscriberCount": str(subs), "videoCount": str(videos),
                       "viewCount": str(views)},
        "contentDetails": {"relatedPlaylists": {"uploads": uploads}},
    }


def _no_network(monkeypatch):
    """Затыкает все пути в сеть, которыми может пойти resolve_channel.

    Без этого опечатка в id канала (CHANNEL_ID_RE ждёт ровно UC + 22 символа)
    тихо уводит тест в ветку @handle и в живой googleapis.com."""
    def boom(*a, **k):
        raise AssertionError("тест ушёл в сеть: незапатченный вызов YouTube API")
    for name in ("search_videos", "videos_list", "channels_list", "channel_by_handle",
                 "playlist_items", "most_popular", "video_categories",
                 "videos_batch_get_stats", "comment_threads"):
        monkeypatch.setattr(yt, name, boom)


def _api_video(vid, cid, title="Fake Video", views=1000,
               published="2026-01-01T00:00:00Z"):
    """Ответ videos.list в том виде, в каком его разбирает store_videos."""
    return {
        "id": vid,
        "snippet": {"channelId": cid, "title": title, "description": "fake video",
                    "publishedAt": published, "tags": ["fake"], "categoryId": "22",
                    "thumbnails": {"high": {"url": "https://i.ytimg.com/v.jpg"}}},
        "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "2"},
        "contentDetails": {"duration": "PT10M"},
        "status": {},
    }


def _count(sql, params=()):
    conn = db.get_conn()
    n = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return n


# --------------------------------------------------- collect_channel

def test_collect_channel_stores_videos_without_spending_search(monkeypatch):
    _no_network(monkeypatch)
    cid = "UCcollectch0000000000001"
    monkeypatch.setattr(yt, "channels_list",
                        lambda k, ids, **kw: [_api_channel(cid, uploads="UUcollectch")])
    monkeypatch.setattr(yt, "playlist_items",
                        lambda k, pl, max_items=200: (
                            [{"video_id": "vcollectch1"}, {"video_id": "vcollectch2"}], 1))
    monkeypatch.setattr(yt, "videos_list",
                        lambda k, ids, **kw: [_api_video(v, cid) for v in ids])

    out = srv.collect_channel(cid, max_videos=50)

    assert out["channelId"] == cid
    assert out["videos_found"] == 2
    assert out["videos_stored"] == 2
    # весь смысл тула: идём через uploads-плейлист и не трогаем дневной лимит поиска
    assert out["quota"]["search_calls"] == 0

    conn = db.get_conn()
    stored = {r["video_id"] for r in conn.execute(
        "SELECT video_id FROM videos WHERE channel_id=?", (cid,)).fetchall()}
    conn.close()
    assert stored == {"vcollectch1", "vcollectch2"}


# --------------------------------------------------- collect_niche

def test_collect_niche_spends_one_search_call_per_page(monkeypatch):
    _no_network(monkeypatch)
    cid = "UCcollectniche0000000001"
    pages_served = []

    def fake_search(k, query, page_token=None, **kw):
        pages_served.append(page_token)
        n = len(pages_served)
        return {"items": [{"id": {"videoId": f"vcollectniche{n}"}}],
                "nextPageToken": "tok" if n < 2 else None}

    monkeypatch.setattr(yt, "search_videos", fake_search)
    monkeypatch.setattr(yt, "videos_list",
                        lambda k, ids, **kw: [_api_video(v, cid) for v in ids])
    monkeypatch.setattr(yt, "channels_list", lambda k, ids, **kw: [_api_channel(cid)])

    conn = db.get_conn()
    before = collector.search_calls_today(conn)
    conn.close()

    out = srv.collect_niche("fake niche query", label="fake-niche", pages=2)

    conn = db.get_conn()
    after = collector.search_calls_today(conn)
    conn.close()

    assert pages_served == [None, "tok"], "вторая страница должна идти по nextPageToken"
    assert after == before + 2, "каждая страница search.list списывается со счётчика"
    assert out["niche"] == "fake-niche"
    assert out["videos_stored"] == 2


# --------------------------------------------------- collect_trending

def test_collect_trending_writes_a_ranked_chart_snapshot(monkeypatch):
    _no_network(monkeypatch)
    cid = "UCcollecttrend0000000001"
    monkeypatch.setattr(yt, "most_popular",
                        lambda k, region_code="US", video_category_id=None, pages=2:
                        [_api_video("vtrend1", cid), _api_video("vtrend2", cid)])
    monkeypatch.setattr(yt, "channels_list", lambda k, ids, **kw: [_api_channel(cid)])

    out = srv.collect_trending(regions=["US"], pages=1)
    snap = out["snapshots"][0]
    assert snap["videos"] == 2
    assert snap["snapshotId"]

    conn = db.get_conn()
    rows = conn.execute(
        'SELECT video_id, "rank" FROM chart_entries WHERE snapshot_id=? ORDER BY "rank"',
        (snap["snapshotId"],)).fetchall()
    conn.close()
    assert [r["rank"] for r in rows] == [1, 2]
    assert [r["video_id"] for r in rows] == ["vtrend1", "vtrend2"]


# --------------------------------------------------- refresh_stats

def test_refresh_stats_appends_history_instead_of_overwriting(monkeypatch):
    _no_network(monkeypatch)
    cid, vid = "UCrefreshstats0000000001", "vrefreshstats1"
    monkeypatch.setattr(yt, "channels_list", lambda k, ids, **kw: [_api_channel(cid)])
    monkeypatch.setattr(yt, "playlist_items",
                        lambda k, pl, max_items=200: ([{"video_id": vid}], 1))
    monkeypatch.setattr(yt, "videos_list",
                        lambda k, ids, **kw: [_api_video(v, cid) for v in ids])
    srv.collect_channel(cid)

    monkeypatch.setattr(yt, "videos_batch_get_stats",
                        lambda k, ids, **kw: ([_api_video(vid, cid, views=2222)], []))

    sql = "SELECT COUNT(*) FROM video_stats_history WHERE video_id=?"
    before = _count(sql, (vid,))
    srv.refresh_stats(scope="all", limit=5000)
    once = _count(sql, (vid,))
    srv.refresh_stats(scope="all", limit=5000)
    twice = _count(sql, (vid,))

    # Ровно то поведение, на котором стоит idempotentHint=False у refresh_stats:
    # captured_at ставится в now(), поэтому ON CONFLICT никогда не срабатывает.
    assert once == before + 1
    assert twice == once + 1


# --------------------------------------------------- refresh_channels

def test_refresh_channels_appends_a_growth_snapshot_each_call(monkeypatch):
    _no_network(monkeypatch)
    cid = "UCrefreshchans0000000001"
    monkeypatch.setattr(yt, "channels_list",
                        lambda k, ids, **kw: [_api_channel(cid, subs=4242)])

    sql = "SELECT COUNT(*) FROM channel_stats_history WHERE channel_id=?"
    before = _count(sql, (cid,))
    out = srv.refresh_channels(channel_ids=[cid])
    once = _count(sql, (cid,))
    srv.refresh_channels(channel_ids=[cid])
    twice = _count(sql, (cid,))

    assert out["refreshed"] == 1
    assert once == before + 1
    assert twice == once + 1


# --------------------------------------------------- refresh_categories

def test_refresh_categories_upserts_and_does_not_duplicate(monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setattr(yt, "video_categories",
                        lambda k, region_code="US", hl="en_US":
                        [{"id": "9999", "title": "Fake Category", "assignable": True}])

    srv.refresh_categories(regions=["ZZ"])
    srv.refresh_categories(regions=["ZZ"])

    # Единственный из collect/refresh, который идемпотентен: истории нет, только
    # upsert по (category_id, region).
    assert _count("SELECT COUNT(*) FROM video_categories "
                  "WHERE category_id='9999' AND region='ZZ'") == 1


# --------------------------------------------------- track / untrack / list

def test_track_then_untrack_keeps_what_was_already_collected(monkeypatch):
    _no_network(monkeypatch)
    cid = "UCtrackflow0000000000001"
    monkeypatch.setattr(yt, "channels_list",
                        lambda k, ids, **kw: [_api_channel(cid, uploads="UUtrackflow")])
    monkeypatch.setattr(yt, "playlist_items",
                        lambda k, pl, max_items=200: ([{"video_id": "vtrackflow1"}], 1))
    monkeypatch.setattr(yt, "videos_list",
                        lambda k, ids, **kw: [_api_video(v, cid) for v in ids])

    out = srv.track_channel(cid, note="следим")
    assert out["tracked"] is True
    assert cid in {c["channel_id"] for c in srv.list_tracked_channels()}

    srv.untrack_channel(cid)
    assert cid not in {c["channel_id"] for c in srv.list_tracked_channels()}

    # Ровно то, что обещает докстринг untrack_channel и что стоит за
    # destructiveHint=True: канал уходит из списка, собранное остаётся.
    assert _count("SELECT COUNT(*) FROM videos WHERE channel_id=?", (cid,)) == 1


# --------------------------------------------------- alerts

def test_scan_for_alerts_never_duplicates_and_seen_can_be_cleared():
    srv.scan_for_alerts()
    first = _count("SELECT COUNT(*) FROM events")
    srv.scan_for_alerts()
    second = _count("SELECT COUNT(*) FROM events")
    assert second == first, "повторный скан не создаёт дубль по тому же (kind, ref_id)"

    srv.mark_events_seen(all_unseen=True)
    assert srv.list_events(unseen_only=True, limit=1000) == []


# --------------------------------------------------- swipe file

def test_saved_item_roundtrip_and_delete_is_safe_to_repeat():
    saved = srv.save_item("video", "vswipe1", note="хороший ролик")
    item_id = saved["id"]
    assert item_id in {i["id"] for i in srv.list_saved_items(kind="video")}

    srv.delete_saved_item(item_id)
    assert item_id not in {i["id"] for i in srv.list_saved_items(kind="video")}

    # delete_saved_item несёт destructiveHint=True, но idempotentHint=True:
    # повторный вызов обязан быть безобидным.
    srv.delete_saved_item(item_id)


# --------------------------------------------------- calibrate_maturity_curve

def test_calibrate_maturity_curve_only_reads():
    tables = ("videos", "channels", "video_stats_history", "events", "saved_items")

    def snapshot():
        return {t: _count(f"SELECT COUNT(*) FROM {t}") for t in tables}

    before = snapshot()
    srv.calibrate_maturity_curve(min_videos=1)
    # Вопреки названию тул ничего не калибрует в базе -- он считает кривую и
    # отдаёт её текстом. На этом стоит readOnlyHint=True.
    assert snapshot() == before


# --------------------------------------------------- video tags

def test_tag_videos_tool_writes_and_reports_protection():
    conn = db.get_conn()
    db.upsert_channel(conn, {
        "channel_id": "UCtagmcp0000000000000001", "title": "t", "custom_url": None,
        "country": None, "description": "", "default_language": None,
        "subscriber_count": 100, "video_count": 1, "view_count": 100,
        "thumbnail": None, "published_at": None, "topic_categories": None,
        "keywords": None, "uploads_playlist": None, "hidden_subs": 0,
    })
    db.upsert_video(conn, {
        "video_id": "vtagmcp1", "channel_id": "UCtagmcp0000000000000001",
        "title": "t", "description": "", "published_at": "2026-01-01T00:00:00Z",
        "duration_seconds": 300, "view_count": 100, "like_count": 0, "comment_count": 0,
        "thumbnail": None, "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    })
    conn.commit()
    conn.close()

    r = srv.tag_videos([{"video_id": "vtagmcp1", "tag_group": "theme", "tags": ["fear"]}],
                       source="manual")
    assert r["written"] == 1

    r2 = srv.tag_videos([{"video_id": "vtagmcp1", "tag_group": "theme", "tags": ["fear"]}],
                        source="llm")
    assert r2["blockedByProtection"] == 1

    rows = srv.list_video_tags(video_id="vtagmcp1")
    assert [row["source"] for row in rows] == ["manual"]


def test_tag_videos_tool_reports_a_bad_source_as_an_error_not_an_exception():
    out = srv.tag_videos([{"video_id": "v1", "tag_group": "theme", "tags": ["x"]}],
                         source="not-real")
    assert "error" in out


def test_tag_stats_tool_on_an_empty_niche_gives_a_hint():
    out = srv.tag_stats("no-such-niche-mcp", "theme")
    assert out["found"] is False
    assert "hint" in out


# --------------------------------------------------- тонкие делегаты

def test_thin_read_only_tools_answer_on_an_empty_corpus():
    assert isinstance(srv.list_niches(), list)

    sim = srv.similar_videos("vdoesnotexist")
    assert sim["similar"] == []
    assert "hint" in sim, "без эмбеддинга тул обязан объяснить, почему пусто"

    ov = srv.niche_overview_from_channel("UCdoesnotexist0000000001")
    assert ov["found"] is False
    assert "hint" in ov

    # data_coverage и title_changes проверялись только на уровне application;
    # дёргаем и сами тулы, иначе интерфейсный слой остаётся без теста.
    cov = srv.data_coverage(period="30d")
    assert "searchCallsToday" in cov

    changes = srv.title_changes(period="7d", limit=5)
    assert isinstance(changes, dict)


if __name__ == "__main__":
    setup_module()

    class _Monkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._undo):
                setattr(obj, name, value)

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        mp = _Monkeypatch()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
        finally:
            mp.undo()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)

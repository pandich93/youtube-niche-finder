"""Regression tests for the four bugs in .plans/12-bugfixes-from-review.md.
Run with pytest, or directly: python3 tests/test_bugfixes_review.py

Same throwaway-schema Postgres setup as test_smoke.py/test_mcp_tools.py (own
schema per process, dropped at exit) -- no YouTube API key and no network
required, YouTube calls are monkeypatched where a test needs them.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
import interfaces.http.api as api  # noqa: E402
import interfaces.mcp.server as srv  # noqa: E402
from application import channel_tracking as T  # noqa: E402
from application import collecting as collector  # noqa: E402
from application import discovery as trends  # noqa: E402
from infrastructure.categories import repository as C  # noqa: E402

client = TestClient(api.app)


def setup_module(_=None):
    db.init_db()
    C.seed_fallback()
    srv.API_KEY = "test-key"


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("тест ушёл в сеть: незапатченный вызов YouTube API")
    for name in ("search_videos", "videos_list", "channels_list", "channel_by_handle",
                 "playlist_items", "most_popular", "video_categories",
                 "videos_batch_get_stats", "comment_threads"):
        monkeypatch.setattr(yt, name, boom)


def _api_channel(cid, title="Fake Channel", subs=1000, videos=10, views=100000,
                 uploads="UUfake", custom_url=None):
    return {
        "id": cid,
        "snippet": {"title": title, "description": "fake channel",
                    "customUrl": custom_url,
                    "publishedAt": "2020-01-01T00:00:00Z",
                    "thumbnails": {"high": {"url": "https://i.ytimg.com/c.jpg"}}},
        "statistics": {"subscriberCount": str(subs), "videoCount": str(videos),
                       "viewCount": str(views)},
        "contentDetails": {"relatedPlaylists": {"uploads": uploads}},
    }


def _api_video(vid, cid, title="Fake Video", views=1000,
               published="2026-01-01T00:00:00Z"):
    return {
        "id": vid,
        "snippet": {"channelId": cid, "title": title, "description": "fake video",
                    "publishedAt": published, "tags": ["fake"], "categoryId": "22",
                    "thumbnails": {"high": {"url": "https://i.ytimg.com/v.jpg"}}},
        "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "2"},
        "contentDetails": {"duration": "PT10M"},
        "status": {},
    }


def _uc(tag: str) -> str:
    """Build a syntactically valid channel id: UC + exactly 22 [\\w-] chars
    (CHANNEL_ID_RE in application/collecting.py), zero-padded from a short tag."""
    tag = tag[:22]
    return "UC" + tag + "0" * (22 - len(tag))


def _seed_channel_with_handle(cid, handle):
    """Кладём канал в базу локально (без сети), с известным custom_url, чтобы
    проверить бесплатное локальное разрешение хэндла."""
    conn = db.get_conn()
    now = db.now_iso()
    collector.store_channels(conn, [_api_channel(cid, custom_url=handle)], now)
    conn.commit()
    conn.close()


# ============================================================ bug 1: resolve_channel_id

def test_resolve_channel_id_passes_uc_id_through_untouched():
    conn = db.get_conn()
    cid = T.resolve_channel_id(conn, None, _uc("resolveid1a"))
    conn.close()
    assert cid == _uc("resolveid1a")


def test_resolve_channel_id_extracts_uc_id_from_a_url():
    conn = db.get_conn()
    cid = T.resolve_channel_id(conn, None,
                               f"https://www.youtube.com/channel/{_uc('resolveid1b')}")
    conn.close()
    assert cid == _uc("resolveid1b")


def test_resolve_channel_id_resolves_handle_locally_at_zero_quota(monkeypatch):
    _no_network(monkeypatch)  # никакого вызова YouTube -- разрешаем из своей базы
    cid = _uc("resolveid2a")
    _seed_channel_with_handle(cid, "@resolvefromdb")

    conn = db.get_conn()
    got = T.resolve_channel_id(conn, None, "@resolvefromdb")
    conn.close()
    assert got == cid


def test_resolve_channel_id_local_lookup_is_case_insensitive_and_at_sign_optional(monkeypatch):
    _no_network(monkeypatch)
    cid = _uc("resolveid2b")
    _seed_channel_with_handle(cid, "@CaseSensitiveHandle")

    conn = db.get_conn()
    got = T.resolve_channel_id(conn, None, "casesensitivehandle")
    conn.close()
    assert got == cid


def test_resolve_channel_id_falls_back_to_the_api_when_not_known_locally(monkeypatch):
    cid = _uc("resolveid3a")
    monkeypatch.setattr(yt, "channel_by_handle",
                        lambda k, h, **kw: _api_channel(cid, custom_url="@apihandle"))

    conn = db.get_conn()
    got = T.resolve_channel_id(conn, "test-key", "@apihandle")
    conn.close()
    assert got == cid


def test_resolve_channel_id_raises_without_api_key_when_not_known_locally(monkeypatch):
    _no_network(monkeypatch)
    conn = db.get_conn()
    try:
        T.resolve_channel_id(conn, None, "@totallyunknownhandle")
        assert False, "должно было упасть"
    except ValueError as e:
        assert "API" in str(e) or "key" in str(e)
    finally:
        conn.close()


def test_resolve_channel_id_raises_a_readable_error_when_the_api_finds_nothing(monkeypatch):
    monkeypatch.setattr(yt, "channel_by_handle", lambda k, h, **kw: None)
    conn = db.get_conn()
    try:
        T.resolve_channel_id(conn, "test-key", "@nosuchhandle")
        assert False, "должно было упасть"
    except ValueError as e:
        assert "nosuchhandle" in str(e)
    finally:
        conn.close()


# ------------------------------------------- track_channel(collect=False)

def test_mcp_track_channel_without_collect_resolves_handle_to_real_id(monkeypatch):
    _no_network(monkeypatch)
    cid = _uc("resolveid4a")
    _seed_channel_with_handle(cid, "@trackmehandle")

    out = srv.track_channel("@trackmehandle", collect=False)

    assert out["channelId"] == cid
    conn = db.get_conn()
    row = conn.execute("SELECT channel_id FROM tracked_channels WHERE channel_id=?",
                       (cid,)).fetchone()
    bad = conn.execute(
        "SELECT channel_id FROM tracked_channels WHERE channel_id='@trackmehandle'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert bad is None  # старый баг: сырой хэндл писался как есть


def test_mcp_track_channel_unresolvable_handle_writes_nothing(monkeypatch):
    # Не в локальной базе, а API (тоже замоканный) говорит "нет такого канала" --
    # значит идём по тому же пути, что и на проде, когда ключ есть, но канал не найден.
    monkeypatch.setattr(yt, "channel_by_handle", lambda k, h, **kw: None)
    out = srv.track_channel("@nowhereinourdb", collect=False)

    assert "error" in out
    conn = db.get_conn()
    row = conn.execute(
        "SELECT channel_id FROM tracked_channels WHERE channel_id='@nowhereinourdb'"
    ).fetchone()
    conn.close()
    assert row is None


def test_http_track_endpoint_resolves_handle_to_real_id(monkeypatch):
    _no_network(monkeypatch)
    cid = _uc("resolveid5a")
    _seed_channel_with_handle(cid, "@httptrackhandle")
    api.API_KEY = ""  # разрешаем без ключа -- канал уже есть локально

    resp = client.post("/api/channels/track", json={"channel_id": "@httptrackhandle"})

    assert resp.status_code == 200
    assert resp.json()["channelId"] == cid
    conn = db.get_conn()
    row = conn.execute("SELECT channel_id FROM tracked_channels WHERE channel_id=?",
                       (cid,)).fetchone()
    conn.close()
    assert row is not None


def test_http_track_endpoint_404s_on_an_unresolvable_channel(monkeypatch):
    _no_network(monkeypatch)
    api.API_KEY = ""
    resp = client.post("/api/channels/track", json={"channel_id": "@nowhere2"})
    assert resp.status_code == 404


# ============================================================ bug 2: niche row on collect_channel

def test_collect_channel_with_niche_creates_a_niches_row(monkeypatch):
    _no_network(monkeypatch)
    cid = _uc("nicheA")
    monkeypatch.setattr(yt, "channels_list",
                        lambda k, ids, **kw: [_api_channel(cid, uploads="UUnichetest")])
    monkeypatch.setattr(yt, "playlist_items",
                        lambda k, pl, max_items=200: ([{"video_id": "vnichetest1"}], 1))
    monkeypatch.setattr(yt, "videos_list", lambda k, ids, **kw: [_api_video(v, cid) for v in ids])

    out = collector.collect_channel("test-key", cid, max_videos=10, niche="My Niche Label")

    assert out["channelId"] == cid
    conn = db.get_conn()
    row = conn.execute("SELECT slug, query, label FROM niches WHERE slug=?",
                       ("my-niche-label",)).fetchone()
    linked = conn.execute(
        "SELECT COUNT(*) FROM video_niches WHERE niche_slug=?", ("my-niche-label",)
    ).fetchone()[0]
    conn.close()
    assert row is not None
    assert row["query"] == "My Niche Label"
    assert linked == 1


def test_collect_channel_without_niche_does_not_touch_niches(monkeypatch):
    _no_network(monkeypatch)
    cid = _uc("nicheB")
    monkeypatch.setattr(yt, "channels_list",
                        lambda k, ids, **kw: [_api_channel(cid, uploads="UUnicheskip")])
    monkeypatch.setattr(yt, "playlist_items",
                        lambda k, pl, max_items=200: ([], 0))
    monkeypatch.setattr(yt, "videos_list", lambda k, ids, **kw: [])
    conn = db.get_conn()
    n_before = conn.execute("SELECT COUNT(*) FROM niches").fetchone()[0]
    conn.close()

    collector.collect_channel("test-key", cid, max_videos=10)

    conn = db.get_conn()
    n_after = conn.execute("SELECT COUNT(*) FROM niches").fetchone()[0]
    conn.close()
    assert n_after == n_before


# ============================================================ bug 3: viral preset=niche_all

def test_viral_niche_all_preset_drops_the_size_and_view_thresholds():
    conn = db.get_conn()
    now = db.now_iso()
    big = _api_channel(_uc("presetA"), subs=500_000)
    collector.store_channels(conn, [big], now)
    collector.store_videos(conn, [_api_video("vpreset1", big["id"], views=50)],
                           niche_slug="preset-niche", embed=False, now=now)
    conn.commit()
    conn.close()

    default = trends.viral_videos_small_channels(period="all", niche="preset-niche")
    assert default["matched"] == 0  # маленький ролик у крупного канала не проходит дефолт

    preset = trends.viral_videos_small_channels(period="all", niche="preset-niche",
                                                preset="niche_all")
    assert preset["matched"] == 1
    assert preset["filters"]["maxSubscribers"] is None


def test_viral_niche_all_preset_requires_a_niche():
    out = trends.viral_videos_small_channels(period="all", preset="niche_all")
    assert "error" in out
    assert out["results"] == []


# ============================================================ bug 4: fix-tracked

def test_fix_tracked_dry_run_reports_without_changing_anything(monkeypatch):
    _no_network(monkeypatch)
    cid = _uc("fixtrackedA")
    _seed_channel_with_handle(cid, "@fixtrackedhandle")
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO tracked_channels (channel_id, added_at, active) VALUES (?,?,1) "
        "ON CONFLICT (channel_id) DO NOTHING",
        ("@fixtrackedhandle", db.now_iso()))
    conn.commit()
    conn.close()

    res = T.fix_tracked(api_key=None, apply=False)

    assert {"was": "@fixtrackedhandle", "now": cid} in res["fixed"]
    conn = db.get_conn()
    still_there = conn.execute(
        "SELECT 1 FROM tracked_channels WHERE channel_id='@fixtrackedhandle'").fetchone()
    conn.close()
    assert still_there is not None  # dry-run -- ничего не поменялось


def test_fix_tracked_apply_rewrites_resolvable_rows_and_drops_the_rest(monkeypatch):
    _no_network(monkeypatch)
    cid = _uc("fixtrackedB")
    _seed_channel_with_handle(cid, "@fixtrackedapply")
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO tracked_channels (channel_id, added_at, active) VALUES (?,?,1) "
        "ON CONFLICT (channel_id) DO NOTHING",
        ("@fixtrackedapply", db.now_iso()))
    conn.execute(
        "INSERT INTO tracked_channels (channel_id, added_at, active) VALUES (?,?,1) "
        "ON CONFLICT (channel_id) DO NOTHING",
        ("@totallyunresolvable", db.now_iso()))
    conn.commit()
    conn.close()

    res = T.fix_tracked(api_key=None, apply=True)

    assert {"was": "@fixtrackedapply", "now": cid} in res["fixed"]
    assert "@totallyunresolvable" in res["removed"]
    conn = db.get_conn()
    fixed_row = conn.execute("SELECT 1 FROM tracked_channels WHERE channel_id=?",
                             (cid,)).fetchone()
    old_handle = conn.execute(
        "SELECT 1 FROM tracked_channels WHERE channel_id='@fixtrackedapply'").fetchone()
    unresolved_row = conn.execute(
        "SELECT 1 FROM tracked_channels WHERE channel_id='@totallyunresolvable'").fetchone()
    conn.close()
    assert fixed_row is not None
    assert old_handle is None
    assert unresolved_row is None


if __name__ == "__main__":
    class _Monkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._undo):
                setattr(obj, name, value)

    setup_module()
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

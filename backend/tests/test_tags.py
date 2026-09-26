"""Tests for application/tags.py: tag_videos/list_video_tags/tag_stats and
source protection end-to-end through real Postgres -- the repository
functions use named %()s psycopg2 params and a raw cursor (for the
conditional ON CONFLICT ... WHERE guard), worth proving against the real
driver rather than a sqlite double. Same throwaway-schema setup as
test_smoke.py. Run with pytest, or directly: python3 tests/test_tags.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db  # noqa: E402
from application import discovery as trends  # noqa: E402
from application import tags as T  # noqa: E402


def setup_module(_=None):
    db.init_db()


def _channel(cid):
    return {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None,
        "description": "", "default_language": None, "subscriber_count": 1000,
        "video_count": 1, "view_count": 1000, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    }


def _video(vid, cid, title="video"):
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": "2026-01-01T00:00:00Z", "duration_seconds": 300,
        "view_count": 100, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def _seed_video_in_niche(video_id, channel_id, niche_slug):
    conn = db.get_conn()
    db.upsert_channel(conn, _channel(channel_id))
    db.upsert_video(conn, _video(video_id, channel_id))
    db.upsert_niche(conn, niche_slug, niche_slug, niche_slug)
    db.link_video_niche(conn, video_id, niche_slug)
    conn.commit()
    conn.close()


# --------------------------------------------------- tag_videos / protection

def test_tag_videos_requires_a_valid_source():
    _seed_video_in_niche("vtag1", "UCtag0000000000000000001", "n-tags-1")
    try:
        T.tag_videos([{"video_id": "vtag1", "tag_group": "theme", "tags": ["x"]}],
                     source="not-a-real-source")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_llm_cannot_overwrite_a_manual_tag_but_can_add_a_new_one():
    _seed_video_in_niche("vtag2", "UCtag0000000000000000002", "n-tags-2")
    T.tag_videos([{"video_id": "vtag2", "tag_group": "theme", "tags": ["fear"]}],
                source="manual")

    r = T.tag_videos([{"video_id": "vtag2", "tag_group": "theme", "tags": ["fear", "howto"]}],
                     source="llm")

    assert r["written"] == 1              # howto is new, written
    assert r["blockedByProtection"] == 1  # fear is protected, blocked

    tags = {t["tag"]: t["source"] for t in T.list_video_tags(video_id="vtag2")}
    assert tags == {"fear": "manual", "howto": "llm"}


def test_manual_can_overwrite_a_claude_mcp_tag():
    _seed_video_in_niche("vtag3", "UCtag0000000000000000003", "n-tags-3")
    T.tag_videos([{"video_id": "vtag3", "tag_group": "theme", "tags": ["fear"]}],
                source="claude-mcp")
    T.tag_videos([{"video_id": "vtag3", "tag_group": "theme", "tags": ["fear"]}],
                source="manual")

    tags = {t["tag"]: t["source"] for t in T.list_video_tags(video_id="vtag3")}
    assert tags == {"fear": "manual"}


def test_replace_mode_keeps_protected_tags_when_llm_replaces():
    _seed_video_in_niche("vtag4", "UCtag0000000000000000004", "n-tags-4")
    T.tag_videos([{"video_id": "vtag4", "tag_group": "theme", "tags": ["a", "b"]}],
                source="manual", replace=True)

    T.tag_videos([{"video_id": "vtag4", "tag_group": "theme", "tags": ["c"]}],
                source="llm", replace=True)

    tags = {t["tag"]: t["source"] for t in T.list_video_tags(video_id="vtag4")}
    assert tags == {"a": "manual", "b": "manual", "c": "llm"}


def test_replace_mode_from_manual_drops_tags_not_in_the_new_list():
    _seed_video_in_niche("vtag5", "UCtag0000000000000000005", "n-tags-5")
    T.tag_videos([{"video_id": "vtag5", "tag_group": "theme", "tags": ["a", "b"]}],
                source="manual", replace=True)

    T.tag_videos([{"video_id": "vtag5", "tag_group": "theme", "tags": ["b", "c"]}],
                source="manual", replace=True)

    tags = {t["tag"] for t in T.list_video_tags(video_id="vtag5")}
    assert tags == {"b", "c"}


def test_list_video_tags_requires_exactly_one_filter():
    for kwargs in ({}, {"niche": "n", "video_id": "v"}):
        try:
            T.list_video_tags(**kwargs)
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_list_video_tags_by_niche_scopes_through_video_niches():
    _seed_video_in_niche("vtag6", "UCtag0000000000000000006", "n-tags-6")
    _seed_video_in_niche("vtag7", "UCtag0000000000000000007", "n-tags-other")
    T.tag_videos([{"video_id": "vtag6", "tag_group": "theme", "tags": ["x"]}], source="manual")
    T.tag_videos([{"video_id": "vtag7", "tag_group": "theme", "tags": ["y"]}], source="manual")

    rows = T.list_video_tags(niche="n-tags-6")

    assert {r["videoId"] for r in rows} == {"vtag6"}


# --------------------------------------------------- tag_stats

def test_tag_stats_on_a_niche_with_nothing_collected():
    out = T.tag_stats("no-such-niche-at-all", "theme")
    assert out["found"] is False
    assert "hint" in out


def test_tag_stats_wires_population_and_tags_through_to_the_domain_function(monkeypatch):
    _seed_video_in_niche("vtag8", "UCtag0000000000000000008", "n-tags-stats")
    T.tag_videos([{"video_id": "vtag8", "tag_group": "theme", "tags": ["fear"]}],
                source="manual")

    fake_rows = [
        {"video_id": "vtag8", "view_count": 1000, "outlierScore": 4.0,
         "outlierScoreNexlev": 1.0, "ageDays": 60},
        {"video_id": "vtag9-not-tagged", "view_count": 500, "outlierScore": 0.5,
         "outlierScoreNexlev": 0.1, "ageDays": 60},
    ]
    monkeypatch.setattr(trends, "load_window", lambda **kw: fake_rows)

    out = T.tag_stats("n-tags-stats", "theme", outlier_threshold=3.0)

    assert out["found"] is True
    assert out["totalVideos"] == 2
    assert out["baseRate"] == 50.0
    fear = next(t for t in out["tags"] if t["tag"] == "fear")
    assert fear["videos"] == 1
    assert fear["lift"] == 2.0


def test_tag_stats_excludes_videos_younger_than_exclude_recent_days(monkeypatch):
    fake_rows = [
        {"video_id": "vold", "view_count": 100, "outlierScore": 1.0,
         "outlierScoreNexlev": 1.0, "ageDays": 60},
        {"video_id": "vnew", "view_count": 100, "outlierScore": 1.0,
         "outlierScoreNexlev": 1.0, "ageDays": 2},
    ]
    monkeypatch.setattr(trends, "load_window", lambda **kw: fake_rows)

    out = T.tag_stats("whatever-niche", "theme", exclude_recent_days=30)

    assert out["totalVideos"] == 1


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

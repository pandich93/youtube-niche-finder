"""HTTP-layer tests for the video-tags endpoints (interfaces/http/api.py):
POST/GET /api/tags, GET /api/tags/stats. Same throwaway-schema setup as
test_http_rate_limit.py -- no network, no YouTube key. Run with pytest, or
directly: python3 tests/test_tags_http.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402

client = TestClient(api.app)


def setup_module(_=None):
    db.init_db()


def _seed_video(video_id, channel_id):
    conn = db.get_conn()
    db.upsert_channel(conn, {
        "channel_id": channel_id, "title": channel_id, "custom_url": None,
        "country": None, "description": "", "default_language": None,
        "subscriber_count": 100, "video_count": 1, "view_count": 100,
        "thumbnail": None, "published_at": None, "topic_categories": None,
        "keywords": None, "uploads_playlist": None, "hidden_subs": 0,
    })
    db.upsert_video(conn, {
        "video_id": video_id, "channel_id": channel_id, "title": "t", "description": "",
        "published_at": "2026-01-01T00:00:00Z", "duration_seconds": 300,
        "view_count": 100, "like_count": 0, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    })
    conn.commit()
    conn.close()


def test_post_tags_writes_then_get_tags_reads_them_back():
    _seed_video("vhttp1", "UChttp0000000000000000001")

    r = client.post("/api/tags", json={
        "items": [{"video_id": "vhttp1", "tag_group": "theme", "tags": ["fear"]}],
        "source": "manual",
    })
    assert r.status_code == 200
    assert r.json()["written"] == 1

    r = client.get("/api/tags", params={"video_id": "vhttp1"})
    assert r.status_code == 200
    tags = r.json()["tags"]
    assert [t["tag"] for t in tags] == ["fear"]
    assert tags[0]["source"] == "manual"


def test_post_tags_with_a_bad_source_is_a_400():
    r = client.post("/api/tags", json={
        "items": [{"video_id": "vhttp1", "tag_group": "theme", "tags": ["x"]}],
        "source": "not-a-real-source",
    })
    assert r.status_code == 400


def test_get_tags_without_niche_or_video_id_is_a_400():
    r = client.get("/api/tags")
    assert r.status_code == 400


def test_get_tags_stats_on_an_empty_niche_gives_a_hint():
    r = client.get("/api/tags/stats", params={"niche": "no-such-niche-http", "tag_group": "theme"})
    assert r.status_code == 200
    assert r.json()["found"] is False


if __name__ == "__main__":
    setup_module()

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)

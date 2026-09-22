"""Integration tests for application/transcripts.py and its HTTP endpoints
(stage 19) -- real Postgres, real local fastembed model (no network; same
as test_mcp_tools.py's embedding tests). Run with pytest, or directly:
python3 tests/test_transcripts.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db            # noqa: E402
from application import transcripts as TR         # noqa: E402
import interfaces.http.api as api                   # noqa: E402

client = TestClient(api.app)

TIMED_TRANSCRIPT = "\n".join([
    "0:00", "car wash equipment overview and startup costs",
    "0:15", "how much you can realistically earn per month",
    "0:30", "the biggest mistakes new owners make",
])


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


def _video(vid, cid, title):
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": "2026-01-01T00:00:00Z", "duration_seconds": 300,
        "view_count": 5000, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def _seed(video_id, channel_id, title, niche_slug=None):
    conn = db.get_conn()
    db.upsert_channel(conn, _channel(channel_id))
    db.upsert_video(conn, _video(video_id, channel_id, title))
    if niche_slug:
        db.upsert_niche(conn, niche_slug, niche_slug, niche_slug)
        db.link_video_niche(conn, video_id, niche_slug)
    conn.commit()
    conn.close()


def test_request_transcript_queues_it_as_pending():
    _seed("vtr1", "UCtr0000000000000000001", "car wash channel")

    out = TR.request_transcript("vtr1", reason="checking car wash niche")

    assert out["status"] == "pending"
    queue = TR.list_transcript_queue(status="pending")
    assert any(q["videoId"] == "vtr1" for q in queue)


def test_save_transcript_parses_chunks_embeds_and_marks_ready():
    _seed("vtr2", "UCtr0000000000000000002", "car wash channel 2")
    TR.request_transcript("vtr2", reason="r", compare_group="cars")

    out = TR.save_transcript("vtr2", TIMED_TRANSCRIPT)

    assert out["status"] == "ready"
    assert out["chunks"] >= 1
    assert out["hasTimestamps"] is True

    queue = TR.list_transcript_queue(status="ready")
    assert any(q["videoId"] == "vtr2" for q in queue)

    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM transcript_chunks WHERE video_id=?",
                     ("vtr2",)).fetchone()["n"]
    conn.close()
    assert n == out["chunks"]


def test_save_transcript_with_empty_text_marks_error_not_a_crash():
    _seed("vtr3", "UCtr0000000000000000003", "empty transcript video")

    out = TR.save_transcript("vtr3", "   \n\n  ")

    assert out["status"] == "error"
    assert "error" in out
    queue = TR.list_transcript_queue(status="error")
    assert any(q["videoId"] == "vtr3" for q in queue)


def test_reindex_transcript_reuses_the_stored_raw_text():
    _seed("vtr4", "UCtr0000000000000000004", "reindex me")
    TR.save_transcript("vtr4", TIMED_TRANSCRIPT)

    out = TR.reindex_transcript("vtr4")

    assert out["status"] == "ready"
    assert out["chunks"] >= 1


def test_reindex_transcript_without_a_saved_transcript_is_a_clear_error():
    out = TR.reindex_transcript("v-never-saved")
    assert out["status"] == "error"


def test_search_transcripts_finds_the_saved_chunk_by_topic(monkeypatch):
    _seed("vtr5", "UCtr0000000000000000005", "car wash business")
    TR.save_transcript("vtr5", TIMED_TRANSCRIPT)

    out = TR.search_transcripts("how much money can you make")

    assert out["results"]
    assert any(r["videoId"] == "vtr5" for r in out["results"])
    top = out["results"][0]
    assert "url" in top and top["url"].startswith("https://youtu.be/")


def test_search_transcripts_niche_filter_excludes_other_niches():
    _seed("vtr6", "UCtr0000000000000000006", "in niche a", niche_slug="n-tr-a")
    _seed("vtr7", "UCtr0000000000000000007", "in niche b", niche_slug="n-tr-b")
    TR.save_transcript("vtr6", "0:00\ncar wash startup costs and equipment")
    TR.save_transcript("vtr7", "0:00\ncar wash startup costs and equipment")

    out = TR.search_transcripts("startup costs", niche="n-tr-a")

    video_ids = {r["videoId"] for r in out["results"]}
    assert "vtr6" in video_ids
    assert "vtr7" not in video_ids


def test_search_transcripts_requires_a_query():
    try:
        TR.search_transcripts("")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_search_transcripts_with_nothing_saved_gives_a_hint():
    conn = db.get_conn()
    conn.execute("DELETE FROM transcript_chunks")
    conn.commit()
    conn.close()

    out = TR.search_transcripts("anything at all")

    assert out["results"] == []
    assert "hint" in out


def test_http_endpoints_roundtrip():
    _seed("vtr8", "UCtr0000000000000000008", "http roundtrip video")

    r = client.post("/api/transcripts/request", json={"videoId": "vtr8", "reason": "test"})
    assert r.status_code == 200
    assert r.json()["status"] == "pending"

    r = client.get("/api/transcripts/queue", params={"status": "pending"})
    assert r.status_code == 200
    assert any(q["videoId"] == "vtr8" for q in r.json()["queue"])

    r = client.post(f"/api/transcripts/vtr8/save", json={"text": TIMED_TRANSCRIPT})
    assert r.status_code == 200
    assert r.json()["status"] == "ready"

    r = client.get("/api/transcripts/search", params={"query": "mistakes new owners make"})
    assert r.status_code == 200
    assert any(res["videoId"] == "vtr8" for res in r.json()["results"])


def test_http_save_rejects_empty_text_with_400():
    r = client.post("/api/transcripts/vtr-missing/save", json={"text": "  "})
    assert r.status_code == 400


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

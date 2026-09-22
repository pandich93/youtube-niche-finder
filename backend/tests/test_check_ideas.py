"""Tests for application/search.py:check_ideas (stage 17) and its HTTP
endpoint POST /api/ideas/check. Same throwaway-schema setup as
test_niche_videos.py. Run with pytest, or directly:
python3 tests/test_check_ideas.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db            # noqa: E402
from application import search as Q              # noqa: E402
import interfaces.http.api as api                 # noqa: E402

client = TestClient(api.app)


def setup_module(_=None):
    db.init_db()


def _iso_days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _channel(cid):
    return {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None,
        "description": "", "default_language": None, "subscriber_count": 1000,
        "video_count": 1, "view_count": 1000, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    }


def _video(vid, cid, title, published_days_ago, view_count=100):
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": _iso_days_ago(published_days_ago), "duration_seconds": 300,
        "view_count": view_count, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": _iso_days_ago(published_days_ago), "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def _seed(video_id, channel_id, title, days_ago, niche_slug, view_count=100):
    conn = db.get_conn()
    db.upsert_channel(conn, _channel(channel_id))
    db.upsert_video(conn, _video(video_id, channel_id, title, days_ago, view_count))
    db.upsert_niche(conn, niche_slug, niche_slug, niche_slug)
    db.link_video_niche(conn, video_id, niche_slug)
    conn.commit()
    conn.close()


def test_rejects_empty_ideas():
    try:
        Q.check_ideas([])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_rejects_more_than_50_ideas():
    try:
        Q.check_ideas([f"idea {i}" for i in range(51)])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_idea_with_no_matching_title_is_free():
    _seed("videa1", "UCidea0000000000000000001", "totally unrelated content",
         days_ago=200, niche_slug="n-idea-1")

    out = Q.check_ideas(["car wash"], niche="n-idea-1")

    idea = out["ideas"][0]
    assert idea["verdict"] == "free"
    assert idea["matches"] == []


def test_idea_matched_recently_is_recent_regardless_of_outlier():
    _seed("videa2", "UCidea0000000000000000002", "Car Wash Business Tour",
         days_ago=10, niche_slug="n-idea-2")

    out = Q.check_ideas(["car wash"], niche="n-idea-2", recent_days=90)

    idea = out["ideas"][0]
    assert idea["verdict"] == "recent"
    assert idea["daysSinceLastCoverage"] < 90


def test_idea_matched_long_ago_without_baseline_is_flopped():
    # a single video with no prior channel history has no baseline -> outlierScore
    # is None, which check_ideas must NOT treat as "proven"
    _seed("videa3", "UCidea0000000000000000003", "Funeral Home Documentary",
         days_ago=200, niche_slug="n-idea-3")

    out = Q.check_ideas(["funeral home"], niche="n-idea-3", recent_days=90)

    idea = out["ideas"][0]
    assert idea["verdict"] == "flopped"
    assert idea["bestOutlierScore"] is None


def test_title_match_is_case_insensitive_substring():
    _seed("videa4", "UCidea0000000000000000004", "THE BEST Laundromat Tour Ever",
         days_ago=200, niche_slug="n-idea-4")

    out = Q.check_ideas(["laundromat"], niche="n-idea-4")

    assert out["ideas"][0]["matchCount"] == 1
    assert out["ideas"][0]["matches"][0]["matchedBy"] == "title"


def test_multiple_ideas_in_one_call_are_independent():
    _seed("videa5", "UCidea0000000000000000005", "Pet Grooming Salon",
         days_ago=200, niche_slug="n-idea-5")

    out = Q.check_ideas(["pet grooming", "something nobody covers"], niche="n-idea-5")

    verdicts = {i["idea"]: i["verdict"] for i in out["ideas"]}
    assert verdicts["pet grooming"] == "flopped"  # matched, old, no baseline
    assert verdicts["something nobody covers"] == "free"


def test_http_endpoint_roundtrips():
    _seed("videa6", "UCidea0000000000000000006", "Storage Unit Auction Flip",
         days_ago=5, niche_slug="n-idea-6")

    r = client.post("/api/ideas/check", json={"ideas": ["storage unit"], "niche": "n-idea-6"})

    assert r.status_code == 200
    assert r.json()["ideas"][0]["verdict"] == "recent"


def test_http_endpoint_rejects_empty_ideas_with_400():
    r = client.post("/api/ideas/check", json={"ideas": []})
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

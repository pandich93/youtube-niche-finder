"""Tests for application/search.py:niche_videos (stage 15's scatter-chart
data source) and its HTTP endpoint GET /api/niches/{slug}/videos. Same
throwaway-schema setup as test_tags.py. Run with pytest, or directly:
python3 tests/test_niche_videos.py
"""
import os
import sys

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


def _channel(cid):
    return {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None,
        "description": "", "default_language": None, "subscriber_count": 1000,
        "video_count": 1, "view_count": 1000, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    }


def _video(vid, cid, title="video", is_short=0, duration=300):
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": "2026-01-01T00:00:00Z", "duration_seconds": duration,
        "view_count": 100, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": is_short, "topic_categories": None, "live_content": None,
    }


def _seed(video_id, channel_id, niche_slug, **video_kwargs):
    conn = db.get_conn()
    db.upsert_channel(conn, _channel(channel_id))
    db.upsert_video(conn, _video(video_id, channel_id, **video_kwargs))
    db.upsert_niche(conn, niche_slug, niche_slug, niche_slug)
    db.link_video_niche(conn, video_id, niche_slug)
    conn.commit()
    conn.close()


def test_niche_videos_on_an_empty_niche_gives_a_hint():
    out = Q.niche_videos("no-such-niche-scatter")
    assert out["found"] is False
    assert out["videos"] == []


def test_niche_videos_lists_every_video_with_outlier_fields():
    _seed("vscat1", "UCscat0000000000000000001", "n-scat-1")
    _seed("vscat2", "UCscat0000000000000000001", "n-scat-1")

    out = Q.niche_videos("n-scat-1")

    assert out["found"] is True
    assert out["videoCount"] == 2
    ids = {v["videoId"] for v in out["videos"]}
    assert ids == {"vscat1", "vscat2"}
    v = out["videos"][0]
    for key in ("outlierScore", "outlierScoreRolling", "outlierScorePeriod",
               "channelId", "durationSeconds", "isShort", "ageDays"):
        assert key in v


def test_niche_videos_channel_ids_filters_to_a_subset():
    _seed("vscat3", "UCscat0000000000000000002", "n-scat-2")
    _seed("vscat4", "UCscat0000000000000000003", "n-scat-2")

    out = Q.niche_videos("n-scat-2", channel_ids=["UCscat0000000000000000002"])

    assert [v["videoId"] for v in out["videos"]] == ["vscat3"]


def test_niche_videos_include_shorts_false_drops_shorts():
    _seed("vscat5", "UCscat0000000000000000004", "n-scat-3",
         is_short=1, duration=30)
    _seed("vscat6", "UCscat0000000000000000004", "n-scat-3",
         is_short=0, duration=300)

    out = Q.niche_videos("n-scat-3", include_shorts=False)

    assert [v["videoId"] for v in out["videos"]] == ["vscat6"]


def test_http_endpoint_returns_the_same_shape():
    _seed("vscat7", "UCscat0000000000000000005", "n-scat-4")

    r = client.get("/api/niches/n-scat-4/videos")

    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True
    assert body["videos"][0]["videoId"] == "vscat7"


def test_http_endpoint_channels_query_param_is_comma_separated():
    _seed("vscat8", "UCscat0000000000000000006", "n-scat-5")
    _seed("vscat9", "UCscat0000000000000000007", "n-scat-5")

    r = client.get("/api/niches/n-scat-5/videos",
                   params={"channels": "UCscat0000000000000000006"})

    assert r.status_code == 200
    assert [v["videoId"] for v in r.json()["videos"]] == ["vscat8"]


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

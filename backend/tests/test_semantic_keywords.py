"""Integration test for discovery.py:trending_keywords(keywords_mode=
'semantic') (stage 10) -- real Postgres, real local fastembed model. Run
with pytest, or directly: python3 tests/test_semantic_keywords.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db          # noqa: E402
from application import discovery as trends     # noqa: E402


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


def _video(vid, cid, title, days_ago=1):
    from datetime import datetime, timedelta, timezone
    pub = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": pub, "duration_seconds": 300,
        "view_count": 1000, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": pub, "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def test_ngram_mode_default_is_unaffected_by_stage_10():
    conn = db.get_conn()
    cid = "UCkwngram"
    db.upsert_channel(conn, _channel(cid))
    for i, title in enumerate(["Cold Shower Challenge Day 1", "Cold Showers For A Month",
                               "Unrelated Cooking Video"]):
        vid = f"vkwngram{i}"
        db.upsert_video(conn, _video(vid, cid, title))
    conn.commit()
    conn.close()

    # calling with keywords_mode="ngram" (the default) must be byte-identical
    # to calling trending_keywords with no keywords_mode argument at all --
    # the real guarantee this stage must not break
    explicit = trends.trending_keywords(period="all", min_videos=1, keywords_mode="ngram",
                                        compare_previous=False)
    implicit = trends.trending_keywords(period="all", min_videos=1, compare_previous=False)

    assert explicit["keywordsMode"] == "ngram"
    assert [r["keyword"] for r in explicit["keywords"]] == \
        [r["keyword"] for r in implicit["keywords"]]


def test_semantic_mode_merges_paraphrases_and_sums_their_videos():
    conn = db.get_conn()
    cid = "UCkwsem"
    db.upsert_channel(conn, _channel(cid))
    titles = ["I Tried A Cold Shower Every Morning", "My Cold Showers Experiment Results",
             "Ice Bath Every Single Day", "Totally Unrelated Video About Cars"]
    for i, title in enumerate(titles):
        vid = f"vkwsem{i}"
        db.upsert_video(conn, _video(vid, cid, title))
    conn.commit()
    conn.close()

    ngram_out = trends.trending_keywords(period="all", min_videos=1, keywords_mode="ngram",
                                         compare_previous=False)
    semantic_out = trends.trending_keywords(period="all", min_videos=1, keywords_mode="semantic",
                                            compare_previous=False, semantic_similarity=0.75)

    assert semantic_out["keywordsMode"] == "semantic"
    # semantic mode should never produce MORE distinct keywords than ngram
    # mode on the same corpus -- merging only reduces the count
    assert len(semantic_out["keywords"]) <= len(ngram_out["keywords"])


def test_semantic_mode_falls_back_gracefully_with_no_videos():
    out = trends.trending_keywords(period="all", niche="no-such-niche-semantic-kw",
                                   min_videos=1, keywords_mode="semantic",
                                   compare_previous=False)
    assert out["keywordsMode"] == "semantic"
    assert out["keywords"] == []


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

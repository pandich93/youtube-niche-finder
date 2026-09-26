"""Tests for stage 06 (pgvector): the fallback Python-cosine path always
runs and is tested unconditionally (regression coverage for the refactor
that added the pgvector branch alongside it); the pgvector branch itself
only runs -- and is only meaningful -- against a Postgres that actually has
the extension (pgvector/pgvector:pg16), so those tests check
db.pgvector_available() and show up as pytest skips otherwise rather than
failing a plain postgres:16 setup.

Run with pytest, or directly: python3 tests/test_pgvector.py
To exercise the pgvector-path tests for real:
  docker run -d --name nf-pgvector-test -p 5544:5432 \
    -e POSTGRES_DB=niches -e POSTGRES_USER=niches -e POSTGRES_PASSWORD=niches \
    pgvector/pgvector:pg16
  NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5544/niches \
    python3 tests/test_pgvector.py
  docker rm -f nf-pgvector-test
"""
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import numpy as np                                     # noqa: E402
import pytest                                          # noqa: E402
import infrastructure.postgres as db                    # noqa: E402
from application import search as Q                     # noqa: E402
from application import metadata_review as MR            # noqa: E402
import infrastructure.embeddings.fastembed_provider as emb  # noqa: E402



def _require_pgvector():
    """pgvector-path tests need the vector extension (init_db decides, so
    this runs inside the test, not at import). A visible skip, never a
    silent pass: CI once ran without pgvector and hid a crash that way."""
    if not db.pgvector_available():
        pytest.skip("this Postgres has no vector extension")


def setup_module(_=None):
    db.init_db()


def _rand_vec(seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=db.EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _channel(cid):
    return {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None,
        "description": "", "default_language": None, "subscriber_count": 1000,
        "video_count": 1, "view_count": 1000, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    }


def _video(vid, cid, title, seed):
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": "2026-01-01T00:00:00Z", "duration_seconds": 300,
        "view_count": 100, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": emb.to_blob(_rand_vec(seed)),
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def _seed_corpus(prefix, n=6):
    """n videos across 2 channels, deterministic pseudo-random embeddings --
    upsert_video's dual-write means embedding_v gets populated for free
    whenever pgvector is available."""
    conn = db.get_conn()
    for i in range(n):
        cid = f"UC{prefix}chan{i % 2}"
        vid = f"v{prefix}{i}"
        db.upsert_channel(conn, _channel(cid))
        # seed on prefix too: every test seeds into the same schema, and equal
        # seeds across corpora give identical vectors -- exact similarity ties
        # whose order pgvector and the Python fallback break differently
        seed = zlib.crc32(prefix.encode()) * 100 + i
        db.upsert_video(conn, _video(vid, cid, f"{prefix} video {i}", seed=seed))
    conn.commit()
    conn.close()
    return [f"v{prefix}{i}" for i in range(n)]


# --------------------------------------------------------- fallback path

def test_similar_videos_fallback_path_ranks_by_cosine_similarity():
    ids = _seed_corpus("fbv", n=4)
    # force the fallback branch regardless of what this DB actually has --
    # this is the refactor's regression test, not a pgvector-availability test
    import application.search as search_mod
    orig = db.pgvector_available
    search_mod.db.pgvector_available = lambda: False
    try:
        out = Q.similar_videos(ids[0], limit=10)
    finally:
        search_mod.db.pgvector_available = orig

    assert out["video_id"] == ids[0]
    assert out["candidatesConsidered"] == 3
    assert len(out["similar"]) == 3
    scores = [s["similarity"] for s in out["similar"]]
    assert scores == sorted(scores, reverse=True)


def test_similar_channels_fallback_path_returns_centroid_ranked_channels():
    ids = _seed_corpus("fbc", n=6)
    import application.search as search_mod
    orig = db.pgvector_available
    search_mod.db.pgvector_available = lambda: False
    try:
        target_channel = "UCfbcchan0"
        out = Q.similar_channels(target_channel, limit=5, min_videos_embedded=1)
    finally:
        search_mod.db.pgvector_available = orig

    assert out["channel_id"] == target_channel
    assert any(s["channelId"] == "UCfbcchan1" for s in out["similar"])


def test_near_duplicates_fallback_path_respects_min_similarity():
    _seed_corpus("fbd", n=3)
    import application.metadata_review as mr_mod
    orig = db.pgvector_available
    mr_mod.db.pgvector_available = lambda: False
    try:
        out = MR._near_duplicates("anything", "", channel_id="UCfbdchan0",
                                  min_similarity=2.0)  # impossible threshold
    finally:
        mr_mod.db.pgvector_available = orig
    assert out["near"] == []


# --------------------------------------------------------- pgvector path

def test_similar_videos_pgvector_path_matches_fallback_ranking():
    _require_pgvector()
    ids = _seed_corpus("pgv", n=5)

    out_pg = Q.similar_videos(ids[0], limit=10)

    import application.search as search_mod
    orig = db.pgvector_available
    search_mod.db.pgvector_available = lambda: False
    try:
        out_py = Q.similar_videos(ids[0], limit=10)
    finally:
        search_mod.db.pgvector_available = orig

    order_pg = [s["videoId"] for s in out_pg["similar"]]
    order_py = [s["videoId"] for s in out_py["similar"]]
    assert order_pg == order_py
    for a, b in zip(out_pg["similar"], out_py["similar"]):
        assert abs(a["similarity"] - b["similarity"]) < 1e-3


def test_similar_channels_pgvector_path_matches_fallback_ranking():
    _require_pgvector()
    _seed_corpus("pgc", n=6)
    target_channel = "UCpgcchan0"

    out_pg = Q.similar_channels(target_channel, limit=5, min_videos_embedded=1)

    import application.search as search_mod
    orig = db.pgvector_available
    search_mod.db.pgvector_available = lambda: False
    try:
        out_py = Q.similar_channels(target_channel, limit=5, min_videos_embedded=1)
    finally:
        search_mod.db.pgvector_available = orig

    assert [s["channelId"] for s in out_pg["similar"]] == \
        [s["channelId"] for s in out_py["similar"]]
    # both branches count every embedded channel other than the target, before
    # the min_videos_embedded filter and the limit
    assert out_pg["candidatesConsidered"] == out_py["candidatesConsidered"]


def test_backfill_migrates_existing_blob_only_rows_into_embedding_v():
    _require_pgvector()
    conn = db.get_conn()
    db.upsert_channel(conn, _channel("UCbackfillchan"))
    # write BLOB directly, bypassing upsert_video's dual-write, to simulate a
    # row written before pgvector was ever enabled
    conn.execute(
        "INSERT INTO videos (video_id, channel_id, title, embedding, first_seen_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT (video_id) DO UPDATE SET embedding=excluded.embedding",
        ("vbackfill1", "UCbackfillchan", "t", emb.to_blob(_rand_vec(99)), db.now_iso()))
    conn.commit()
    conn.close()

    from infrastructure.postgres.schema import _backfill_embedding_v
    conn = db.get_conn()
    migrated = _backfill_embedding_v(conn)
    row = conn.execute(
        "SELECT embedding_v IS NOT NULL AS has_v FROM videos WHERE video_id=?",
        ("vbackfill1",)).fetchone()
    conn.close()
    assert migrated >= 1
    assert row["has_v"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-rs", "-p", "no:cacheprovider"]))

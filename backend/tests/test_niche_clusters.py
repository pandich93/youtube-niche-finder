"""Integration tests for application/niche_clusters.py (stage 08) -- real
Postgres, real local fastembed model, LLM provider monkeypatched (same
pattern as test_enrichment.py). Run with pytest, or directly:
python3 tests/test_niche_clusters.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import numpy as np  # noqa: E402

import infrastructure.embeddings.fastembed_provider as emb  # noqa: E402
import infrastructure.postgres as db  # noqa: E402
from application import niche_clusters as NCL  # noqa: E402
from infrastructure.llm import factory  # noqa: E402
from infrastructure.llm.base import LLMResult  # noqa: E402
from infrastructure.llm.null import NullProvider  # noqa: E402


def setup_module(_=None):
    db.init_db()


class _StubProvider:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def complete_json(self, system, user, schema, *, model=None, max_tokens=1024):
        self.calls += 1
        return self.results.pop(0) if self.results else None


def _rand_vec(seed, offset):
    rng = np.random.default_rng(seed)
    v = rng.normal(scale=0.3, size=384).astype(np.float32) + offset
    return v / np.linalg.norm(v)


def _channel(cid, subs=1000):
    return {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None,
        "description": "", "default_language": None, "subscriber_count": subs,
        "video_count": 1, "view_count": 1000, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    }


def _video(vid, cid, title, seed, offset, views=100):
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": "2026-01-01T00:00:00Z", "duration_seconds": 300,
        "view_count": views, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": emb.to_blob(_rand_vec(seed, offset)),
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def _clear_clusters():
    conn = db.get_conn()
    conn.execute("DELETE FROM niche_clusters")
    conn.commit()
    conn.close()


def test_too_few_channels_gives_a_hint_not_a_crash():
    _clear_clusters()
    conn = db.get_conn()
    db.upsert_channel(conn, _channel("UCcl0000000000000000001"))
    db.upsert_video(conn, _video("vcl1", "UCcl0000000000000000001", "solo video", 1, 0.0))
    conn.commit()
    conn.close()

    out = NCL.compute_clusters(k=2)

    assert out["clusters"] == []
    assert "hint" in out

    # clean up so this channel's embedded video doesn't pollute later
    # tests' channelsConsidered/channelCount counts in this shared schema
    conn = db.get_conn()
    conn.execute("DELETE FROM videos WHERE video_id = 'vcl1'")
    conn.execute("DELETE FROM channels WHERE channel_id = 'UCcl0000000000000000001'")
    conn.commit()
    conn.close()


def test_compute_clusters_groups_channels_by_embedding_and_names_them(monkeypatch):
    _clear_clusters()
    stub = _StubProvider([
        LLMResult(data={"name": "Group A", "description": "d", "audience": "a"},
                  model="m/x", prompt_tokens=1, completion_tokens=1, cost_usd=0.001),
        LLMResult(data={"name": "Group B", "description": "d", "audience": "a"},
                  model="m/x", prompt_tokens=1, completion_tokens=1, cost_usd=0.001),
    ])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    monkeypatch.setattr(NCL, "MIN_CHANNELS", 4)

    conn = db.get_conn()
    # two well-separated groups of channels, offset in embedding space
    for i in range(4):
        cid = f"UCclA{i}"
        db.upsert_channel(conn, _channel(cid, subs=1000))
        db.upsert_video(conn, _video(f"vclA{i}", cid, f"group A video {i}", i, 5.0, views=1000))
    for i in range(4):
        cid = f"UCclB{i}"
        db.upsert_channel(conn, _channel(cid, subs=500000))  # over the competition threshold
        db.upsert_video(conn, _video(f"vclB{i}", cid, f"group B video {i}", i, -5.0, views=1000))
    conn.commit()
    conn.close()

    out = NCL.compute_clusters(k=2)

    # Assertions key off channelIds membership, not exact totals/counts --
    # other test files share this Postgres schema and may leave their own
    # embedded-video channels behind, which would otherwise land in one of
    # these two clusters and throw off a plain count.
    a_ids = {f"UCclA{i}" for i in range(4)}
    b_ids = {f"UCclB{i}" for i in range(4)}
    assert len(out["clusters"]) == 2
    names = {c["name"] for c in out["clusters"]}
    assert names == {"Group A", "Group B"}

    a_cluster = next(c for c in out["clusters"]
                     if a_ids.issubset(set(c["channelIds"])))
    b_cluster = next(c for c in out["clusters"]
                     if b_ids.issubset(set(c["channelIds"])))
    assert a_cluster is not b_cluster  # the two groups landed in different clusters
    assert b_cluster["competitionCount"] >= 4  # all of group B is over the threshold


def test_compute_clusters_falls_back_to_top_tags_without_llm():
    _clear_clusters()
    import infrastructure.llm.factory as factory_mod

    class _Null:
        def complete_json(self, *a, **kw):
            return None
    orig = factory_mod.get_provider
    factory_mod.get_provider = lambda: NullProvider()
    try:
        conn = db.get_conn()
        for i in range(4):
            cid = f"UCclN{i}"
            db.upsert_channel(conn, _channel(cid))
            vid = f"vclN{i}"
            db.upsert_video(conn, _video(vid, cid, f"fallback video {i}", i, 0.0))
            db.upsert_video_tag(conn, vid, "theme", "cooking", "manual")
        conn.commit()
        conn.close()

        out = NCL.compute_clusters(k=1)

        assert len(out["clusters"]) == 1
        assert "cooking" in out["clusters"][0]["name"]
    finally:
        factory_mod.get_provider = orig


def test_niche_map_reads_back_stored_clusters_sorted_by_opportunity(monkeypatch):
    _clear_clusters()
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO niche_clusters (cluster_id, name, channel_count, "
        "median_outlier_score, competition_count, channel_ids, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("cluster-low", "Low opportunity", 3, 1.0, 5, "[]", db.now_iso()))
    conn.execute(
        "INSERT INTO niche_clusters (cluster_id, name, channel_count, "
        "median_outlier_score, competition_count, channel_ids, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("cluster-high", "High opportunity", 3, 5.0, 0, "[]", db.now_iso()))
    conn.commit()
    conn.close()

    out = NCL.niche_map()

    assert [c["clusterId"] for c in out["clusters"]] == ["cluster-high", "cluster-low"]


def test_niche_map_with_nothing_computed_gives_a_hint():
    _clear_clusters()
    out = NCL.niche_map()
    assert out["clusters"] == []
    assert "hint" in out


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

"""Tests for application/content_gaps.py (plan 03): viewer questions from a
niche's comments checked against what is already collected. Throwaway schema
(tests/schema_scope.py); YouTube (collector.video_comments), the LLM provider
and the embedding model are monkeypatched -- no network.
Run with pytest, or directly: python3 tests/test_content_gaps.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
from application import collecting as collector  # noqa: E402
from application import content_gaps as CG  # noqa: E402
from infrastructure.llm import factory  # noqa: E402
from infrastructure.llm.null import NullProvider  # noqa: E402

NOW = datetime.now(timezone.utc)
CH = "UC" + "gapschan".ljust(22, "0")
NICHE = "bread"

QUESTIONS = {
    "gv1": [{"text": "How do you keep a sourdough starter alive in winter?", "likeCount": 40,
             "author": "Alice"},
            {"text": "Great video, thanks!", "likeCount": 100, "author": "Bob"}],
    "gv2": [{"text": "how do you keep a sourdough starter alive in winter??", "likeCount": 3,
             "author": "Carol"},
            {"text": "Please make a video about gluten free bread", "likeCount": 5,
             "author": "Dan"}],
    "gv3": [{"text": "Can you do a tutorial on focaccia art", "likeCount": 1,
             "author": "Eve"}],
}


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    conn = db.get_conn()
    for t in ("video_insights", "transcript_chunks", "video_niches", "videos", "channels",
              "niches", "llm_cache", "llm_usage"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO channels (channel_id, title) VALUES (?, ?)", (CH, "Bread Lab"))
    conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", (NICHE, "bread", "Bread"))
    for i, (vid, title, views) in enumerate((
            ("gv1", "Sourdough basics", 90000), ("gv2", "Rye loaf", 50000),
            ("gv3", "Focaccia in 10 minutes", 30000), ("gv4", "Bread starter in winter", 100))):
        conn.execute(
            "INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
            "comment_count) VALUES (?,?,?,?,?,?)",
            (vid, CH, title, (NOW - timedelta(days=30 + i)).isoformat(), views, 10))
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (vid, NICHE))
    conn.commit()
    conn.close()
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    monkeypatch.setattr(CG, "_embed", lambda texts: None)   # no model in tests


def _fake_comments(calls):
    def _video_comments(api_key, video_id, max_results=100, order="relevance", search_terms=None):
        calls.append(video_id)
        items = QUESTIONS.get(video_id, [])
        return {"video_id": video_id, "count": len(items), "comments": items,
                "quota": {"units_from_shared_pool": 1, "search_calls": 0}}
    return _video_comments


def test_rules_mode_finds_gaps_and_merges_repeated_questions(monkeypatch):
    calls = []
    monkeypatch.setattr(collector, "video_comments", _fake_comments(calls))

    out = CG.content_gaps("key", NICHE, top_videos=3, fetch=True)

    assert out["mode"] == "rules"
    assert out["quotaSpent"] == 3 and sorted(calls) == ["gv1", "gv2", "gv3"]
    topics = [g["topic"] for g in out["gaps"]]
    assert topics[0] == "How do you keep a sourdough starter alive in winter?"
    top = out["gaps"][0]
    assert sorted(top["sourceVideos"]) == ["gv1", "gv2"]
    assert top["status"] == "free" and top["askers"] == 2
    assert "Great video, thanks!" not in [e for g in out["gaps"] for e in g["examples"]]
    assert out["coverageBase"]["videos"] == 4
    assert out["skippedVideos"] == []


def test_repeat_call_spends_no_quota(monkeypatch):
    calls = []
    monkeypatch.setattr(collector, "video_comments", _fake_comments(calls))
    CG.content_gaps("key", NICHE, top_videos=3, fetch=True)
    calls.clear()

    again = CG.content_gaps("key", NICHE, top_videos=3, fetch=True)

    assert calls == [] and again["quotaSpent"] == 0
    assert len(again["gaps"]) == 3


def test_cache_keeps_question_text_and_likes_but_never_the_author(monkeypatch):
    monkeypatch.setattr(collector, "video_comments", _fake_comments([]))
    CG.content_gaps("key", NICHE, top_videos=3, fetch=True)

    conn = db.get_conn()
    rows = conn.execute("SELECT result FROM video_insights WHERE task = ?",
                        (CG.TASK,)).fetchall()
    conn.close()
    blob = repr([r["result"] for r in rows])
    assert "sourdough" in blob
    for name in ("Alice", "Bob", "Carol", "Dan", "Eve", "author"):
        assert name not in blob


def test_without_fetch_only_the_cache_is_read(monkeypatch):
    calls = []
    monkeypatch.setattr(collector, "video_comments", _fake_comments(calls))

    out = CG.content_gaps(None, NICHE, top_videos=3, fetch=False)

    assert calls == [] and out["quotaSpent"] == 0 and out["gaps"] == []
    assert [s["videoId"] for s in out["skippedVideos"]] == ["gv1", "gv2", "gv3"]
    assert all(s["reason"] == "not-fetched" for s in out["skippedVideos"])


def test_exhausted_quota_stops_fetching_and_lists_the_rest(monkeypatch):
    calls = []

    def _quota(api_key, video_id, **kw):
        calls.append(video_id)
        raise yt.QuotaExceeded("quotaExceeded")
    monkeypatch.setattr(collector, "video_comments", _quota)

    out = CG.content_gaps("key", NICHE, top_videos=3, fetch=True)

    assert calls == ["gv1"]
    assert [s["reason"] for s in out["skippedVideos"]] == ["quota-exhausted"] * 3
    assert out["quotaSpent"] == 0


def test_a_failing_video_is_skipped_and_the_rest_still_run(monkeypatch):
    fake = _fake_comments([])

    def _flaky(api_key, video_id, **kw):
        if video_id == "gv2":
            raise RuntimeError("boom")
        return fake(api_key, video_id, **kw)
    monkeypatch.setattr(collector, "video_comments", _flaky)

    out = CG.content_gaps("key", NICHE, top_videos=3, fetch=True)

    assert [(s["videoId"], s["reason"]) for s in out["skippedVideos"]] == [("gv2", "error")]
    assert out["quotaSpent"] == 2 and out["gaps"]


def test_llm_mode_reads_cached_requests_and_checks_coverage(monkeypatch):
    class _Live:
        def complete_json(self, *a, **kw):
            return None
    monkeypatch.setattr(factory, "get_provider", lambda: _Live())
    conn = db.get_conn()
    db.save_video_insights(conn, "gv1", "comment_insights", {
        "pains": [], "video_ideas": [],
        "requests": [{"topic": "Bread starter in winter", "evidence": "how do I keep it alive"},
                     {"topic": "Gluten free bread", "evidence": "please do gluten free"}],
    }, "m")
    conn.commit()
    conn.close()
    monkeypatch.setattr(collector, "video_comments",
                        lambda *a, **kw: pytest.fail("cache hit must not fetch"))

    out = CG.content_gaps("key", NICHE, top_videos=1, fetch=True)

    assert out["mode"] == "llm" and out["quotaSpent"] == 0
    assert [g["topic"] for g in out["gaps"]] == ["Gluten free bread"]
    assert out["gaps"][0]["examples"] == ["please do gluten free"]
    assert out["coveredCount"] == 1
    assert out["covered"][0]["nearestVideo"]["videoId"] == "gv4"


def test_use_llm_false_forces_rules_even_with_a_provider(monkeypatch):
    monkeypatch.setattr(factory, "get_provider", lambda: object())
    monkeypatch.setattr(collector, "video_comments", _fake_comments([]))

    out = CG.content_gaps("key", NICHE, top_videos=3, fetch=True, use_llm=False)

    assert out["mode"] == "rules" and out["gaps"]


def test_vectors_merge_paraphrases_and_a_transcript_counts_as_coverage(monkeypatch):
    np = pytest.importorskip("numpy")
    emb = pytest.importorskip("infrastructure.embeddings.fastembed_provider")

    def _vec(text):
        t = text.lower()
        if "starter" in t or "закваск" in t:
            return np.array([1.0, 0.0, 0.0], dtype=np.float32)
        if "gluten" in t:
            return np.array([0.0, 1.0, 0.0], dtype=np.float32)
        return np.array([0.0, 0.0, 1.0], dtype=np.float32)
    monkeypatch.setattr(CG, "_embed", lambda texts: [_vec(t) for t in texts])
    monkeypatch.setattr(emb, "embed", lambda texts: [_vec(t) for t in texts]
                        if not isinstance(texts, str) else _vec(texts))
    QUESTIONS["gv3"] = [{"text": "А как сохранить закваску живой зимой?", "likeCount": 2,
                         "author": "Fay"}]
    try:
        conn = db.get_conn()
        conn.execute("INSERT INTO transcript_chunks (video_id, idx, start_sec, text, embedding) "
                     "VALUES (?,?,?,?,?)",
                     ("gv2", 0, 42, "today we bake gluten free bread",
                      emb.to_blob(_vec("gluten"))))
        conn.commit()
        conn.close()
        monkeypatch.setattr(collector, "video_comments", _fake_comments([]))

        out = CG.content_gaps("key", NICHE, top_videos=3, fetch=True)
    finally:
        QUESTIONS["gv3"] = [{"text": "Can you do a tutorial on focaccia art", "likeCount": 1,
                             "author": "Eve"}]

    starter = next(g for g in out["gaps"] if "starter" in g["topic"])
    assert starter["askers"] == 3 and sorted(starter["sourceVideos"]) == ["gv1", "gv2", "gv3"]
    assert out["coveredCount"] == 1
    assert out["covered"][0]["nearestTranscript"]["startSec"] == 42
    assert out["coverageBase"]["transcripts"] == 1


def test_limit_caps_the_list():
    out = CG.content_gaps(None, NICHE, top_videos=3, limit=1)
    assert len(out["gaps"]) <= 1


def test_unknown_niche_says_so():
    out = CG.content_gaps(None, "no-such-niche")
    assert out["found"] is False and out["gaps"] == []


def test_fetch_without_api_key_is_refused():
    with pytest.raises(ValueError):
        CG.content_gaps(None, NICHE, fetch=True)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

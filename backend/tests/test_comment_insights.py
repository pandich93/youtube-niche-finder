"""Tests for application/enrichment.py:comment_insights/niche_comment_insights
(stage 04). Same schema_scope setup as test_enrichment.py; YouTube API is
monkeypatched (collector.video_comments), LLM provider is monkeypatched via
infrastructure.llm.factory.get_provider -- no network. Run with pytest, or
directly: python3 tests/test_comment_insights.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db               # noqa: E402
from application import enrichment as EN             # noqa: E402
from application import collecting as collector       # noqa: E402
from infrastructure.llm import factory                 # noqa: E402
from infrastructure.llm.base import LLMResult           # noqa: E402
from infrastructure.llm.null import NullProvider        # noqa: E402


def setup_module(_=None):
    db.init_db()


class _StubProvider:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def complete_json(self, system, user, schema, *, model=None, max_tokens=1024):
        self.calls += 1
        return self.results.pop(0) if self.results else None


_FAKE_INSIGHTS = {
    "pains": [{"text": "audio too quiet", "quotes": ["can barely hear you"], "count_estimate": 12}],
    "requests": [{"topic": "more tutorials", "evidence": "do a part 2 please"}],
    "video_ideas": [{"title": "Part 2: advanced setup", "why": "most requested follow-up"}],
    "sentiment": {"positive": 0.6, "neutral": 0.3, "negative": 0.1},
    "language": "en",
}


def _fake_comments(n=5, quota=1):
    def _video_comments(api_key, video_id, max_results=100, order="relevance", search_terms=None):
        return {
            "video_id": video_id, "count": n,
            "comments": [{"text": f"comment {i}", "author": "x", "likeCount": 0,
                         "publishedAt": "2026-01-01T00:00:00Z", "replyCount": 0}
                        for i in range(n)],
            "quota": {"units_from_shared_pool": quota, "search_calls": 0},
        }
    return _video_comments


def _clear_llm_tables():
    conn = db.get_conn()
    conn.execute("DELETE FROM llm_cache")
    conn.execute("DELETE FROM llm_usage")
    conn.execute("DELETE FROM video_insights")
    conn.execute("DELETE FROM meta WHERE key='llm_budget_blocked_until'")
    conn.commit()
    conn.close()


def test_comment_insights_with_null_provider_returns_a_clear_hint(monkeypatch):
    _clear_llm_tables()
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    monkeypatch.setattr(collector, "video_comments", _fake_comments())

    out = EN.comment_insights("fake-key", "vci-null")

    assert out["cached"] is False
    assert "LLM_PROVIDER" in out["hint"]
    assert out["pains"] == []
    assert out["quotaSpent"] == 1  # comments were still fetched before the LLM step


def test_comment_insights_with_no_comments_skips_the_llm_call_entirely(monkeypatch):
    _clear_llm_tables()
    stub = _StubProvider([LLMResult(data=_FAKE_INSIGHTS, model="m/x",
                                    prompt_tokens=1, completion_tokens=1, cost_usd=0.001)])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    monkeypatch.setattr(collector, "video_comments", _fake_comments(n=0))

    out = EN.comment_insights("fake-key", "vci-empty")

    assert out["hint"] == "no comments found (or comments disabled) for this video"
    assert stub.calls == 0


def test_comment_insights_writes_cache_and_a_repeat_call_skips_youtube_and_llm(monkeypatch):
    _clear_llm_tables()
    stub = _StubProvider([LLMResult(data=_FAKE_INSIGHTS, model="m/x",
                                    prompt_tokens=1, completion_tokens=1, cost_usd=0.001)])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    calls = {"n": 0}

    def counting_comments(*a, **kw):
        calls["n"] += 1
        return _fake_comments()(*a, **kw)
    monkeypatch.setattr(collector, "video_comments", counting_comments)

    first = EN.comment_insights("fake-key", "vci-cache")
    assert first["cached"] is False
    assert first["pains"][0]["text"] == "audio too quiet"
    assert calls["n"] == 1
    assert stub.calls == 1

    second = EN.comment_insights("fake-key", "vci-cache")
    assert second["cached"] is True
    assert second["quotaSpent"] == 0
    assert second["pains"][0]["text"] == "audio too quiet"
    assert calls["n"] == 1  # YouTube not called again
    assert stub.calls == 1  # LLM not called again


def test_comment_insights_force_refresh_still_refetches_comments(monkeypatch):
    # force_refresh bypasses the video_insights row cache -- it always
    # re-fetches YouTube comments (quota spent again). Whether the
    # underlying LLM call itself repeats is llm_gateway's own cache's call
    # (same task+model+input never costs twice, even across force_refresh),
    # which is correct: this test only asserts the guarantee comment_insights
    # actually owns.
    _clear_llm_tables()
    stub = _StubProvider([
        LLMResult(data=_FAKE_INSIGHTS, model="m/x", prompt_tokens=1, completion_tokens=1, cost_usd=0.001),
        LLMResult(data=_FAKE_INSIGHTS, model="m/x", prompt_tokens=1, completion_tokens=1, cost_usd=0.001),
    ])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    calls = {"n": 0}

    def counting_comments(*a, **kw):
        calls["n"] += 1
        return _fake_comments()(*a, **kw)
    monkeypatch.setattr(collector, "video_comments", counting_comments)

    EN.comment_insights("fake-key", "vci-force")
    out = EN.comment_insights("fake-key", "vci-force", force_refresh=True)

    assert out["cached"] is False
    assert out["quotaSpent"] == 1
    assert calls["n"] == 2


def test_comment_insights_max_comments_deduplicates_and_caps():
    def dup_comments(api_key, video_id, max_results=100, order="relevance", search_terms=None):
        return {"video_id": video_id, "count": 6,
               "comments": [{"text": "same text"}] * 3 + [{"text": f"unique {i}"} for i in range(3)],
               "quota": {"units_from_shared_pool": 1}}
    deduped = EN._dedupe_comments(dup_comments("k", "v")["comments"], max_comments=2)
    assert len(deduped) == 2
    assert deduped[0]["text"] == "same text"  # first occurrence kept
    assert deduped[1]["text"] == "unique 0"


def test_niche_comment_insights_with_no_cached_videos_gives_a_hint():
    _clear_llm_tables()
    out = EN.niche_comment_insights("no-such-niche-insights")
    assert out["found"] is False
    assert out["videosUsed"] == 0


def test_niche_comment_insights_merges_cached_per_video_results(monkeypatch):
    _clear_llm_tables()
    conn = db.get_conn()
    db.upsert_channel(conn, {
        "channel_id": "UCcinight0000000000000001", "title": "t", "custom_url": None,
        "country": None, "description": "", "default_language": None,
        "subscriber_count": 100, "video_count": 1, "view_count": 100, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    })
    db.upsert_video(conn, {
        "video_id": "vni1", "channel_id": "UCcinight0000000000000001", "title": "video 1",
        "description": "", "published_at": "2026-01-01T00:00:00Z", "duration_seconds": 300,
        "view_count": 1000, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    })
    db.upsert_niche(conn, "n-comment-insights", "n-comment-insights", "n-comment-insights")
    db.link_video_niche(conn, "vni1", "n-comment-insights")
    db.save_video_insights(conn, "vni1", _FAKE_INSIGHTS, "m/x")
    conn.commit()
    conn.close()

    stub = _StubProvider([LLMResult(data=_FAKE_INSIGHTS, model="m/niche",
                                    prompt_tokens=1, completion_tokens=1, cost_usd=0.001)])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)

    out = EN.niche_comment_insights("n-comment-insights")

    assert out["found"] is True
    assert out["videosUsed"] == 1
    assert out["videoIds"] == ["vni1"]
    assert out["pains"][0]["text"] == "audio too quiet"


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

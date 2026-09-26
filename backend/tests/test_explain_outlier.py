"""Tests for application/enrichment.py:explain_outlier (stage 05) and its
HTTP endpoint GET /api/video/{id}/why (204 when LLM is off). Same
schema_scope setup as test_comment_insights.py -- LLM provider monkeypatched,
no network. Run with pytest, or directly: python3 tests/test_explain_outlier.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import enrichment as EN  # noqa: E402
from infrastructure.llm import factory  # noqa: E402
from infrastructure.llm.base import LLMResult  # noqa: E402
from infrastructure.llm.null import NullProvider  # noqa: E402

client = TestClient(api.app)


def setup_module(_=None):
    db.init_db()


class _StubProvider:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def complete_json(self, system, user, schema, *, model=None, max_tokens=1024):
        self.calls += 1
        return self.results.pop(0) if self.results else None


_FAKE_EXPLAIN = {
    "hooks": ["curiosity-gap title", "cold open with the payoff"],
    "title_pattern": "question + number",
    "timing_factor": "",
    "replicable_formula": "Open on the result, ask the question in the title.",
    "confidence": 0.6,
}


def _channel(cid):
    return {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None,
        "description": "", "default_language": None, "subscriber_count": 1000,
        "video_count": 1, "view_count": 1000, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    }


def _video(vid, cid, title, views, published_at):
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "d" * 10,
        "published_at": published_at, "duration_seconds": 300,
        "view_count": views, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": published_at, "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def _seed_channel_with_history(prefix, n=6, spike_views=100000):
    """n prior videos with modest views, then one spike -- gives the spike a
    real rolling/period baseline to be an outlier against."""
    conn = db.get_conn()
    cid = f"UCexpl{prefix}"
    db.upsert_channel(conn, _channel(cid))
    for i in range(n):
        db.upsert_video(conn, _video(f"v{prefix}old{i}", cid, f"old video {i}",
                                     1000, f"2026-01-{i + 1:02d}T00:00:00Z"))
    db.upsert_video(conn, _video(f"v{prefix}spike", cid, "THIS ONE BLEW UP",
                                 spike_views, "2026-02-01T00:00:00Z"))
    conn.commit()
    conn.close()
    return cid, f"v{prefix}spike"


def _clear_llm_tables():
    conn = db.get_conn()
    conn.execute("DELETE FROM llm_cache")
    conn.execute("DELETE FROM llm_usage")
    conn.execute("DELETE FROM video_insights")
    conn.execute("DELETE FROM meta WHERE key='llm_budget_blocked_until'")
    conn.commit()
    conn.close()


def test_explain_outlier_for_an_uncollected_video_is_a_clear_not_found():
    out = EN.explain_outlier("v-does-not-exist")
    assert out["found"] is False


def test_explain_outlier_without_enough_history_gives_a_hint_not_a_crash(monkeypatch):
    _clear_llm_tables()
    stub = _StubProvider([LLMResult(data=_FAKE_EXPLAIN, model="m/x",
                                    prompt_tokens=1, completion_tokens=1, cost_usd=0.001)])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    conn = db.get_conn()
    cid = "UCexplnohist"
    db.upsert_channel(conn, _channel(cid))
    db.upsert_video(conn, _video("v-nohist", cid, "lonely video", 500, "2026-01-01T00:00:00Z"))
    conn.commit()
    conn.close()

    out = EN.explain_outlier("v-nohist")

    assert out["found"] is True
    assert "history" in out["hint"]
    assert stub.calls == 0  # no LLM call wasted on an ungrounded explanation


def test_explain_outlier_with_null_provider_returns_a_clear_hint(monkeypatch):
    _clear_llm_tables()
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    cid, video_id = _seed_channel_with_history("null")

    out = EN.explain_outlier(video_id)

    assert out["found"] is True
    assert "LLM_PROVIDER" in out["hint"]
    assert out["hooks"] == []


def test_explain_outlier_writes_cache_and_a_repeat_call_skips_the_llm(monkeypatch):
    _clear_llm_tables()
    stub = _StubProvider([LLMResult(data=_FAKE_EXPLAIN, model="m/x",
                                    prompt_tokens=1, completion_tokens=1, cost_usd=0.001)])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    cid, video_id = _seed_channel_with_history("cache")

    first = EN.explain_outlier(video_id)
    assert first["cached"] is False
    assert first["hooks"] == _FAKE_EXPLAIN["hooks"]
    assert stub.calls == 1

    second = EN.explain_outlier(video_id)
    assert second["cached"] is True
    assert second["hooks"] == _FAKE_EXPLAIN["hooks"]
    assert stub.calls == 1


def test_http_endpoint_returns_204_when_llm_is_off(monkeypatch):
    _clear_llm_tables()
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    cid, video_id = _seed_channel_with_history("http204")

    r = client.get(f"/api/video/{video_id}/why")

    assert r.status_code == 204
    assert r.content == b""


def test_http_endpoint_returns_200_with_body_on_a_real_explanation(monkeypatch):
    _clear_llm_tables()
    stub = _StubProvider([LLMResult(data=_FAKE_EXPLAIN, model="m/x",
                                    prompt_tokens=1, completion_tokens=1, cost_usd=0.001)])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    cid, video_id = _seed_channel_with_history("http200")

    r = client.get(f"/api/video/{video_id}/why")

    assert r.status_code == 200
    assert r.json()["hooks"] == _FAKE_EXPLAIN["hooks"]


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

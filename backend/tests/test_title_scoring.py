"""Integration tests for application/enrichment.py:score_titles/suggest_titles
(stage 09). Same schema_scope + LLM-provider-monkeypatch setup as
test_enrichment.py. Run with pytest, or directly:
python3 tests/test_title_scoring.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db  # noqa: E402
from application import enrichment as EN  # noqa: E402
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


def _channel(cid):
    return {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None,
        "description": "", "default_language": None, "subscriber_count": 1000,
        "video_count": 1, "view_count": 1000, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    }


def _video(vid, cid, title, views=1000, days_ago=10):
    from datetime import datetime, timedelta, timezone
    pub = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": pub, "duration_seconds": 300,
        "view_count": views, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": pub, "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def _seed_niche(slug, n=6):
    conn = db.get_conn()
    cid = f"UCtitle{slug}"
    db.upsert_channel(conn, _channel(cid))
    db.upsert_niche(conn, slug, slug, slug)
    for i in range(n):
        vid = f"vtitle{slug}{i}"
        db.upsert_video(conn, _video(vid, cid, f"I Tried Cold Showers Day {i}", views=1000 * (i + 1)))
        db.link_video_niche(conn, vid, slug)
    conn.commit()
    conn.close()


def test_score_titles_requires_candidates():
    try:
        EN.score_titles([], niche_slug="n-title-empty")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_score_titles_requires_niche_or_channel():
    try:
        EN.score_titles(["a title"])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_score_titles_without_llm_gives_deterministic_scores_only(monkeypatch):
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    _seed_niche("n-title-null")

    out = EN.score_titles(["A Reasonably Sized Title About Cold Showers And Discipline"],
                          niche_slug="n-title-null")

    assert out["llmUsed"] is False
    assert "hint" in out and out["hint"]
    t = out["titles"][0]
    assert t["score"] == t["deterministicScore"]
    assert t["strengths"] == []


def test_score_titles_detects_matched_patterns_in_signals(monkeypatch):
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    _seed_niche("n-title-pattern")

    out = EN.score_titles(["I Tried Something New For Thirty Days Straight"],
                          niche_slug="n-title-pattern")

    signals = out["titles"][0]["signals"]
    assert "length" in signals and "hasNumber" in signals


def test_score_titles_with_llm_uses_the_returned_score_and_fields(monkeypatch):
    stub = _StubProvider([LLMResult(
        data={"titles": [{"title": "My Candidate Title", "score": 77,
                          "strengths": ["matches pattern X"], "risks": ["a bit long"],
                          "improved": "My Better Candidate Title"}]},
        model="m/x", prompt_tokens=1, completion_tokens=1, cost_usd=0.001)])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    _seed_niche("n-title-llm")

    out = EN.score_titles(["My Candidate Title"], niche_slug="n-title-llm")

    assert out["llmUsed"] is True
    t = out["titles"][0]
    assert t["score"] == 77
    assert t["strengths"] == ["matches pattern X"]
    assert t["improved"] == "My Better Candidate Title"


def test_suggest_titles_without_llm_gives_a_hint_and_no_titles(monkeypatch):
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    _seed_niche("n-title-suggest-null")

    out = EN.suggest_titles("cold showers", niche_slug="n-title-suggest-null")

    assert out["titles"] == []
    assert "hint" in out


def test_suggest_titles_with_llm_generates_and_scores(monkeypatch):
    stub = _StubProvider([
        LLMResult(data={"titles": ["Generated Title One", "Generated Title Two"]},
                  model="m/x", prompt_tokens=1, completion_tokens=1, cost_usd=0.001),
        LLMResult(data={"titles": [
            {"title": "Generated Title One", "score": 60, "strengths": [], "risks": [],
             "improved": "x"},
            {"title": "Generated Title Two", "score": 70, "strengths": [], "risks": [],
             "improved": "y"},
        ]}, model="m/x", prompt_tokens=1, completion_tokens=1, cost_usd=0.001),
    ])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    _seed_niche("n-title-suggest-llm")

    out = EN.suggest_titles("cold showers", niche_slug="n-title-suggest-llm", n=5)

    assert len(out["titles"]) == 2
    assert {t["title"] for t in out["titles"]} == {"Generated Title One", "Generated Title Two"}


def test_suggest_titles_requires_a_topic():
    try:
        EN.suggest_titles("", niche_slug="n-title-notopic")
        assert False, "expected ValueError"
    except ValueError:
        pass


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

"""Tests for application/hook_score.py (plan 10) -- hook report of a saved
transcript, niche benchmark, scoring of a pasted draft -- plus their HTTP
routes and MCP tools. Same schema_scope setup as test_explain_outlier.py.
Transcripts are inserted with plain SQL (save_transcript would load the
embedding model); LLM provider is stubbed, no network. Run with pytest, or
directly: python3 tests/test_hook_score.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
import interfaces.mcp.server as srv  # noqa: E402
from application import hook_score as HK  # noqa: E402
from infrastructure.llm import factory  # noqa: E402
from infrastructure.llm.base import LLMResult  # noqa: E402
from infrastructure.llm.null import NullProvider  # noqa: E402

client = TestClient(api.app)

TIMED = "\n".join(["0:00", "I spent $5,000 on a car wash and nobody tells you this.",
                   "0:10", "Have you ever wondered how much owners earn?",
                   "0:20", "By the end of this video you'll know the three mistakes.",
                   "0:40", "Now let's look at the numbers."])
UNTIMED = " ".join(["word"] * 200)

_FAKE_HOOK = {
    "works": ["opens on a concrete sum"],
    "improve": ["state the payoff earlier"],
    "rewrite": "I lost $5,000 on a car wash -- here is why.",
    "confidence": 0.6,
}


def setup_module(_=None):
    db.init_db()


class _StubProvider:
    def __init__(self):
        self.calls = 0
        self.last_user = None

    def complete_json(self, system, user, schema, *, model=None, max_tokens=1024):
        self.calls += 1
        self.last_user = user
        return LLMResult(data=dict(_FAKE_HOOK), model="m/x", prompt_tokens=1,
                         completion_tokens=1, cost_usd=0.001)


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


def _put_transcript(video_id, text):
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO transcripts (video_id, language, text, has_timestamps, word_count, "
        "created_at) VALUES (?,?,?,?,?,?) ON CONFLICT (video_id) DO UPDATE SET text=excluded.text",
        (video_id, None, text, 1 if "0:00" in text else 0, len(text.split()), db.now_iso()))
    conn.commit()
    conn.close()


def _seed_video(vid, title="a video"):
    conn = db.get_conn()
    cid = f"UC{vid}"
    db.upsert_channel(conn, _channel(cid))
    db.upsert_video(conn, _video(vid, cid, title, 1000, "2026-01-01T00:00:00Z"))
    conn.commit()
    conn.close()


def _clear_llm_tables():
    conn = db.get_conn()
    for t in ("llm_cache", "llm_usage", "video_insights"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM meta WHERE key='llm_budget_blocked_until'")
    conn.commit()
    conn.close()


def _count(table):
    conn = db.get_conn()
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def _seed_niche_video(slug, vid, views, text):
    """One channel with 6 modest uploads and a target video with `views`, so the
    target has a real rolling baseline and a known outlierScore (views / 1000)."""
    conn = db.get_conn()
    cid = f"UC{vid}"
    db.upsert_channel(conn, _channel(cid))
    for i in range(6):
        db.upsert_video(conn, _video(f"{vid}old{i}", cid, f"old {i}", 1000,
                                     f"2026-01-{i + 1:02d}T00:00:00Z"))
    db.upsert_video(conn, _video(vid, cid, "target", views, "2026-02-01T00:00:00Z"))
    db.link_video_niche(conn, vid, slug)
    conn.commit()
    conn.close()
    _put_transcript(vid, text)


# ------------------------------------------------------------- hook_report

def test_hook_report_without_a_transcript_says_so():
    out = HK.hook_report("v-none")
    assert out["found"] is True and out["hasTranscript"] is False
    assert out["hint"]
    assert "score" not in out


def test_hook_report_scores_a_timed_transcript():
    _seed_video("vhk1", "My car wash")
    _put_transcript("vhk1", TIMED)
    out = HK.hook_report("vhk1")
    assert out["hasTranscript"] is True and out["title"] == "My car wash"
    assert out["hook"]["mode"] == "timed"
    assert out["hook"]["words"] > 20 and "Now let's look" not in out["hook"]["text"]
    assert out["score"] > 50 and out["level"] in ("ok", "strong")
    assert out["features"]["number"]["hit"] is True
    assert out["quotaUsed"] == 0
    assert "llm" not in out and "nicheComparison" not in out
    assert out["hook"]["startsAtSec"] == 0


def test_hook_report_works_without_a_videos_row():
    _put_transcript("vhk-orphan", TIMED)
    out = HK.hook_report("vhk-orphan")
    assert out["hasTranscript"] is True and out["title"] is None
    assert out["score"] is not None


def test_hook_report_untimed_takes_75_words():
    _put_transcript("vhk-untimed", UNTIMED)
    out = HK.hook_report("vhk-untimed")
    assert out["hook"]["mode"] == "untimed"
    assert out["hook"]["words"] == 75 and out["words"] == 75


# ------------------------------------------------------ niche_hook_benchmark

def test_benchmark_without_transcripts_is_not_found():
    out = HK.niche_hook_benchmark("no-such-niche")
    assert out["found"] is False and out["hint"]
    assert out["quotaUsed"] == 0


def test_benchmark_with_a_few_transcripts_is_insufficient_data():
    for i in range(3):
        _seed_niche_video("hk-few", f"vhkfew{i}", 100000, "I spent $5,000 on a car wash.")
    out = HK.niche_hook_benchmark("hk-few")
    assert out["found"] is True
    assert out["level"] == "insufficient-data" and out["reliable"] is False
    assert out["outliers"]["n"] == 3 and out["regular"]["n"] == 0
    assert out["need"] == 10
    assert out["moreCommonInOutliers"] == []


_FULL_NICHE = []


def _ensure_full_niche():
    """Niche "hk-full": 10 outlier and 10 regular hooks (number in 9/10 vs 3/10),
    plus one grey-zone video. Seeded once per test module."""
    if _FULL_NICHE:
        return
    for i in range(10):
        text = "I spent $5,000 on a car wash." if i < 9 else "I spent money on a car wash."
        _seed_niche_video("hk-full", f"vhkfullo{i}", 100000, text)
    for i in range(10):
        text = "I spent $5,000 on a car wash." if i < 3 else "I spent money on a car wash."
        _seed_niche_video("hk-full", f"vhkfullr{i}", 1000, text)
    # 2x its baseline: neither an outlier nor an ordinary video, left out of both groups
    _seed_niche_video("hk-full", "vhkfullgrey", 2000, "I spent $5,000 on a car wash.")
    _FULL_NICHE.append(True)


def test_benchmark_with_enough_hooks_finds_the_feature_outliers_share():
    _ensure_full_niche()
    out = HK.niche_hook_benchmark("hk-full")
    assert out["level"] == "ok" and out["reliable"] is True
    assert out["outliers"]["n"] == 10 and out["regular"]["n"] == 10
    assert out["features"]["number"]["diffPp"] == 60
    assert out["moreCommonInOutliers"] == ["number"]
    assert out["greyZone"] == 1 and out["quotaUsed"] == 0


# ----------------------------------------------------------- score_hook_text

def test_score_hook_text_requires_text():
    for empty in ("", "   ", None):
        try:
            HK.score_hook_text(empty)
        except ValueError as e:
            assert str(e) == "text is required"
        else:
            raise AssertionError("expected ValueError")


def test_score_hook_text_without_niche_has_no_comparison():
    out = HK.score_hook_text("Have you ever wondered why car washes fail? I lost $40,000.")
    assert out["score"] > 0 and out["hook"]["mode"] == "untimed"
    assert "comparison" not in out and out["quotaUsed"] == 0


def test_score_hook_text_with_a_thin_niche_does_not_invent_numbers():
    out = HK.score_hook_text("I spent $5,000 on a car wash.", niche="niche-without-data")
    assert out["comparison"]["benchmarkLevel"] == "insufficient-data"
    assert out["comparison"]["outlierMean"] is None and out["comparison"]["regularMean"] is None
    assert out["comparison"]["featureGaps"] == []
    assert out["benchmark"]["level"] == "insufficient-data" and out["benchmark"]["need"] == 10


def test_score_hook_text_compares_with_a_reliable_niche():
    _ensure_full_niche()
    out = HK.score_hook_text("I spent money on a car wash.", niche="hk-full")
    cmp = out["comparison"]
    assert cmp["benchmarkLevel"] == "ok"
    assert cmp["outlierMean"] is not None and cmp["yourScore"] == out["score"]
    assert cmp["featureGaps"] == ["number"]      # outliers use numbers, this draft has none


def test_score_hook_text_stores_and_sends_nothing(monkeypatch):
    _ensure_full_niche()
    stub = _StubProvider()
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    before = {t: _count(t) for t in ("video_insights", "llm_cache", "drafts", "llm_usage")}
    HK.score_hook_text("A private draft intro that must not be saved anywhere.", niche="hk-full")
    HK.score_hook_text("Another private draft.")
    after = {t: _count(t) for t in before}
    assert before == after
    assert stub.calls == 0


# ----------------------------------------------------------------------- LLM

def test_llm_is_off_by_default_and_never_touches_the_provider(monkeypatch):
    _clear_llm_tables()
    stub = _StubProvider()
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    _put_transcript("vhk-nollm", TIMED)
    HK.hook_report("vhk-nollm")
    assert stub.calls == 0 and _count("video_insights") == 0


def test_llm_result_is_cached_and_the_text_hash_invalidates_it(monkeypatch):
    _clear_llm_tables()
    stub = _StubProvider()
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    _put_transcript("vhk-llm", TIMED)

    first = HK.hook_report("vhk-llm", llm=True)
    assert first["llm"]["works"] == _FAKE_HOOK["works"] and first["llm"]["cached"] is False
    assert stub.calls == 1
    assert "Have you ever wondered" in stub.last_user
    conn = db.get_conn()
    row = conn.execute("SELECT result FROM video_insights WHERE video_id=? AND task='hook'",
                       ("vhk-llm",)).fetchone()
    conn.close()
    assert row["result"]["textHash"]
    assert "Have you ever" not in str(row["result"])     # the intro text itself is not cached

    second = HK.hook_report("vhk-llm", llm=True)
    assert second["llm"]["cached"] is True and second["llm"]["rewrite"] == _FAKE_HOOK["rewrite"]
    assert stub.calls == 1

    HK.hook_report("vhk-llm", llm=True, force_refresh=True)
    assert stub.calls == 2

    _put_transcript("vhk-llm", TIMED.replace("car wash", "laundromat"))
    changed = HK.hook_report("vhk-llm", llm=True)
    assert changed["llm"]["cached"] is False and stub.calls == 3


def test_llm_with_null_provider_keeps_the_deterministic_part(monkeypatch):
    _clear_llm_tables()
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    _put_transcript("vhk-null", TIMED)
    out = HK.hook_report("vhk-null", llm=True)
    assert out["llm"] is None and "LLM_PROVIDER" in out["llmHint"]
    assert out["score"] is not None
    assert _count("video_insights") == 0


# ------------------------------------------------------------------ HTTP, MCP

def test_http_routes():
    _ensure_full_niche()
    _put_transcript("vhk-http", TIMED)
    r = client.get("/api/videos/vhk-http/hook")
    assert r.status_code == 200 and r.json()["hasTranscript"] is True
    assert "llm" not in r.json()

    r = client.post("/api/hooks/score", json={"text": "Have you ever wondered why? I lost $9."})
    assert r.status_code == 200 and r.json()["score"] > 0
    assert client.post("/api/hooks/score", json={"text": "  "}).status_code == 400
    assert client.post("/api/hooks/score", json={}).status_code == 400

    r = client.post("/api/hooks/score", json={"text": "I spent money.", "niche": "hk-full"})
    assert r.json()["comparison"]["benchmarkLevel"] == "ok"

    r = client.get("/api/niches/hk-full/hook-benchmark")
    assert r.status_code == 200 and r.json()["reliable"] is True
    assert client.get("/api/niches/none-here/hook-benchmark").json()["found"] is False


def test_mcp_tools_delegate():
    _ensure_full_niche()
    _put_transcript("vhk-mcp", TIMED)
    assert srv.hook_report("vhk-mcp")["hook"]["mode"] == "timed"
    assert srv.hook_report("vhk-mcp", niche="hk-full")["nicheComparison"]["benchmarkLevel"] == "ok"
    assert srv.niche_hook_benchmark("hk-full")["reliable"] is True
    assert srv.score_hook_text("I lost $9 on a car wash.")["score"] > 0
    assert "error" in srv.score_hook_text("")


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

    fns = [v for k, v in globals().items() if k.startswith("test_")]
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

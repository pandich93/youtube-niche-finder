"""Tests for application/briefs.py (plan 02): turning one outlier video into a
draft brief for the creator's own video. Same throwaway-schema setup as
test_smoke.py (tests/schema_scope.py) seeded with the synthetic demo corpus;
the LLM functions and similarity search are monkeypatched -- no network, no
quota, no model.
Run with pytest, or directly: python3 tests/test_briefs.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import briefs as BR  # noqa: E402
from application import enrichment as EN  # noqa: E402
from application import metadata_review as MR  # noqa: E402
from application import search as Q  # noqa: E402
from infrastructure.categories import repository as C  # noqa: E402
from seed_demo import seed  # noqa: E402

SRC = "avid000"          # a video of "Tiny AI Lab" in the demo corpus
WORDS_100 = " ".join(f"w{i}" for i in range(100))


def setup_module(_=None):
    db.init_db()
    C.seed_fallback()
    seed()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    conn = db.get_conn()
    for t in ("drafts", "transcripts", "transcript_requests"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    # No embeddings in the demo corpus: keep similarity out unless a test asks.
    monkeypatch.setattr(Q, "similar_videos", lambda *a, **k: {"similar": []})


def _rows(sql, params=()):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _parts(brief):
    return {s["part"]: s["reason"] for s in brief["skipped"]}


# ------------------------------------------------------------ basics

def test_unknown_video_is_not_found():
    b = BR.build_brief("nosuchvideo", use_llm=False, save=False)
    assert b["found"] is False and "hint" in b


def test_brief_without_llm_has_the_free_parts_and_says_what_it_skipped():
    b = BR.build_brief(SRC, use_llm=False, save=False)
    assert b["found"] is True and b["videoId"] == SRC and b["quotaUsed"] == 0
    assert b["llmUsed"] is False
    assert b["source"]["title"] and b["source"]["views"] is not None
    assert b["source"]["isShort"] is False
    assert b["niche"] == "demo"                         # taken from video_niches
    assert b["overlap"]["verdict"] in ("free", "recent", "proven", "flopped")
    assert "patterns" in b["angle"]
    parts = _parts(b)
    assert "why_viral" in parts and "titles" in parts   # both need an LLM
    assert "LLM" in parts["titles"]
    assert b["titles"]["suggestions"] == []
    assert b["draftId"] is None


def test_overlap_never_counts_the_source_video_as_prior_coverage(monkeypatch):
    seen = {}

    def fake_check(ideas, niche=None, **kw):
        seen["niche"] = niche
        return {"ideas": [{"idea": ideas[0], "verdict": "recent", "matchCount": 2,
                           "daysSinceLastCoverage": 3, "bestOutlierScore": 4.0,
                           "matches": [
                               {"videoId": SRC, "title": "self", "ageDays": 60, "outlierScore": 22.0},
                               {"videoId": "other1", "title": "other", "ageDays": 5,
                                "outlierScore": 1.0}]}]}
    monkeypatch.setattr(Q, "check_ideas", fake_check)
    b = BR.build_brief(SRC, use_llm=False, save=False)
    assert [m["videoId"] for m in b["overlap"]["matches"]] == ["other1"]
    assert b["overlap"]["matchCount"] == 1
    # the verdict is recomputed without the source: only a 5-day-old match is left
    assert b["overlap"]["verdict"] == "recent" and b["overlap"]["daysSinceLastCoverage"] == 5
    assert seen["niche"] == "demo"


def test_a_video_without_an_outlier_score_says_so(monkeypatch):
    from application import discovery as trends
    monkeypatch.setattr(trends, "load_window", lambda **k: [])
    b = BR.build_brief(SRC, use_llm=False, save=False)
    assert b["source"]["outlierScore"] is None
    assert "source" in _parts(b) and "baseline" in _parts(b)["source"]


# ------------------------------------------------------------ hook

def _transcript(video_id, text):
    conn = db.get_conn()
    conn.execute("INSERT INTO transcripts (video_id, language, text, has_timestamps, "
                 "word_count, created_at) VALUES (?,?,?,?,?,?)",
                 (video_id, "en", text, 0, len(text.split()), db.now_iso()))
    conn.commit()
    conn.close()


def test_hook_is_the_first_75_words_of_a_pasted_transcript():
    _transcript(SRC, WORDS_100)
    b = BR.build_brief(SRC, use_llm=False, save=False)
    assert b["hook"]["available"] is True
    assert b["hook"]["text"].split() == [f"w{i}" for i in range(75)]
    assert "hook" not in _parts(b)
    assert _rows("SELECT * FROM transcript_requests") == []


def test_missing_transcript_tells_you_what_to_paste_and_queues_it_only_on_save():
    b = BR.build_brief(SRC, use_llm=False, save=False)
    assert b["hook"]["available"] is False and "Транскрипты" in b["hook"]["action"]
    assert "hook" in _parts(b)
    assert _rows("SELECT * FROM transcript_requests") == []      # a preview writes nothing
    BR.build_brief(SRC, use_llm=False, save=True)
    q = _rows("SELECT video_id, status, reason FROM transcript_requests")
    assert q == [{"video_id": SRC, "status": "pending", "reason": "brief"}]


def test_short_transcript_is_used_whole():
    _transcript(SRC, "just five little words here")
    assert BR.build_brief(SRC, use_llm=False, save=False)["hook"]["text"] == \
        "just five little words here"


# ------------------------------------------------------------ references

def test_thumbnail_references_come_from_similar_videos_with_their_thumbnails(monkeypatch):
    monkeypatch.setattr(Q, "similar_videos", lambda vid, **k: {"similar": [
        {"videoId": "avid001", "title": "t1", "views": 10, "similarity": 0.8,
         "channelTitle": "c"}]})
    b = BR.build_brief(SRC, use_llm=False, save=False)
    assert [r["videoId"] for r in b["thumbnailReferences"]] == ["avid001"]
    assert "thumbnail" in b["thumbnailReferences"][0]
    assert "thumbnailReferences" not in _parts(b)


def test_no_similar_videos_is_reported_not_hidden():
    b = BR.build_brief(SRC, use_llm=False, save=False)
    assert b["thumbnailReferences"] == []
    assert "thumbnailReferences" in _parts(b)


# ------------------------------------------------------------ LLM path

def test_with_an_llm_the_brief_gets_an_angle_titles_and_a_working_title(monkeypatch):
    monkeypatch.setattr(EN, "explain_outlier", lambda vid, **k: {
        "videoId": vid, "found": True, "hooks": ["question in the first line"],
        "title_pattern": "how I ...", "timing_factor": "weekday morning",
        "replicable_formula": "promise + proof", "confidence": "medium"})
    monkeypatch.setattr(EN, "suggest_titles", lambda topic, **k: {
        "llmUsed": True, "titles": [
            {"title": "My own take", "score": 82, "deterministicScore": 70},
            {"title": "Second option", "score": 60, "deterministicScore": 55}]})
    b = BR.build_brief(SRC, use_llm=True, save=True)
    assert b["llmUsed"] is True
    assert b["why"]["hooks"] == ["question in the first line"]
    assert [t["title"] for t in b["titles"]["suggestions"]] == ["My own take", "Second option"]
    assert "why_viral" not in _parts(b) and "titles" not in _parts(b)
    d = _rows("SELECT title FROM drafts WHERE id=?", (b["draftId"],))[0]
    assert d["title"] == "My own take"                  # best-scored suggestion


def test_llm_that_returns_nothing_is_treated_as_skipped(monkeypatch):
    monkeypatch.setattr(EN, "explain_outlier",
                        lambda vid, **k: {"videoId": vid, "found": True, "hooks": [],
                                          "hint": "LLM_PROVIDER is none"})
    monkeypatch.setattr(EN, "suggest_titles",
                        lambda topic, **k: {"titles": [], "hint": "LLM_PROVIDER is none"})
    b = BR.build_brief(SRC, use_llm=True, save=False)
    assert b["llmUsed"] is False and b["why"] is None
    assert {"why_viral", "titles"} <= set(_parts(b))


# ------------------------------------------------------------ gap topic (plan 03)

def test_gap_topic_is_what_the_overlap_check_and_titles_are_about(monkeypatch):
    seen = {}

    def fake_check(ideas, niche=None, **kw):
        seen["ideas"] = ideas
        return {"ideas": [{"idea": ideas[0], "matches": []}]}
    monkeypatch.setattr(Q, "check_ideas", fake_check)
    monkeypatch.setattr(EN, "explain_outlier", lambda vid, **k: {"hooks": []})
    monkeypatch.setattr(EN, "suggest_titles", lambda topic, **k: seen.setdefault(
        "topic", topic) and {"titles": []})
    b = BR.build_brief(SRC, use_llm=True, save=False, gap_topic="  how to fine-tune on a laptop? ")
    assert seen["ideas"] == ["how to fine-tune on a laptop?"]
    assert seen["topic"] == "how to fine-tune on a laptop?"
    assert b["gapTopic"] == "how to fine-tune on a laptop?"


def test_saved_gap_brief_uses_the_gap_as_working_title_without_an_llm():
    b = BR.build_brief(SRC, use_llm=False, save=True, gap_topic="how to fine-tune on a laptop?")
    d = MR.list_drafts()[0]
    assert d["id"] == b["draftId"] and d["title"] == "how to fine-tune on a laptop?"
    assert d["sourceVideoId"] == SRC and d["review"]["gapTopic"] == "how to fine-tune on a laptop?"


def test_without_gap_topic_the_brief_is_unchanged():
    b = BR.build_brief(SRC, use_llm=False, save=False)
    assert b["gapTopic"] is None


# ------------------------------------------------------------ saving

def test_save_creates_a_draft_linked_to_the_source_video():
    b = BR.build_brief(SRC, use_llm=False, save=True)
    assert isinstance(b["draftId"], int)
    d = MR.list_drafts()[0]
    assert d["id"] == b["draftId"] and d["sourceVideoId"] == SRC
    assert d["niche"] == "demo"
    assert d["review"]["kind"] == "brief"
    assert d["title"] == b["source"]["title"]           # no LLM: source title as working title
    assert d["publishedAt"] is None and d["videoId"] is None


def test_preview_without_save_creates_no_draft():
    BR.build_brief(SRC, use_llm=False, save=False)
    assert _rows("SELECT COUNT(*) AS n FROM drafts")[0]["n"] == 0


def test_saved_brief_review_is_valid_json_with_the_parts():
    b = BR.build_brief(SRC, use_llm=False, save=True)
    raw = _rows("SELECT review FROM drafts WHERE id=?", (b["draftId"],))[0]["review"]
    review = raw if isinstance(raw, dict) else json.loads(raw)
    assert review["kind"] == "brief" and "overlap" in review and "source" in review


def test_drafts_table_has_the_source_video_column():
    cols = {r["column_name"] for r in _rows(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='drafts' AND table_schema=current_schema()")}
    assert "source_video_id" in cols


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

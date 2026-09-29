"""Hook score (plan 10): how strong the first ~30 seconds of a video read,
from a transcript the user pasted in (transcripts are never fetched
automatically -- see PRIVACY.md). The rules live in domain/hook_scoring.py;
this module loads the rows and shapes the answers.

Three entry points:
  hook_report          one saved transcript, optionally compared with its
                       niche and optionally reviewed by the LLM (opt-in, cached)
  niche_hook_benchmark hooks of a niche's outliers vs its ordinary videos --
                       only meaningful with 10+ transcripts in each group,
                       which a hand-pasted corpus rarely has
  score_hook_text      a draft intro pasted by the author. The draft is never
                       stored, logged or sent to an LLM.

Zero YouTube quota everywhere; the only paid path is hook_report(llm=True).
"""
import hashlib
import os

import infrastructure.postgres as db
from application import llm_gateway as gw
from domain import hook_scoring as HS
from domain import periods as P
from domain import transcripts as TRD
from infrastructure.llm import factory

LLM_HOOK_TTL_DAYS = int(os.environ.get("LLM_HOOK_TTL_DAYS", "30"))
OUTLIER_FROM = 3.0        # outlierScore at or above this: an outlier
REGULAR_BELOW = 1.5       # below this: an ordinary video; in between is skipped
MAX_BENCHMARK_CHANNELS = 500

HOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "works": {"type": "array", "items": {"type": "string"}},
        "improve": {"type": "array", "items": {"type": "string"}},
        "rewrite": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["works", "improve", "rewrite", "confidence"],
}

_HOOK_SYSTEM = (
    "You review the first ~30 seconds of a YouTube video, given only as transcript "
    "text, for a creator's content-strategy dashboard. Rely only on the given text: "
    "do not invent facts, numbers or visuals that are not in it, and do not claim "
    "that a change will raise views. Give at most 3 items under 'works' (what already "
    "hooks a viewer) and at most 3 under 'improve' (concrete, cautious suggestions), "
    "and one 'rewrite' of the opening in the same style. Answer in the language of "
    "the text. 'confidence' is 0..1."
)


def _hook_of(raw_text: str) -> dict:
    parsed = TRD.parse_transcript(raw_text)
    return HS.hook_text(parsed["segments"])


def _hook_summary(hook: dict) -> dict:
    return {k: hook[k] for k in ("text", "mode", "words", "startsAtSec", "spanSec", "warning")}


def _score_of(hook: dict) -> dict:
    return HS.score_hook(hook["text"], span_sec=hook["spanSec"])


def _comparison(result: dict, bench: dict) -> dict:
    """Where one hook stands against a niche benchmark; without a reliable
    benchmark the level says so and no niche number is offered."""
    reliable = bench.get("found") and bench.get("reliable")
    out = {"yourScore": result["score"],
           "benchmarkLevel": bench.get("level") if bench.get("found") else "insufficient-data",
           "outlierMean": bench["outliers"]["meanScore"] if reliable else None,
           "regularMean": bench["regular"]["meanScore"] if reliable else None,
           "featureGaps": []}
    if reliable:
        out["featureGaps"] = [
            n for n in bench["moreCommonInOutliers"]
            if result["features"].get(n) is not None and not result["features"][n]["hit"]]
    return out


def _insufficient(bench: dict) -> dict:
    return {"level": "insufficient-data",
            "have": bench.get("have") or {"outliers": 0, "regular": 0},
            "need": HS.MIN_OUTLIERS}


# ---------------------------------------------------------------- hook_report

def _llm_input(hook: dict, language: str) -> str:
    return f"Language of the text: {language}\nIntro text (first ~30 seconds):\n{hook['text']}"


def _llm_review(video_id: str, hook: dict, language: str, force_refresh: bool) -> tuple:
    """(llm dict | None, hint | None). Cached in video_insights (task 'hook') for
    LLM_HOOK_TTL_DAYS, keyed by a hash of the intro text so an edited transcript
    is reviewed afresh. The cache stores the model's answer only, not the text."""
    text_hash = hashlib.sha256(hook["text"].encode("utf-8")).hexdigest()
    conn = db.get_conn()
    try:
        cached = conn.execute(
            "SELECT result, model, created_at FROM video_insights WHERE video_id=? AND task=?",
            (video_id, "hook")).fetchone()
    finally:
        conn.close()
    if (not force_refresh and cached and cached["created_at"]
            and (cached["result"] or {}).get("textHash") == text_hash
            and P.days_since(cached["created_at"]) <= LLM_HOOK_TTL_DAYS):
        data = {k: v for k, v in cached["result"].items() if k != "textHash"}
        return {**data, "cached": True, "model": cached["model"],
                "createdAt": cached["created_at"]}, None

    model = factory.default_model()
    user = _llm_input(hook, language)
    if force_refresh:
        # the gateway's own cache would otherwise hand back the identical answer
        conn = db.get_conn()
        conn.execute("DELETE FROM llm_cache WHERE key=?",
                     (gw._cache_key("hook_review", model, _HOOK_SYSTEM, user, HOOK_SCHEMA),))
        conn.commit()
        conn.close()
    data = gw.run("hook_review", _HOOK_SYSTEM, user, HOOK_SCHEMA, model=model)
    if data is None:
        return None, ("LLM_PROVIDER is none, or the daily LLM budget is exhausted -- "
                      "set LLM_PROVIDER=openrouter + OPENROUTER_API_KEY to enable this")
    conn = db.get_conn()
    db.save_video_insights(conn, video_id, "hook", {**data, "textHash": text_hash},
                           model or "auto")
    conn.commit()
    conn.close()
    return {**data, "cached": False, "model": model or "auto"}, None


def hook_report(video_id: str, niche: str = None, llm: bool = False,
                force_refresh: bool = False) -> dict:
    """Score of the first ~30 seconds of one saved transcript: 0-100, level,
    which features hit, penalties for filler, up to 3 tips. A score of the
    intro's TEXT, not of its visuals. With `niche`, adds how it compares with
    the niche benchmark (or says the benchmark has too little data). With
    llm=True, adds an LLM review of that text (opt-in; sends the intro text to
    the provider and can cost money; cached in video_insights). Zero YouTube
    quota."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT t.text, v.title FROM transcripts t "
            "LEFT JOIN videos v ON v.video_id = t.video_id WHERE t.video_id = ?",
            (video_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"videoId": video_id, "found": True, "hasTranscript": False,
                "hint": "No transcript saved for this video -- paste one on the "
                        "Транскрипты screen (request_transcript / save_transcript) first."}

    hook = _hook_of(row["text"])
    result = _score_of(hook)
    out = {"videoId": video_id, "found": True, "hasTranscript": True, "title": row["title"],
           "hook": _hook_summary(hook), **result, "quotaUsed": 0}
    if niche:
        out["nicheComparison"] = _comparison(result, niche_hook_benchmark(niche))
    if llm and hook["text"]:
        out["llm"], hint = _llm_review(video_id, hook, result["language"], force_refresh)
        if hint:
            out["llmHint"] = hint
    return out


# ----------------------------------------------------------------- benchmark

def niche_hook_benchmark(niche: str, outlier_threshold: float = OUTLIER_FROM,
                         regular_below: float = REGULAR_BELOW) -> dict:
    """Hooks of a niche's outliers (outlierScore >= 3) against its ordinary
    videos (< 1.5); videos in between, or without a baseline, are skipped.
    Reliable only with 10+ hooks in each group; below that the numbers come
    back with reliable=False and no "more common in outliers" claim. Reads
    only videos that have a saved transcript -- zero quota."""
    conn = db.get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT t.video_id, t.text, v.channel_id FROM transcripts t "
            "JOIN videos v ON v.video_id = t.video_id "
            "JOIN video_niches vn ON vn.video_id = t.video_id WHERE vn.niche_slug = ?",
            (niche,)).fetchall()]
    finally:
        conn.close()
    if not rows:
        return {"niche": niche, "found": False, "quotaUsed": 0,
                "hint": "No saved transcripts for videos in this niche -- paste some on the "
                        "Транскрипты screen first."}

    from application import discovery as trends
    channel_ids = sorted({r["channel_id"] for r in rows})[:MAX_BENCHMARK_CHANNELS]
    score_by_video = {w["video_id"]: w.get("outlierScore")
                      for w in trends.load_window(period="all", channel_ids=channel_ids)}

    outliers, regular, grey, without = [], [], 0, 0
    for r in rows:
        outlier_score = score_by_video.get(r["video_id"])
        if outlier_score is None:
            without += 1
            continue
        if outlier_score >= outlier_threshold:
            group = outliers
        elif outlier_score < regular_below:
            group = regular
        else:
            grey += 1
            continue
        group.append(_score_of(_hook_of(r["text"])))

    agg = HS.aggregate_hooks(outliers, regular)
    have = {"outliers": agg["outliers"]["n"], "regular": agg["regular"]["n"]}
    reliable = agg["reliable"]
    return {"niche": niche, "found": True,
            "level": "ok" if reliable else "insufficient-data", "reliable": reliable,
            "have": have, "need": HS.MIN_OUTLIERS,
            "outliers": agg["outliers"], "regular": agg["regular"],
            "features": agg["features"],
            "moreCommonInOutliers": agg["moreCommonInOutliers"] if reliable else [],
            "greyZone": grey, "withoutOutlierScore": without,
            "note": HS.DISCLAIMER, "quotaUsed": 0}


# ------------------------------------------------------------------- drafts

def score_hook_text(text: str, niche: str = None) -> dict:
    """Score an intro the author is drafting. The text is scored in memory and
    dropped: it is not saved (no video_insights, llm_cache or drafts row), not
    logged and not sent to an LLM. With `niche`, compares the score with that
    niche's benchmark, or reports that there is not enough data for one."""
    if not text or not text.strip():
        raise ValueError("text is required")
    hook = HS.hook_text([{"startSec": None, "text": text}])
    result = _score_of(hook)
    out = {"hook": _hook_summary(hook), **result, "quotaUsed": 0}
    if niche:
        bench = niche_hook_benchmark(niche)
        out["comparison"] = _comparison(result, bench)
        if not (bench.get("found") and bench.get("reliable")):
            out["benchmark"] = _insufficient(bench)
    return out

"""Background AI labeling for channels and videos (stage 03) -- the thing
that turns "faceless / on-topic" from something only Claude judges in chat
into a queryable field every dashboard/extension view can filter on.

classify_channels() writes to channels.llm_labels (always overwritten --
there's no human channel-level label to protect, unlike video tags).
tag_new_videos() writes into the video_tags table stage 16 built, with
source='llm'; the repository layer's PROTECTED_TAG_SOURCES already stops an
'llm' write from clobbering a 'manual'/'claude-mcp' one, so this module
doesn't duplicate that check -- it just never targets an already-tagged
video in the first place, to avoid burning budget on calls the protection
layer would discard anyway.

Both functions are no-ops when LLM_PROVIDER=none: llm_gateway.run() returns
None immediately and nothing here writes anything.

Known gap: llm_gateway.run() returns only the parsed data, not which model
in OPENROUTER's free-fallback chain actually answered (see
infrastructure/llm/openrouter.py), so channels.llm_model records
factory.default_model() (the pinned model, or "auto" when the caller lets
the gateway pick from the free chain) rather than the exact model that
answered a given call.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from application import llm_gateway as gw
from domain import periods as P
from domain import title_scoring as TSC
from infrastructure.llm import factory

LLM_RELABEL_DAYS = int(os.environ.get("LLM_RELABEL_DAYS", "30"))
MIN_MANUAL_TAGS_FOR_NICHE = int(os.environ.get("LLM_MIN_MANUAL_TAGS", "20"))

CHANNEL_SCHEMA = {
    "type": "object",
    "properties": {
        "is_faceless": {"type": "boolean"},
        "faceless_confidence": {"type": "number"},
        "content_format": {"type": "string", "enum": [
            "talking_head", "voiceover_stock", "animation", "screen_recording",
            "compilation", "ai_generated", "podcast", "other"]},
        "ai_voice_likely": {"type": "boolean"},
        "topic": {"type": "string"},
        "language": {"type": "string"},
        "made_for_kids_likely": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_faceless", "faceless_confidence", "content_format", "ai_voice_likely",
                "topic", "language", "made_for_kids_likely", "reason"],
}

_CHANNEL_SYSTEM = (
    "You classify YouTube channels for a content-strategy dashboard. Given a "
    "channel's name, description and recent video titles, decide whether it "
    "is faceless (no creator ever on camera), its dominant content format, "
    "likely language and a short topic. Be decisive: low faceless_confidence "
    "is fine, but always pick exactly one content_format. reason must be "
    "<=200 characters and in English."
)

VIDEO_TAG_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "proposed": {"type": "boolean"},
                },
                "required": ["tag", "proposed"],
            },
        },
    },
    "required": ["tags"],
}

_VIDEO_TAG_SYSTEM = (
    "You tag a YouTube video for a niche's curated taxonomy. Pick one or more "
    "tags from the given taxonomy that describe this video's theme, hook or "
    "format, and set proposed=false for each. Only when truly nothing in the "
    "taxonomy fits, invent one short new tag and set proposed=true for it. "
    "Always prefer an existing taxonomy tag over inventing one."
)


# ------------------------------------------------------------ channels

def _channel_candidates(conn, limit: int):
    """Channels never labeled, or labeled more than LLM_RELABEL_DAYS ago.
    Tracked channels first (someone is actively watching them), then most
    recently discovered. A true anomaly-score priority would need
    domain/metrics.py's full outlier computation per channel, too expensive
    to run on every worker tick -- recency is the cheap proxy for "more
    likely to include a breakout we haven't looked at yet"."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LLM_RELABEL_DAYS)).isoformat()
    return conn.execute(
        "SELECT c.channel_id, c.title, c.description, "
        "(tc.channel_id IS NOT NULL AND tc.active = 1) AS is_tracked "
        "FROM channels c LEFT JOIN tracked_channels tc ON tc.channel_id = c.channel_id "
        "WHERE c.llm_labeled_at IS NULL OR c.llm_labeled_at < ? "
        "ORDER BY is_tracked DESC, c.first_seen_at DESC NULLS LAST "
        "LIMIT ?", (cutoff, limit)).fetchall()


def _channel_video_sample(conn, channel_id: str, n: int = 10):
    return conn.execute(
        "SELECT title, tags, duration_seconds, is_short FROM videos "
        "WHERE channel_id = ? ORDER BY published_at DESC LIMIT ?",
        (channel_id, n)).fetchall()


def _build_channel_input(row, videos) -> str:
    titles = [v["title"] for v in videos if v["title"]]
    shorts = sum(1 for v in videos if v["is_short"])
    shorts_share = round(shorts / len(videos), 2) if videos else 0.0
    durations = [v["duration_seconds"] for v in videos if v["duration_seconds"] is not None]
    tag_counts = {}
    for v in videos:
        raw = v["tags"]
        for t in (raw.split(",") if isinstance(raw, str) else []):
            t = t.strip()
            if t:
                tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tags = sorted(tag_counts, key=tag_counts.get, reverse=True)[:10]
    description = (row["description"] or "")[:600]
    lines = [
        f"Channel: {row['title'] or row['channel_id']}",
        f"Description: {description}",
        "Recent titles:",
        *[f"- {t}" for t in titles],
        f"Top tags: {', '.join(top_tags) or 'none'}",
        f"Durations (s): {durations or 'unknown'}",
        f"Shorts share: {shorts_share}",
    ]
    return "\n".join(lines)


def classify_channels(limit: int = 50) -> dict:
    """Label up to `limit` channels with is_faceless/content_format/topic/...
    Stops as soon as the LLM gateway returns None (Null provider or daily
    budget exhausted) rather than burning the rest of the batch on calls
    that will also come back empty."""
    conn = db.get_conn()
    model = factory.default_model() or "auto"
    classified = 0
    budget_exhausted = False
    try:
        candidates = _channel_candidates(conn, limit)
        for row in candidates:
            videos = _channel_video_sample(conn, row["channel_id"])
            user = _build_channel_input(row, videos)
            data = gw.run("classify_channel", _CHANNEL_SYSTEM, user, CHANNEL_SCHEMA)
            if data is None:
                budget_exhausted = True
                break
            db.record_channel_llm_labels(conn, row["channel_id"], data, model)
            classified += 1
        conn.commit()
    finally:
        conn.close()
    return {"candidates": len(candidates), "classified": classified,
            "budgetExhausted": budget_exhausted}


# ------------------------------------------------------------ videos

def _niche_tag_group_counts(conn):
    """(niche, tag_group) -> count of human-sourced tags, across every niche
    that has at least one. Used to decide which taxonomies are trained
    enough to auto-tag from."""
    rows = conn.execute(
        "SELECT vn.niche_slug AS niche, vt.tag_group AS tag_group, COUNT(*) AS n "
        "FROM video_tags vt JOIN video_niches vn ON vn.video_id = vt.video_id "
        "WHERE vt.source IN ('manual', 'claude-mcp') "
        "GROUP BY vn.niche_slug, vt.tag_group").fetchall()
    return [{"niche": r["niche"], "tagGroup": r["tag_group"], "n": r["n"]} for r in rows]


def _taxonomy(conn, niche: str, tag_group: str, examples_limit: int = 5):
    tag_rows = conn.execute(
        "SELECT vt.tag, COUNT(*) AS n FROM video_tags vt "
        "JOIN video_niches vn ON vn.video_id = vt.video_id "
        "WHERE vn.niche_slug = ? AND vt.tag_group = ? "
        "GROUP BY vt.tag ORDER BY n DESC", (niche, tag_group)).fetchall()
    taxonomy = [r["tag"] for r in tag_rows]

    example_rows = conn.execute(
        "SELECT v.video_id, v.title, vt.tag FROM video_tags vt "
        "JOIN video_niches vn ON vn.video_id = vt.video_id "
        "JOIN videos v ON v.video_id = vt.video_id "
        "WHERE vn.niche_slug = ? AND vt.tag_group = ? AND vt.source IN ('manual', 'claude-mcp') "
        "ORDER BY vt.created_at DESC LIMIT ?",
        (niche, tag_group, examples_limit * 3)).fetchall()
    examples = {}
    for r in example_rows:
        ex = examples.setdefault(r["video_id"], {"title": r["title"], "tags": []})
        ex["tags"].append(r["tag"])
    examples = list(examples.values())[:examples_limit]
    return taxonomy, examples


def _video_candidates(conn, niche: str, tag_group: str, limit: int):
    return conn.execute(
        "SELECT v.video_id, v.title FROM videos v "
        "JOIN video_niches vn ON vn.video_id = v.video_id "
        "WHERE vn.niche_slug = ? "
        "AND v.video_id NOT IN (SELECT video_id FROM video_tags WHERE tag_group = ?) "
        "ORDER BY v.published_at DESC LIMIT ?", (niche, tag_group, limit)).fetchall()


def _build_video_input(tag_group: str, taxonomy: list, examples: list, title: str) -> str:
    lines = [f"Tag group: {tag_group}", f"Taxonomy: {', '.join(taxonomy)}"]
    if examples:
        lines.append("Examples:")
        for ex in examples:
            lines.append(f"- \"{ex['title']}\" -> {', '.join(ex['tags'])}")
    lines.append(f"Video title: {title}")
    return "\n".join(lines)


def tag_new_videos(limit: int = 100) -> dict:
    """Auto-tag videos in niches whose taxonomy is trained enough
    (>= MIN_MANUAL_TAGS_FOR_NICHE human-sourced tags in that tag_group).
    Only ever targets a video that has no tag at all yet in that group --
    an already (even partially) tagged video is left to the human/Claude who
    started it. `limit` is a total across every ready (niche, tag_group)
    pair, spent in that order until exhausted or the LLM gateway stops
    answering."""
    conn = db.get_conn()
    written = proposed = videos_seen = 0
    budget_exhausted = False
    skipped_niches = []
    try:
        counts = _niche_tag_group_counts(conn)
        ready = [c for c in counts if c["n"] >= MIN_MANUAL_TAGS_FOR_NICHE]
        skipped_niches = [c for c in counts if c["n"] < MIN_MANUAL_TAGS_FOR_NICHE]

        remaining = limit
        for entry in ready:
            if remaining <= 0 or budget_exhausted:
                break
            niche, tag_group = entry["niche"], entry["tagGroup"]
            taxonomy, examples = _taxonomy(conn, niche, tag_group)
            taxonomy_set = set(taxonomy)
            candidates = _video_candidates(conn, niche, tag_group, remaining)
            for row in candidates:
                if remaining <= 0:
                    break
                user = _build_video_input(tag_group, taxonomy, examples, row["title"] or "")
                data = gw.run("tag_video", _VIDEO_TAG_SYSTEM, user, VIDEO_TAG_SCHEMA)
                if data is None:
                    budget_exhausted = True
                    break
                videos_seen += 1
                remaining -= 1
                for item in (data.get("tags") or []):
                    tag = (item.get("tag") or "").strip()
                    if not tag:
                        continue
                    is_proposed = bool(item.get("proposed")) or tag not in taxonomy_set
                    if db.upsert_video_tag(conn, row["video_id"], tag_group, tag, "llm",
                                           proposed=is_proposed):
                        proposed += 1 if is_proposed else 0
                        written += 1
        conn.commit()
    finally:
        conn.close()
    return {
        "videosTagged": videos_seen, "tagsWritten": written, "proposedTags": proposed,
        "budgetExhausted": budget_exhausted,
        "skippedNiches": [{"niche": s["niche"], "tagGroup": s["tagGroup"],
                          "manualTags": s["n"], "needed": MIN_MANUAL_TAGS_FOR_NICHE}
                         for s in skipped_niches],
    }


# ------------------------------------------------------------ comment insights (04)

LLM_INSIGHTS_TTL_DAYS = int(os.environ.get("LLM_INSIGHTS_TTL_DAYS", "7"))

_EMPTY_INSIGHTS = {"pains": [], "requests": [], "video_ideas": [], "sentiment": None,
                   "language": None}

COMMENT_INSIGHTS_SCHEMA = {
    "type": "object",
    "properties": {
        "pains": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "quotes": {"type": "array", "items": {"type": "string"}},
                    "count_estimate": {"type": "integer"},
                },
                "required": ["text", "quotes", "count_estimate"],
            },
        },
        "requests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"topic": {"type": "string"}, "evidence": {"type": "string"}},
                "required": ["topic", "evidence"],
            },
        },
        "video_ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "why": {"type": "string"}},
                "required": ["title", "why"],
            },
        },
        "sentiment": {
            "type": "object",
            "properties": {
                "positive": {"type": "number"}, "neutral": {"type": "number"},
                "negative": {"type": "number"},
            },
            "required": ["positive", "neutral", "negative"],
        },
        "language": {"type": "string"},
    },
    "required": ["pains", "requests", "video_ideas", "sentiment", "language"],
}

_COMMENT_INSIGHTS_SYSTEM = (
    "You analyze YouTube comments for a creator's content-strategy dashboard. "
    "Extract at most 7 pains (real frustrations/problems, each with up to 2 "
    "short verbatim quotes and a rough count_estimate of how many comments "
    "raise it), at most 7 requests (topics/features viewers ask for, with one "
    "evidence quote each), and at most 5 concrete video_ideas the pains/"
    "requests suggest. Estimate overall sentiment as positive/neutral/negative "
    "fractions summing to 1. Detect the dominant comment language (ISO-639-1). "
    "Output only what the comments actually support -- fewer items is fine, "
    "don't invent volume."
)

_NICHE_INSIGHTS_SYSTEM = (
    "You merge several per-video YouTube comment-insight reports for videos in "
    "the same niche into one niche-level summary, same JSON shape as a single "
    "report. Combine near-duplicate pains/requests across videos (their "
    "count_estimate should roughly add up across the merged sources), rank "
    "video_ideas by how many videos' pains/requests support them, weight "
    "sentiment by how many comments each source video's pains/requests imply "
    "it had. Detect the dominant language across sources."
)


def _dedupe_comments(comments: list, max_comments: int) -> list:
    seen = set()
    out = []
    for c in comments:
        text = (c.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(c)
        if len(out) >= max_comments:
            break
    return out


def comment_insights(api_key: str, video_id: str, max_comments: int = 200,
                     force_refresh: bool = False) -> dict:
    """Stage 04: pains/requests/video ideas mined from a video's top
    comments via LLMGateway (long-context model, comment threads can run to
    thousands of tokens). Cached in video_insights for LLM_INSIGHTS_TTL_DAYS
    -- a repeat call within that window costs neither YouTube quota nor an
    LLM call. Spends 1 YouTube quota unit (comment_threads.list) only on a
    cache miss; never called automatically by the worker (see worker_cycle.py
    -- no comment-fetching step exists there by design, this is opt-in and
    costs real money once an LLM provider is configured)."""
    conn = db.get_conn()
    if not force_refresh:
        row = conn.execute(
            "SELECT result, model, created_at FROM video_insights WHERE video_id=? AND task=?",
            (video_id, "comment_insights")).fetchone()
        if row and row["created_at"] and P.days_since(row["created_at"]) <= LLM_INSIGHTS_TTL_DAYS:
            conn.close()
            return {"videoId": video_id, "cached": True, "model": row["model"],
                   "createdAt": row["created_at"], "quotaSpent": 0, **row["result"]}
    conn.close()

    from application import collecting as collector
    raw = collector.video_comments(api_key, video_id, max_results=max_comments)
    quota_spent = (raw.get("quota") or {}).get("units_from_shared_pool", 0)
    comments = _dedupe_comments(raw.get("comments") or [], max_comments)
    if not comments:
        return {"videoId": video_id, "cached": False, "quotaSpent": quota_spent,
               "hint": "no comments found (or comments disabled) for this video",
               **_EMPTY_INSIGHTS}

    text_block = "\n".join(f"- {(c.get('text') or '').strip()[:300]}" for c in comments)
    model = factory.long_context_model()
    data = gw.run("comment_insights", _COMMENT_INSIGHTS_SYSTEM, text_block,
                  COMMENT_INSIGHTS_SCHEMA, model=model)
    if data is None:
        return {"videoId": video_id, "cached": False, "quotaSpent": quota_spent,
               "hint": "LLM_PROVIDER is none, or the daily LLM budget is exhausted -- "
                       "set LLM_PROVIDER=openrouter + OPENROUTER_API_KEY to enable this",
               **_EMPTY_INSIGHTS}

    conn = db.get_conn()
    db.save_video_insights(conn, video_id, "comment_insights", data, model or "auto")
    conn.commit()
    conn.close()
    return {"videoId": video_id, "cached": False, "model": model or "auto",
           "quotaSpent": quota_spent, **data}


def niche_comment_insights(niche_slug: str, top_n: int = 5) -> dict:
    """Stage 04: merges whichever of the niche's top-viewed videos already
    have a cached comment_insights() result into one niche-level summary --
    a second LLM pass over those cached JSON reports, never over raw
    comments, so this never spends YouTube quota and never triggers a new
    per-video LLM call on its own."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT vi.video_id, vi.result, v.title, v.view_count FROM video_insights vi "
        "JOIN videos v ON v.video_id = vi.video_id "
        "JOIN video_niches vn ON vn.video_id = vi.video_id "
        "WHERE vn.niche_slug = ? AND vi.task = 'comment_insights' "
        "ORDER BY v.view_count DESC NULLS LAST LIMIT ?",
        (niche_slug, top_n)).fetchall()
    conn.close()
    if not rows:
        return {"niche": niche_slug, "found": False, "videosUsed": 0,
               "hint": "no cached comment_insights for this niche's videos yet -- "
                       "call comment_insights(video_id) on some of them first",
               **_EMPTY_INSIGHTS}

    combined_input = "\n\n".join(
        f"Video: {r['title']}\n{json.dumps(r['result'], ensure_ascii=False)}" for r in rows)
    model = factory.long_context_model()
    data = gw.run("niche_comment_insights", _NICHE_INSIGHTS_SYSTEM, combined_input,
                  COMMENT_INSIGHTS_SCHEMA, model=model)
    video_ids = [r["video_id"] for r in rows]
    if data is None:
        return {"niche": niche_slug, "found": True, "videosUsed": len(rows),
               "videoIds": video_ids,
               "hint": "LLM_PROVIDER is none, or the daily LLM budget is exhausted",
               **_EMPTY_INSIGHTS}
    return {"niche": niche_slug, "found": True, "videosUsed": len(rows),
           "videoIds": video_ids, "model": model or "auto", **data}


# ------------------------------------------------------------ why viral (05)

LLM_WHY_VIRAL_TTL_DAYS = int(os.environ.get("LLM_WHY_VIRAL_TTL_DAYS", "14"))

_EMPTY_EXPLAIN = {"hooks": [], "title_pattern": None, "timing_factor": None,
                  "replicable_formula": None, "confidence": None}

EXPLAIN_OUTLIER_SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": {"type": "array", "items": {"type": "string"}},
        "title_pattern": {"type": "string"},
        "timing_factor": {"type": "string"},
        "replicable_formula": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["hooks", "title_pattern", "timing_factor", "replicable_formula", "confidence"],
}

_EXPLAIN_OUTLIER_SYSTEM = (
    "You explain why one YouTube video outperformed its channel's own "
    "baseline, for a creator's content-strategy dashboard. Base your answer "
    "ONLY on the numbers and text given below -- title, description, tags, "
    "duration, publish time, the outlier multiplier, the channel's baseline "
    "views, view velocity, up to 10 other titles from the same channel for "
    "comparison, and the channel's format/topic labels if given. Never "
    "invent facts the input doesn't support. Output at most 3 hooks (specific "
    "packaging choices that plausibly drove the outperformance), one "
    "title_pattern (the structural pattern vs. the channel's other titles), "
    "a timing_factor (empty string if publish timing doesn't plausibly "
    "explain anything), a replicable_formula (<=160 chars, actionable for "
    "the creator's next video), and a confidence 0..1 reflecting how much "
    "the given numbers actually support this explanation -- low confidence "
    "is fine and expected when the signal is thin."
)


def _build_explain_input(vrow, target, other_titles: list, labels: dict) -> str:
    lines = [
        f"Title: {vrow['title']}",
        f"Description: {(vrow.get('description') or '')[:500]}",
        f"Tags: {vrow.get('tags') or 'none'}",
        f"Duration (s): {vrow.get('duration_seconds')}",
        f"Published at: {vrow.get('published_at')}",
        f"Outlier multiplier (rolling, vs. last N uploads): {target.get('outlierScoreRolling')}",
        f"Outlier multiplier (period, vs. same-season uploads): {target.get('outlierScorePeriod')}",
        f"Channel baseline median views: {target.get('baselineMedianViews')}",
        f"Views per hour (first 24h): {target.get('vph24h')}",
        "Other titles from this channel:",
        *[f"- {t}" for t in other_titles],
    ]
    if labels:
        lines.append(f"Channel format/topic labels: {json.dumps(labels)}")
    return "\n".join(lines)


def explain_outlier(video_id: str, force_refresh: bool = False) -> dict:
    """Stage 05: LLM explanation of why one video beat its channel's
    baseline -- hooks, title pattern, timing factor, a short replicable
    formula. Cached in video_insights (task='why_viral') for
    LLM_WHY_VIRAL_TTL_DAYS (14). Zero YouTube quota (reads only what's
    already collected); costs an LLM call on a cache miss."""
    conn = db.get_conn()
    vrow = conn.execute(
        "SELECT video_id, channel_id, title, description, tags, duration_seconds, "
        "published_at FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    if not vrow:
        conn.close()
        return {"videoId": video_id, "found": False,
               "hint": "video not collected -- run collect_channel first", **_EMPTY_EXPLAIN}
    vrow = dict(vrow)
    channel_id = vrow["channel_id"]

    if not force_refresh:
        cached = conn.execute(
            "SELECT result, model, created_at FROM video_insights WHERE video_id=? AND task=?",
            (video_id, "why_viral")).fetchone()
        if cached and cached["created_at"] and P.days_since(cached["created_at"]) <= LLM_WHY_VIRAL_TTL_DAYS:
            conn.close()
            return {"videoId": video_id, "found": True, "cached": True, "model": cached["model"],
                   "createdAt": cached["created_at"], **cached["result"]}

    ch = conn.execute("SELECT llm_labels FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
    conn.close()

    from application import discovery as trends
    rows = trends.load_window(period="all", channel_ids=[channel_id])
    target = next((r for r in rows if r["video_id"] == video_id), None)
    if target is None or (target.get("outlierScoreRolling") is None
                          and target.get("outlierScorePeriod") is None):
        return {"videoId": video_id, "found": True, "cached": False,
               "hint": "not enough channel history yet for an outlier baseline -- "
                       "collect more of this channel's videos first", **_EMPTY_EXPLAIN}

    other_titles = [r["title"] for r in rows if r["video_id"] != video_id][:10]
    labels = (ch["llm_labels"] if ch else None) or {}
    user_input = _build_explain_input(vrow, target, other_titles, labels)
    model = factory.default_model()
    data = gw.run("explain_outlier", _EXPLAIN_OUTLIER_SYSTEM, user_input,
                  EXPLAIN_OUTLIER_SCHEMA, model=model)
    if data is None:
        return {"videoId": video_id, "found": True, "cached": False,
               "hint": "LLM_PROVIDER is none, or the daily LLM budget is exhausted -- "
                       "set LLM_PROVIDER=openrouter + OPENROUTER_API_KEY to enable this",
               **_EMPTY_EXPLAIN}

    conn = db.get_conn()
    db.save_video_insights(conn, video_id, "why_viral", data, model or "auto")
    conn.commit()
    conn.close()
    return {"videoId": video_id, "found": True, "cached": False, "model": model or "auto", **data}


# ------------------------------------------------------------ title scoring (09)

SCORE_TITLES_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "score": {"type": "integer"},
                    "strengths": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "improved": {"type": "string"},
                },
                "required": ["title", "score", "strengths", "risks", "improved"],
            },
        },
    },
    "required": ["titles"],
}

_SCORE_TITLES_SYSTEM = (
    "You score YouTube title candidates for a creator's content-strategy "
    "dashboard, 0-100 each. Ground every score in the given title patterns "
    "that actually correlate with outliers in this niche/channel (cite the "
    "matching ones in strengths, missing ones in risks) and the niche's "
    "best-performing titles for style comparison. strengths/risks are short "
    "bullet phrases; improved is one rewritten version of the title. Score "
    "realistically -- most titles are mediocre, reserve 80+ for titles that "
    "genuinely match multiple proven patterns."
)

SUGGEST_TITLES_SCHEMA = {
    "type": "object",
    "properties": {"titles": {"type": "array", "items": {"type": "string"}}},
    "required": ["titles"],
}

_SUGGEST_TITLES_SYSTEM = (
    "You generate YouTube title candidates for a creator's content-strategy "
    "dashboard, in the style of the niche's best-performing titles given "
    "below. Each title must be about the given topic, distinct from the "
    "others, and should lean on the same structural patterns the reference "
    "titles use where it genuinely fits -- don't force a pattern that "
    "doesn't suit the topic."
)


def _title_context(niche_slug: str = None, channel_id: str = None) -> dict:
    from application import channel_tracking as CT
    from application import discovery as trends
    patterns_res = CT.title_patterns(niche=niche_slug, channel_id=channel_id)
    rows = trends.load_window(period="all", niche=niche_slug,
                              channel_ids=[channel_id] if channel_id else None)
    top_rows = sorted(rows, key=lambda r: r.get("outlierScore") or 0, reverse=True)[:20]
    return {
        "patterns": patterns_res.get("patterns") or [],
        "topTitles": [r["title"] for r in top_rows if r.get("title")],
        "videoRows": rows,
    }


def _near_duplicate_scores(titles: list, video_rows: list) -> dict:
    """title -> max cosine similarity against this niche/channel's already-
    published videos, or None per title if embeddings aren't available."""
    try:
        import infrastructure.embeddings.fastembed_provider as emb
    except Exception:
        return {t: None for t in titles}

    video_ids = [r["video_id"] for r in video_rows if r.get("video_id")]
    existing_vecs = []
    if video_ids:
        conn = db.get_conn()
        try:
            for i in range(0, len(video_ids), 400):
                chunk = video_ids[i:i + 400]
                sql = ("SELECT embedding FROM videos WHERE video_id IN (%s) "
                      "AND embedding IS NOT NULL" % ",".join("?" * len(chunk)))
                existing_vecs += [emb.from_blob(r["embedding"])
                                 for r in conn.execute(sql, chunk).fetchall()]
        finally:
            conn.close()
    if not existing_vecs:
        return {t: None for t in titles}

    out = {}
    for t in titles:
        v = emb.embed(t)
        out[t] = max(emb.cosine(v, ev) for ev in existing_vecs)
    return out


def score_titles(candidates: list, niche_slug: str = None, channel_id: str = None) -> dict:
    """Stage 09: each candidate gets a deterministic score (length, digits,
    matched title patterns, near-duplicate check -- works with no LLM at
    all) plus, when an LLM is configured, a richer 0-100 score grounded in
    the niche's actual title patterns with strengths/risks/an improved
    rewrite. Falls back to the deterministic score alone (score ==
    deterministicScore, empty strengths/risks) when the LLM is off."""
    candidates = [c.strip() for c in (candidates or []) if c and c.strip()]
    if not candidates:
        raise ValueError("candidates is required")
    if not niche_slug and not channel_id:
        raise ValueError("pass niche_slug or channel_id")

    ctx = _title_context(niche_slug=niche_slug, channel_id=channel_id)
    near_dup = _near_duplicate_scores(candidates, ctx["videoRows"])

    det_by_title = {}
    for title in candidates:
        signals = TSC.deterministic_signals(title, ctx["patterns"],
                                            near_duplicate_score=near_dup.get(title))
        det_by_title[title] = {"signals": signals,
                               "deterministicScore": TSC.deterministic_score(signals)}

    model = factory.default_model()
    llm_by_title = {}
    user_input = (
        "Title patterns (keyword, outlier lift):\n"
        + "\n".join(f"- {p['keyword']} ({p.get('outlierLift')})" for p in ctx["patterns"][:15])
        + "\n\nBest-performing titles in this niche/channel:\n"
        + "\n".join(f"- {t}" for t in ctx["topTitles"])
        + "\n\nCandidates to score:\n" + "\n".join(f"- {t}" for t in candidates)
    )
    data = gw.run("score_titles", _SCORE_TITLES_SYSTEM, user_input, SCORE_TITLES_SCHEMA,
                  model=model)
    if data:
        llm_by_title = {t["title"]: t for t in data.get("titles") or []}

    out = []
    for title in candidates:
        det = det_by_title[title]
        llm = llm_by_title.get(title)
        out.append({
            "title": title,
            "score": llm["score"] if llm else det["deterministicScore"],
            "strengths": llm["strengths"] if llm else [],
            "risks": llm["risks"] if llm else [],
            "improved": llm["improved"] if llm else None,
            "deterministicScore": det["deterministicScore"], "signals": det["signals"],
        })
    return {
        "niche": niche_slug, "channelId": channel_id, "titles": out,
        "llmUsed": bool(llm_by_title),
        "hint": None if llm_by_title else
            "LLM_PROVIDER is none, or the daily LLM budget is exhausted -- "
            "scores are deterministic-only (length/patterns/duplicates)",
    }


def suggest_titles(topic: str, niche_slug: str = None, channel_id: str = None,
                   n: int = 10) -> dict:
    """Stage 09: generate up to n title candidates for `topic` in the style
    of the niche/channel's best-performing titles, then score them the same
    way score_titles() does. Requires an LLM (generation, unlike scoring,
    has no meaningful deterministic fallback)."""
    if not topic or not topic.strip():
        raise ValueError("topic is required")
    if not niche_slug and not channel_id:
        raise ValueError("pass niche_slug or channel_id")

    ctx = _title_context(niche_slug=niche_slug, channel_id=channel_id)
    model = factory.default_model()
    user_input = (
        f"Topic: {topic}\n\nTitle patterns that correlate with outliers here:\n"
        + "\n".join(f"- {p['keyword']}" for p in ctx["patterns"][:15])
        + "\n\nBest-performing titles in this niche/channel:\n"
        + "\n".join(f"- {t}" for t in ctx["topTitles"])
        + f"\n\nGenerate up to {n} distinct title candidates for this topic."
    )
    data = gw.run("suggest_titles", _SUGGEST_TITLES_SYSTEM, user_input, SUGGEST_TITLES_SCHEMA,
                  model=model)
    if not data or not data.get("titles"):
        return {"topic": topic, "niche": niche_slug, "channelId": channel_id, "titles": [],
               "hint": "LLM_PROVIDER is none, or the daily LLM budget is exhausted -- "
                       "title generation has no deterministic fallback"}

    candidates = data["titles"][:n]
    scored = score_titles(candidates, niche_slug=niche_slug, channel_id=channel_id)
    return {"topic": topic, **scored}

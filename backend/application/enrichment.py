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
import os
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from application import llm_gateway as gw
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

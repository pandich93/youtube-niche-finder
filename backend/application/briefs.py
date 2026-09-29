"""Outlier -> brief (plan 02): turn one video that beat its channel's baseline
into a working brief for the creator's OWN video, from tools that already
exist -- so the answer to "found an outlier, now what?" is one call instead
of five. Zero YouTube quota; LLM parts run only with use_llm=True and a
configured provider, and every part that could not run is listed in
`skipped` with the reason rather than papered over with a template.

The brief is research, not a script: it says what worked and what is already
covered so the creator picks a different angle; it never rewrites the source.
"""
import infrastructure.postgres as db
from application import channel_tracking as T
from application import discovery as trends
from application import enrichment as EN
from application import metadata_review as MR
from application import search as Q
from application import transcripts as TR
from domain import idea_verdicts as IV

HOOK_WORDS = 75          # ~30 seconds of speech
SIMILAR_LIMIT = 5
TITLE_CANDIDATES = 8
OVERLAP_MATCHES = 25     # ask for more than we show: the source itself is dropped
NOTE = ("Research, not a script: use it to choose your own angle. What worked is not "
        "something to copy, and the overlap section shows what is already covered.")


def _skip(skipped, part, reason):
    skipped.append({"part": part, "reason": reason})


def _source(conn, video_id):
    row = conn.execute(
        "SELECT v.video_id, v.channel_id, v.title, v.duration_seconds, v.is_short, "
        "c.title AS channel_title FROM videos v LEFT JOIN channels c "
        "ON c.channel_id = v.channel_id WHERE v.video_id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def _niche_of(conn, video_id):
    row = conn.execute("SELECT niche_slug FROM video_niches WHERE video_id = ? "
                       "ORDER BY niche_slug LIMIT 1", (video_id,)).fetchone()
    return row["niche_slug"] if row else None


def _hook(conn, video_id, skipped):
    row = conn.execute("SELECT text FROM transcripts WHERE video_id = ?", (video_id,)).fetchone()
    words = (row["text"] or "").split() if row else []
    if words:
        return {"available": True, "words": min(len(words), HOOK_WORDS),
                "text": " ".join(words[:HOOK_WORDS])}
    _skip(skipped, "hook", "no transcript stored -- transcripts are never fetched automatically")
    return {"available": False,
            "action": "Вставьте транскрипт на экране «Транскрипты» — крючок добавится в бриф."}


def _angle(niche, channel_id):
    ctx = {"niche": niche} if niche else {"channel_id": channel_id}
    patterns = T.title_patterns(top_n=8, **ctx).get("patterns", [])
    best = T.best_time_to_publish(**ctx).get("best", [])[:3]
    return {"patterns": patterns, "bestTime": best}


def _overlap(title, niche, source_id):
    res = Q.check_ideas([title], niche=niche, matches_per_idea=OVERLAP_MATCHES)
    idea = (res.get("ideas") or [{}])[0]
    matches = [m for m in idea.get("matches", []) if m.get("videoId") != source_id]
    v = IV.verdict(matches) if matches else IV.verdict([])
    return {**v, "matchCount": len(matches), "matches": matches[:10],
            "semanticSearchAvailable": res.get("semanticSearchAvailable")}


def _references(conn, video_id):
    sim = Q.similar_videos(video_id, limit=SIMILAR_LIMIT).get("similar", [])
    out = []
    for s in sim:
        row = conn.execute("SELECT thumbnail FROM videos WHERE video_id = ?",
                           (s["videoId"],)).fetchone()
        out.append({**s, "thumbnail": row["thumbnail"] if row else None})
    return out


def build_brief(video_id: str, niche: str = None, use_llm: bool = True,
                save: bool = True, gap_topic: str = None) -> dict:
    """Brief for a video of your own, built from one outlier: its numbers and
    hook, why it worked and title patterns of the niche, whether the topic is
    already covered (source excluded), title candidates, and similar videos as
    thumbnail references. save=True also stores it as a draft
    (drafts.source_video_id points back at the outlier) and queues a missing
    transcript; save=False writes nothing. Zero YouTube quota.

    gap_topic (plan 03): a viewer question found under this video's comments.
    The overlap check and the title candidates are then about the gap, not
    the source title, and without an LLM the gap is the draft's working
    title -- the source stays as the reference for hook and numbers."""
    gap_topic = (gap_topic or "").strip() or None
    conn = db.get_conn()
    try:
        src = _source(conn, video_id)
        if not src:
            return {"videoId": video_id, "found": False,
                    "hint": "video not collected -- run collect_channel first"}
        niche = niche or _niche_of(conn, video_id)
        topic = gap_topic or src["title"]
        skipped = []
        hook = _hook(conn, video_id, skipped)

        rows = trends.load_window(period="all", channel_ids=[src["channel_id"]])
        row = next((r for r in rows if r["video_id"] == video_id), {})
        source = {"videoId": video_id, "title": src["title"], "channelId": src["channel_id"],
                  "channelTitle": src["channel_title"], "views": row.get("view_count"),
                  "outlierScore": row.get("outlierScore"),
                  "outlierBand": row.get("outlierBand"),
                  "viewsPerSubscriber": row.get("viewsPerSubscriber"),
                  "lengthSeconds": src["duration_seconds"],
                  "isShort": bool(src["is_short"]), "thumbnail": row.get("thumbnail"),
                  "publishedAt": row.get("published_at")}

        if source["outlierScore"] is None:
            _skip(skipped, "source", "no outlier score yet -- the channel needs 4+ collected "
                                     "videos for a baseline, so this may not be an outlier at all")
        angle, overlap, references = {"patterns": [], "bestTime": []}, None, []
        try:
            angle = _angle(niche, src["channel_id"])
        except Exception as e:  # a thin corpus must not sink the whole brief
            _skip(skipped, "angle", f"not enough data for patterns: {e}")
        title_l = (src["title"] or "").lower()
        angle["matchedInTitle"] = [p["keyword"] for p in angle["patterns"]
                                   if p.get("keyword") and p["keyword"].lower() in title_l]
        try:
            overlap = _overlap(topic, niche, video_id)
        except Exception as e:
            _skip(skipped, "overlap", str(e))
        try:
            references = _references(conn, video_id)
            if not references:
                _skip(skipped, "thumbnailReferences",
                      "no similar videos -- the video has no embedding yet "
                      "(backfill_embeddings) or nothing close is collected")
        except Exception as e:
            _skip(skipped, "thumbnailReferences", str(e))

        why, suggestions = None, []
        if use_llm:
            try:
                res = EN.explain_outlier(video_id)
                if res.get("hooks"):
                    why = {k: res.get(k) for k in ("hooks", "title_pattern", "timing_factor",
                                                   "replicable_formula", "confidence")}
                else:
                    _skip(skipped, "why_viral", res.get("hint") or "the LLM returned nothing")
            except Exception as e:
                _skip(skipped, "why_viral", str(e))
            try:
                res = EN.suggest_titles(topic, niche_slug=niche,
                                        channel_id=None if niche else src["channel_id"],
                                        n=TITLE_CANDIDATES)
                suggestions = sorted(res.get("titles") or [],
                                     key=lambda t: t.get("score") or 0, reverse=True)
                if not suggestions:
                    _skip(skipped, "titles", res.get("hint") or "the LLM returned nothing")
            except Exception as e:
                _skip(skipped, "titles", str(e))
        else:
            _skip(skipped, "why_viral", "needs an LLM (use_llm=false)")
            _skip(skipped, "titles", "needs an LLM (use_llm=false); no template stands in for it")

        brief = {
            "found": True, "videoId": video_id, "niche": niche, "kind": "brief",
            "gapTopic": gap_topic,
            "source": source, "hook": hook, "why": why, "angle": angle,
            "titles": {"suggestions": suggestions,
                       "patternSkeletons": [p["keyword"] for p in angle["patterns"][:5]]},
            "overlap": overlap, "thumbnailReferences": references,
            "skipped": skipped, "llmUsed": bool(why or suggestions),
            "quotaUsed": 0, "note": NOTE, "draftId": None,
        }
    finally:
        conn.close()

    if save:
        if not hook["available"]:
            TR.request_transcript(video_id, reason="brief")
        working = suggestions[0]["title"] if suggestions else topic
        brief["draftId"] = MR.save_draft(
            working, niche=niche, is_short=source["isShort"], review=brief,
            source_video_id=video_id)["id"]
    return brief

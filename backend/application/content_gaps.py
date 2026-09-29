"""Content gaps (plan 03): what viewers of a niche ask for in the comments that
no collected video answers yet -- demand without supply.

Per top video of the niche (by views) the questions come from one of two places:
  llm    the requests of a cached comment_insights() report (stage 04), when an
         LLM provider is configured;
  rules  domain.content_gaps.extract_questions over the raw comments, cached in
         video_insights under task 'comment_questions' as {text, likeCount}
         only -- never the author.
Comments cost 1 YouTube unit per video and only with fetch=True; otherwise (and
once the quota runs out) only cached videos are used and the rest are listed in
skippedVideos with the reason. The worker never fetches comments.

Similar questions are grouped by embeddings (by normalised text without them),
then each group is checked against the niche: check_ideas() over collected
titles, plus cosine over pasted transcript chunks. Coverage is only as good as
the local corpus -- coverageBase in the answer says how big it is.
"""
import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from application import collecting as collector
from application import enrichment as EN
from application import search as Q
from domain import content_gaps as G
from domain import periods as P
from infrastructure.llm import factory
from infrastructure.llm.null import NullProvider

TASK = "comment_questions"
MAX_COMMENTS = 100     # one commentThreads.list page = 1 unit
MAX_TOP_VIDEOS = 30
MAX_LIMIT = 50
EXAMPLES = 3
COVERED_SHOWN = 5
RULES_NOTE = ("No LLM: questions were picked by rules (a question mark or an explicit "
              "request, English and Russian) -- expect more noise and misses than with "
              "an LLM, especially in other languages.")
NOTE = ("A gap is only as real as the corpus: coverage is checked against the videos "
        "and transcripts collected here, not all of YouTube.")


def _llm_enabled() -> bool:
    return not isinstance(factory.get_provider(), NullProvider)


def _embed(texts):
    """Vectors for texts, or None when the local model is unavailable."""
    if not texts:
        return None
    try:
        import infrastructure.embeddings.fastembed_provider as emb
        return list(emb.embed(list(texts)))
    except Exception:
        return None


def _cached(conn, video_id, task):
    row = conn.execute("SELECT result, created_at FROM video_insights "
                       "WHERE video_id = ? AND task = ?", (video_id, task)).fetchone()
    if row and row["created_at"] and P.days_since(row["created_at"]) <= EN.LLM_INSIGHTS_TTL_DAYS:
        return row["result"] or {}
    return None


def _llm_items(result):
    return [{"text": r["topic"].strip(), "likeCount": 0,
             "example": (r.get("evidence") or "").strip() or None}
            for r in (result or {}).get("requests") or [] if (r.get("topic") or "").strip()]


def _rules_items(result):
    return [{"text": q["text"], "likeCount": int(q.get("likeCount") or 0)}
            for q in (result or {}).get("questions") or [] if q.get("text")]


def _top_videos(conn, niche, n):
    return [dict(r) for r in conn.execute(
        "SELECT v.video_id, v.title, v.view_count, v.is_short, v.comment_count FROM videos v "
        "JOIN video_niches vn ON vn.video_id = v.video_id WHERE vn.niche_slug = ? "
        "ORDER BY v.view_count DESC NULLS LAST, v.video_id LIMIT ?", (niche, n)).fetchall()]


def _coverage_base(conn, niche):
    row = conn.execute(
        "SELECT COUNT(*) AS videos, COUNT(v.embedding) AS emb FROM videos v "
        "JOIN video_niches vn ON vn.video_id = v.video_id WHERE vn.niche_slug = ?",
        (niche,)).fetchone()
    tr = conn.execute(
        "SELECT COUNT(DISTINCT tc.video_id) FROM transcript_chunks tc "
        "JOIN video_niches vn ON vn.video_id = tc.video_id WHERE vn.niche_slug = ?",
        (niche,)).fetchone()[0]
    return {"videos": row["videos"], "withEmbeddings": row["emb"], "transcripts": tr}


def _transcript_chunks(conn, niche):
    try:
        import infrastructure.embeddings.fastembed_provider as emb
    except Exception:
        return []
    rows = conn.execute(
        "SELECT tc.video_id, tc.start_sec, tc.text, tc.embedding, v.title FROM transcript_chunks tc "
        "JOIN video_niches vn ON vn.video_id = tc.video_id "
        "LEFT JOIN videos v ON v.video_id = tc.video_id "
        "WHERE vn.niche_slug = ? AND tc.embedding IS NOT NULL", (niche,)).fetchall()
    return [(dict(r), emb.from_blob(r["embedding"])) for r in rows]


def _questions(api_key, videos, llm, fetch):
    """(items, sources, skipped, quota): items carry their videoId."""
    items, sources, skipped, quota, quota_hit = [], [], [], 0, False
    for v in videos:
        vid = v["video_id"]
        conn = db.get_conn()
        try:
            llm_hit = _cached(conn, vid, "comment_insights") if llm else None
            rules_hit = _cached(conn, vid, TASK) if llm_hit is None else None
        finally:
            conn.close()
        kind, got, origin = None, None, "cache"
        if llm_hit is not None:
            kind, got = "llm", _llm_items(llm_hit)
        elif rules_hit is not None:
            kind, got = "rules", _rules_items(rules_hit)
        elif not fetch:
            skipped.append({"videoId": vid, "title": v["title"], "reason": "not-fetched"})
            continue
        elif quota_hit:
            skipped.append({"videoId": vid, "title": v["title"], "reason": "quota-exhausted"})
            continue
        elif v.get("comment_count") == 0:
            skipped.append({"videoId": vid, "title": v["title"], "reason": "no-comments"})
            continue
        else:
            origin = "fetched"
            try:
                if llm:
                    res = EN.comment_insights(api_key, vid, max_comments=MAX_COMMENTS)
                    quota += int(res.get("quotaSpent") or 0)
                    if res.get("hint") and not res.get("requests"):
                        skipped.append({"videoId": vid, "title": v["title"], "reason": "llm-unavailable",
                                        "detail": res["hint"]})
                        continue
                    kind, got = "llm", _llm_items(res)
                else:
                    raw = collector.video_comments(api_key, vid, max_results=MAX_COMMENTS)
                    quota += int((raw.get("quota") or {}).get("units_from_shared_pool") or 0)
                    comments = raw.get("comments") or []
                    qs = G.extract_questions(comments)
                    conn = db.get_conn()
                    try:
                        db.save_video_insights(conn, vid, TASK,
                                               {"questions": qs, "commentsRead": len(comments)},
                                               "rules")
                        conn.commit()
                    finally:
                        conn.close()
                    kind, got = "rules", _rules_items({"questions": qs})
            except yt.QuotaExceeded:
                quota_hit = True
                skipped.append({"videoId": vid, "title": v["title"], "reason": "quota-exhausted"})
                continue
            except Exception as e:
                skipped.append({"videoId": vid, "title": v["title"], "reason": "error",
                                "detail": str(e)[:200]})
                continue
        sources.append({"videoId": vid, "title": v["title"], "views": v["view_count"],
                        "isShort": bool(v["is_short"]), "kind": kind, "origin": origin,
                        "questions": len(got)})
        items.extend({**it, "videoId": vid} for it in got)
    return items, sources, skipped, quota


def _video_coverage(topics, niche):
    """{topic: (coverage, nearest, verdict)} from check_ideas, plus whether
    it could match by meaning or only by title substring."""
    out, semantic = {}, False
    for i in range(0, len(topics), Q.MAX_IDEAS_PER_CALL):
        res = Q.check_ideas(topics[i:i + Q.MAX_IDEAS_PER_CALL], niche=niche,
                            matches_per_idea=10_000)
        semantic = semantic or bool(res.get("semanticSearchAvailable"))
        for idea in res.get("ideas") or []:
            best, best_sim = None, None
            for m in idea.get("matches") or []:
                sim = 1.0 if m["matchedBy"] == "title" else m.get("semanticScore")
                if sim is not None and (best_sim is None or sim > best_sim):
                    best, best_sim = m, sim
            nearest = None if best is None else {
                "videoId": best["videoId"], "title": best["title"],
                "channelTitle": best.get("channelTitle"), "views": best.get("views"),
                "similarity": round(best_sim, 3), "matchedBy": best["matchedBy"]}
            out[idea["idea"]] = (best_sim, nearest, idea.get("verdict"))
    return out, semantic


def _group(items, vectors):
    groups = G.cluster_questions(items, vectors)
    out = []
    for g in groups:
        members = sorted((items[i] for i in g), key=lambda it: it["likeCount"], reverse=True)
        rep = max(g, key=lambda i: items[i]["likeCount"])
        video_ids = list(dict.fromkeys(it["videoId"] for it in members))
        examples = list(dict.fromkeys(it.get("example") or it["text"] for it in members))
        out.append({"topic": items[rep]["text"], "vector": vectors[rep] if vectors else None,
                    "askers": len(members), "likes": sum(it["likeCount"] for it in members),
                    "demand": G.demand(members, sources=len(video_ids)),
                    "sourceVideos": video_ids, "examples": examples[:EXAMPLES]})
    return out


def content_gaps(api_key, niche: str, top_videos: int = 10, use_llm: bool = None,
                 fetch: bool = False, limit: int = 20) -> dict:
    """Viewer questions from the comments of the niche's top videos that no
    collected video (or pasted transcript) covers, ranked by demand x (1 -
    coverage). fetch=True spends 1 quota unit per uncached video; fetch=False
    reads only the cache. use_llm=None means "if a provider is configured"."""
    top_videos = max(1, min(int(top_videos), MAX_TOP_VIDEOS))
    limit = max(1, min(int(limit), MAX_LIMIT))
    if fetch and not api_key:
        raise ValueError("YOUTUBE_API_KEY is required to fetch comments (fetch=true)")
    llm = _llm_enabled() if use_llm is None else bool(use_llm)

    conn = db.get_conn()
    try:
        videos = _top_videos(conn, niche, top_videos)
        base = _coverage_base(conn, niche)
    finally:
        conn.close()
    answer = {"niche": niche, "found": bool(videos), "mode": "llm" if llm else "rules",
              "modeNote": None if llm else RULES_NOTE, "gaps": [], "covered": [],
              "coveredCount": 0, "sourceVideos": [], "skippedVideos": [], "quotaSpent": 0,
              "coverageBase": base, "semanticSearchAvailable": False, "note": NOTE}
    if not videos:
        answer["hint"] = f"no collected videos in niche {niche!r} -- run collect_niche first"
        return answer

    items, sources, skipped, quota = _questions(api_key, videos, llm, fetch)
    answer.update(sourceVideos=sources, skippedVideos=skipped, quotaSpent=quota)
    if not items:
        answer["hint"] = ("no viewer questions yet -- call with fetch=true to read comments "
                          "(1 quota unit per video)" if skipped else
                          "the comments of these videos contain no questions or requests")
        return answer

    vectors = _embed([it["text"] for it in items])
    groups = _group(items, vectors)
    by_topic, semantic = _video_coverage([g["topic"] for g in groups], niche)
    conn = db.get_conn()
    try:
        chunks = _transcript_chunks(conn, niche) if vectors else []
    finally:
        conn.close()

    gaps, covered = [], []
    for g in groups:
        cov, nearest, verdict = by_topic.get(g["topic"], (None, None, None))
        near_tr = None
        if chunks and g["vector"] is not None:
            row, sim = max(((r, G.cosine(g["vector"], vec)) for r, vec in chunks),
                           key=lambda t: t[1])
            if sim >= G.PARTIAL_AT:
                near_tr = {"videoId": row["video_id"], "title": row["title"],
                           "startSec": row["start_sec"], "text": (row["text"] or "")[:300],
                           "similarity": round(sim, 3)}
                cov = sim if cov is None else max(cov, sim)
        g.pop("vector")
        entry = {**g, "status": G.gap_status(cov), "coverage": None if cov is None else round(cov, 3),
                 "score": G.gap_score(g["demand"], cov), "nearestVideo": nearest,
                 "nearestTranscript": near_tr, "ideaVerdict": verdict}
        (covered if entry["status"] == "covered" else gaps).append(entry)

    gaps.sort(key=lambda e: (-e["score"], -e["demand"], e["topic"]))
    covered.sort(key=lambda e: (-e["demand"], e["topic"]))
    answer.update(gaps=gaps[:limit], covered=covered[:COVERED_SHOWN], coveredCount=len(covered),
                  gapCount=len(gaps), semanticSearchAvailable=semantic)
    return answer

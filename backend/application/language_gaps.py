"""Language gaps (plan 26) -- the rule is in domain/language_gaps.py. For each
outlier in the source language: the nearest videos in the target language
(embedding cosine, the multilingual model reads "как я построил ИИ-лабораторию"
close to "how I built an AI lab"), their outlier scores from the same
load_window everything else uses, and how many other channels repeated the
format at home. Zero quota, no LLM. Reads only: similar_videos and
search_outliers are not touched.
"""
import numpy as np

from application import discovery as trends
from domain import language_gaps as L
from infrastructure import postgres as db

MAX_SOURCES = 100          # source outliers examined, best first
MAX_PER_CHANNEL = 5        # ... of which one channel's string of hits takes at most this many
TARGET_NEIGHBOURS = 5      # per source, target language
HOME_NEIGHBOURS = 10       # per source, source language, other channels
NOTE = ("An estimate of niche-finder over the videos we collected: 'open' can also mean the "
        "target-language channels were never collected, and a video's language is what its "
        "channel declared or what we detected from the title.")

# SQL twin of L.normalize_lang for the ANN filter: 'en-US' / 'en_GB' / 'EN' -> 'en'
_LANG_SQL = "LOWER(SPLIT_PART(REPLACE(v.default_language, '_', '-'), '-', 1))"


def _score(r):
    return r.get("outlierScoreAgeAdjusted") or r.get("outlierScore")


def _language_stats(conn) -> dict:
    """{code: {"videos": n, "channels": {ids}}} over videos with an embedding
    (the ones that can be a neighbour); 'en-US' and 'en' merge."""
    out = {}
    for r in conn.execute("SELECT default_language, channel_id, COUNT(*) AS n FROM videos "
                          "WHERE embedding IS NOT NULL GROUP BY default_language, channel_id"
                          ).fetchall():
        code = L.normalize_lang(r["default_language"])
        if code is None:
            continue
        s = out.setdefault(code, {"videos": 0, "channels": set()})
        s["videos"] += r["n"]
        s["channels"].add(r["channel_id"])
    return out


def languages() -> list:
    """Languages we hold videos in, biggest first -- for the pair pickers."""
    conn = db.get_conn()
    try:
        stats = _language_stats(conn)
    finally:
        conn.close()
    return sorted(({"code": c, "videos": s["videos"], "channels": len(s["channels"])}
                   for c, s in stats.items()), key=lambda x: x["videos"], reverse=True)


def _source_vectors(conn, video_ids) -> dict:
    import infrastructure.embeddings.fastembed_provider as emb
    out = {}
    for i in range(0, len(video_ids), 400):
        chunk = video_ids[i:i + 400]
        sql = ("SELECT video_id, embedding FROM videos WHERE embedding IS NOT NULL "
               "AND video_id IN (%s)" % ",".join("?" * len(chunk)))
        for r in conn.execute(sql, chunk).fetchall():
            out[r["video_id"]] = emb.from_blob(r["embedding"])
    return out


def _nearest(conn, sources: dict, vectors: dict, lang: str, k: int, other_channels: bool) -> dict:
    """{source video_id: [{videoId, channelId, similarity}]} -- the k nearest
    videos in `lang`; other_channels leaves out the source's own channel."""
    import infrastructure.embeddings.fastembed_provider as emb
    out = {}
    if db.pgvector_available():
        db.filtered_ann(conn)  # the language filter runs after HNSW's first ~40 rows
        for vid, channel_id in sources.items():
            if vid not in vectors:
                continue
            literal = emb.to_pgvector_literal(vectors[vid])
            where, params = ["v.embedding_v IS NOT NULL", f"{_LANG_SQL} = ?", "v.video_id != ?"], [lang, vid]
            if other_channels:
                where.append("v.channel_id != ?")
                params.append(channel_id)
            rows = conn.execute(
                "SELECT v.video_id, v.channel_id, (1 - (v.embedding_v <=> ?::vector)) AS similarity "
                f"FROM videos v WHERE {' AND '.join(where)} "
                "ORDER BY v.embedding_v <=> ?::vector LIMIT ?",
                [literal] + params + [literal, k]).fetchall()
            out[vid] = [{"videoId": r["video_id"], "channelId": r["channel_id"],
                         "similarity": round(r["similarity"], 4)} for r in rows]
        return out
    # no pgvector: score the language's embeddings here, like similar_videos does
    cand = [r for r in conn.execute("SELECT video_id, channel_id, default_language, embedding "
                                    "FROM videos WHERE embedding IS NOT NULL").fetchall()
            if L.normalize_lang(r["default_language"]) == lang]
    if not cand:
        return {vid: [] for vid in sources if vid in vectors}
    mat = np.vstack([emb.from_blob(r["embedding"]) for r in cand])
    mat = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
    for vid, channel_id in sources.items():
        if vid not in vectors:
            continue
        v = vectors[vid]
        sims = mat @ (v / max(float(np.linalg.norm(v)), 1e-12))
        hits = []
        for idx in np.argsort(-sims):
            r = cand[int(idx)]
            if r["video_id"] == vid or (other_channels and r["channel_id"] == channel_id):
                continue
            hits.append({"videoId": r["video_id"], "channelId": r["channel_id"],
                         "similarity": round(float(sims[idx]), 4)})
            if len(hits) == k:
                break
        out[vid] = hits
    return out


def _fill(neighbour: dict, by_id: dict) -> dict:
    r = by_id.get(neighbour["videoId"]) or {}
    return {**neighbour, "title": r.get("title"), "channelTitle": r.get("channel_title"),
            "views": r.get("view_count"), "publishedAt": r.get("published_at"),
            "outlierScore": _score(r) if r else None}


def _check(outliers: list, src: str, tgt: str, min_similarity: float):
    """Cards (verdict, matches, demand) for these source rows -> (cards, how
    many had no embedding, language stats)."""
    conn = db.get_conn()
    try:
        stats = _language_stats(conn)
        sources = {r["video_id"]: r["channel_id"] for r in outliers}
        vectors = _source_vectors(conn, list(sources))
        target = _nearest(conn, sources, vectors, tgt, TARGET_NEIGHBOURS, other_channels=False)
        home = _nearest(conn, sources, vectors, src, HOME_NEIGHBOURS, other_channels=True)
    finally:
        conn.close()
    home = {vid: [n for n in ns if n["similarity"] >= L.SOURCE_MIN_SIMILARITY]
            for vid, ns in home.items()}
    found = [n for ns in (*target.values(), *home.values()) for n in ns]
    by_id = ({r["video_id"]: r for r in trends.load_window(
        period="all", channel_ids=sorted({n["channelId"] for n in found}),
        video_ids=sorted({n["videoId"] for n in found}))} if found else {})
    cards, no_vector = [], 0
    for r in outliers:
        vid = r["video_id"]
        if vid not in vectors:
            no_vector += 1
            continue
        score = round(_score(r) or 0, 2)
        cards.append({
            "videoId": vid, "title": r["title"], "channelId": r["channel_id"],
            "channelTitle": r["channel_title"], "views": r["view_count"],
            "publishedAt": r["published_at"], "outlierScore": score,
            "demand": L.demand(score, [_fill(n, by_id) for n in home.get(vid, [])]),
            **L.verdict([_fill(n, by_id) for n in target.get(vid, [])], min_similarity=min_similarity)})
    return cards, no_vector, stats


def _corpus(stats: dict, tgt: str) -> dict:
    videos = stats.get(tgt, {}).get("videos", 0)
    return {"videos": videos, "channels": len(stats.get(tgt, {}).get("channels", ())),
            "thin": videos < L.THIN_CORPUS_VIDEOS, "hint": L.corpus_hint(videos, tgt)}


def language_gaps(source_lang: str, target_lang: str, niche: str = None, min_outlier: float = 3.0,
                  min_similarity: float = L.MIN_SIMILARITY, limit: int = 30) -> dict:
    """Outliers in source_lang and whether anything like them exists in
    target_lang: 'open' (nothing close), 'thin' (close, none an outlier),
    'covered' (close and an outlier). The niche narrows the SOURCE outliers;
    the target language is searched across everything we collected. Zero quota."""
    src, tgt = L.normalize_lang(source_lang), L.normalize_lang(target_lang)
    if not src or not tgt:
        raise ValueError("unknown language: use two-letter codes such as 'en' and 'ru'")
    if src == tgt:
        raise ValueError("pick two different languages")
    rows = trends.load_window(period="all", niche=niche)
    best_first = sorted((r for r in rows if L.normalize_lang(r["default_language"]) == src
                         and (_score(r) or 0) >= min_outlier), key=_score, reverse=True)
    per_channel, outliers = {}, []
    for r in best_first:
        if per_channel.get(r["channel_id"], 0) < MAX_PER_CHANNEL:
            per_channel[r["channel_id"]] = per_channel.get(r["channel_id"], 0) + 1
            outliers.append(r)
        if len(outliers) == MAX_SOURCES:
            break
    cards, no_vector, stats = _check(outliers, src, tgt, min_similarity)
    cards.sort(key=L.sort_key)
    counts = {k: sum(1 for c in cards if c["verdict"] == k) for k in ("open", "thin", "covered")}
    return {"source": src, "target": tgt, "niche": niche, "minOutlier": min_outlier,
            "minSimilarity": min_similarity, "hitScore": L.HIT_SCORE,
            "outliersFound": len(best_first), "sourceOutliers": len(outliers),
            "withoutEmbedding": no_vector, "counts": counts,
            "targetCorpus": _corpus(stats, tgt), "gaps": cards[:limit], "note": NOTE}


def video_language_gap(video_id: str, target_lang: str = None,
                       min_similarity: float = L.MIN_SIMILARITY) -> dict:
    """The same question for one video (the extension's panel): is there
    something like it in target_lang? Works for any video, not only outliers.
    Without target_lang: the biggest other language in the database."""
    rows = trends.load_window(period="all", video_ids=[video_id])
    if not rows:
        raise ValueError("video not in the database")
    row = rows[0]
    src = L.normalize_lang(row["default_language"])
    base = {"videoId": video_id, "source": src, "note": NOTE}
    if src is None:
        return {**base, "target": None, "verdict": None, "reason": "no-language",
                "hint": "the video's language is not known (und or empty)"}
    tgt = L.normalize_lang(target_lang) if target_lang else None
    if target_lang and tgt is None:
        raise ValueError("unknown language: use a two-letter code such as 'ru'")
    if tgt == src:
        raise ValueError("pick a language other than the video's own")
    if tgt is None:
        others = [x["code"] for x in languages() if x["code"] != src]
        if not others:
            return {**base, "target": None, "verdict": None, "reason": "no-other-language",
                    "hint": "the database holds videos in one language only"}
        tgt = others[0]
    cards, no_vector, stats = _check([row], src, tgt, min_similarity)
    if not cards:
        return {**base, "target": tgt, "verdict": None, "reason": "no-embedding",
                "hint": "this video has no embedding yet: run backfill_embeddings"}
    return {**base, "target": tgt, "targetLabel": L.lang_label(tgt),
            "minSimilarity": min_similarity, **cards[0], "targetCorpus": _corpus(stats, tgt)}

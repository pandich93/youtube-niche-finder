"""SEO-помощник по метаданным (plan item 8.8) -- orchestrates domain/metadata.py
against the user's own corpus, and keeps a history of drafts so a later
publish can be linked back for a real outcome check.

Nothing here spends YouTube quota: every signal is computed from what
collect_*/track_channel already stored, same as everything in discovery.py.
Deliberately NOT a single "SEO score" -- see domain/metadata.py's docstring
and docs/plan-iteration-8.md section 8.8 for why.
"""
import json

import infrastructure.postgres as db
from application import discovery as trends
from domain import keywords as K
from domain import metadata as MD
from domain import periods as P

OUTLIER_THRESHOLD_DEFAULT = 3.0


def _outlier_score(r):
    return r.get("outlierScoreAgeAdjusted") or r.get("outlierScore") or r.get("outlierScoreNexlev") or 0


def _top_phrase(rows, outlier_threshold):
    """Best single phrase to anchor the visible-prefix / description checks
    to -- the strongest title_patterns-style phrase in this corpus, or None
    if the sample is too thin to trust one."""
    shaped = [{"video_id": r["video_id"], "title": r["title"], "tags": r["tags"],
              "views": r["view_count"] or 0, "outlier": r["outlier"]} for r in rows]
    stats, total, base = K.aggregate(shaped, use_tags=False, use_title=True, n_max=2,
                                     outlier_threshold=outlier_threshold)
    ranked = K.score(stats, total, base, min_videos=max(3, MD.MIN_RELIABLE_SAMPLE // 2),
                     top_n=1, sort_by="lift")
    return ranked[0]["keyword"] if ranked else None


def _near_duplicates(title, description, niche=None, channel_id=None,
                     limit=5, min_similarity=0.55) -> dict:
    """Cosine similarity of the draft against everything we've already
    collected for this niche/channel -- the "this topic is already covered"
    check neither vidIQ nor NexLev offers, because it needs YOUR history."""
    if not channel_id and not niche:
        return {"checked": 0, "near": [],
                "hint": "pass niche or channel_id to check for topic overlap"}
    import infrastructure.embeddings.fastembed_provider as emb

    text = f"{title or ''}\n{(description or '')[:500]}".strip()
    if not text:
        return {"checked": 0, "near": [], "hint": "empty title/description"}
    target = emb.embed(text)
    conn = db.get_conn()

    if db.pgvector_available():
        # stage 06: threshold + top-k both pushed into SQL against the HNSW
        # index -- see README's pgvector rollback section for the fallback
        # contract this branch exists alongside.
        literal = emb.to_pgvector_literal(target)
        if channel_id:
            where, params = "v.channel_id=? AND v.embedding_v IS NOT NULL", [channel_id]
            joins = ""
        else:
            where = "vn.niche_slug=? AND v.embedding_v IS NOT NULL"
            params = [niche]
            joins = " JOIN video_niches vn ON vn.video_id = v.video_id"
        checked = conn.execute(
            f"SELECT COUNT(*) AS n FROM videos v{joins} WHERE {where}", params).fetchone()["n"]
        sql = (f"SELECT v.video_id, v.title, v.view_count, "
              f"(1 - (v.embedding_v <=> ?::vector)) AS similarity "
              f"FROM videos v{joins} WHERE {where} "
              f"AND (1 - (v.embedding_v <=> ?::vector)) >= ? "
              f"ORDER BY v.embedding_v <=> ?::vector LIMIT ?")
        rows = conn.execute(
            sql, [literal] + params + [literal, min_similarity, literal, limit]).fetchall()
        conn.close()
        near = [{"videoId": r["video_id"], "title": r["title"], "views": r["view_count"],
                "similarity": round(r["similarity"], 3)} for r in rows]
        return {"checked": checked, "near": near}

    if channel_id:
        rows = conn.execute(
            "SELECT video_id, title, view_count, embedding FROM videos "
            "WHERE channel_id=? AND embedding IS NOT NULL", (channel_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT v.video_id, v.title, v.view_count, v.embedding FROM videos v "
            "JOIN video_niches vn ON vn.video_id = v.video_id "
            "WHERE vn.niche_slug=? AND v.embedding IS NOT NULL", (niche,)).fetchall()
    conn.close()

    scored = []
    for r in rows:
        vec = emb.from_blob(r["embedding"])
        score = emb.cosine(target, vec)
        if score >= min_similarity:
            scored.append((r, score))
    scored.sort(key=lambda t: t[1], reverse=True)
    near = [{"videoId": r["video_id"], "title": r["title"], "views": r["view_count"],
            "similarity": round(score, 3)} for r, score in scored[:limit]]
    return {"checked": len(rows), "near": near}


def review_metadata(title: str, description: str = "", tags=None, niche: str = None,
                    channel_id: str = None, is_short: bool = False,
                    period: str = "180d",
                    outlier_threshold: float = OUTLIER_THRESHOLD_DEFAULT) -> dict:
    """The main entry point: everything a draft's title/description/tags can
    be checked against using only what's already in the local database."""
    tags = tags or []
    if not niche and not channel_id:
        return {
            "signals": [], "structuralPatterns": [], "keyPhrase": None,
            "nearDuplicates": {"checked": 0, "near": []},
            "sample": {"videosAnalysed": 0, "outliersInSample": 0, "period": period,
                      "niche": niche, "channelId": channel_id},
            "summary": {"ok": 0, "warn": 0, "unreliable": 0, "total": 0},
            "hint": "pass niche and/or channel_id -- without a comparison pool every "
                    "signal would be unreliable by definition",
        }

    rows = trends.load_window(period=period, niche=niche,
                              channel_ids=[channel_id] if channel_id else None,
                              exclude_shorts=not is_short, only_shorts=is_short)
    for r in rows:
        r["outlier"] = _outlier_score(r)
    outlier_rows = [r for r in rows if r["outlier"] >= outlier_threshold]
    key_phrase = _top_phrase(rows, outlier_threshold) if rows else None

    signals = [
        MD.length_signal("title_length", len(title or ""),
                         [len(r["title"] or "") for r in outlier_rows], "длина заголовка"),
        MD.visible_prefix_signal(title, key_phrase=key_phrase),
        MD.description_signal(description, key_phrase=key_phrase),
    ]

    struct_rows = [{"title": r["title"], "outlier": r["outlier"]} for r in rows]
    structural = MD.structural_lift(struct_rows, outlier_threshold=outlier_threshold)
    draft_features = MD.structural_features(title)
    for feat in structural:
        present = draft_features.get(feat["feature"], False)
        feat["presentInDraft"] = present
        if feat["verdict"] == "unreliable":
            continue
        wants_it = bool(feat["lift"] and feat["lift"] >= 1.2)
        signals.append({
            "id": f"structure_{feat['feature']}",
            "verdict": "ok" if present == wants_it else "warn",
            "explanation": (f"{feat['label']}: lift ×{feat['lift']} у выбросов "
                            f"(выборка {feat['sample']}); в черновике "
                            f"{'есть' if present else 'нет'}, "
                            f"{'и это соответствует паттерну' if present == wants_it else 'а паттерн ниши другой'}"),  # noqa: E501
        })

    trending_phrases = []
    if niche:
        tkw = trends.trending_keywords(period=period, niche=niche, top_n=15)
        trending_phrases = [k["keyword"] for k in tkw.get("keywords", [])]
    signals.append(MD.tag_overlap(tags, trending_phrases))

    near_dup = _near_duplicates(title, description, niche=niche, channel_id=channel_id)

    return {
        "signals": signals,
        "structuralPatterns": structural,
        "keyPhrase": key_phrase,
        "nearDuplicates": near_dup,
        "sample": {"videosAnalysed": len(rows), "outliersInSample": len(outlier_rows),
                  "period": period, "niche": niche, "channelId": channel_id},
        "summary": MD.summarize(signals),
    }


# --------------------------------------------------------------------- drafts

def _j(x):
    try:
        return json.loads(x) if x else None
    except (TypeError, ValueError):
        return None


def _shape_draft(r) -> dict:
    return {"id": r["id"], "videoId": r["video_id"], "title": r["title"],
           "description": r["description"], "tags": _j(r["tags"]) or [],
           "niche": r["niche"], "channelId": r["channel_id"], "isShort": bool(r["is_short"]),
           "review": _j(r["review"]), "createdAt": r["created_at"],
           "publishedAt": r["published_at"]}


def save_draft(title: str, description: str = "", tags=None, niche: str = None,
              channel_id: str = None, is_short: bool = False, review: dict = None) -> dict:
    conn = db.get_conn()
    now = db.now_iso()
    row = conn.execute(
        "INSERT INTO drafts (title, description, tags, niche, channel_id, is_short, review, "
        "created_at) VALUES (?,?,?,?,?,?,?,?) RETURNING id",
        (title, description, json.dumps(tags or [], ensure_ascii=False), niche, channel_id,
         1 if is_short else 0,
         json.dumps(review, ensure_ascii=False) if review else None, now),
    ).fetchone()
    conn.commit()
    conn.close()
    return {"id": row["id"], "createdAt": now}


def list_drafts(channel_id: str = None, unpublished_only: bool = False,
                limit: int = 100) -> list:
    where, params = [], []
    if channel_id:
        where.append("channel_id = ?")
        params.append(channel_id)
    if unpublished_only:
        where.append("video_id IS NULL")
    sql = "SELECT * FROM drafts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    conn = db.get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_shape_draft(r) for r in rows]


def link_draft(draft_id: int, video_id: str) -> dict:
    """Call once the draft has actually been published, so draft_outcomes()
    can compare prediction to reality later."""
    conn = db.get_conn()
    conn.execute("UPDATE drafts SET video_id=?, published_at=? WHERE id=?",
                (video_id, db.now_iso(), draft_id))
    conn.commit()
    conn.close()
    return {"id": draft_id, "videoId": video_id}


def draft_outcomes(min_age_days: float = 7.0) -> list:
    """For linked drafts old enough to have real numbers, surface what the
    review predicted next to what actually happened -- the only honest way
    to learn whether these signals are worth anything for THIS channel and
    niche (docs/plan-iteration-8.md, 'the feedback loop'). This function does
    not itself decide the reviews were right or wrong; it hands both numbers
    to whoever's asking so they can judge."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT d.*, v.view_count, v.published_at AS video_published_at "
        "FROM drafts d JOIN videos v ON v.video_id = d.video_id "
        "WHERE d.video_id IS NOT NULL").fetchall()
    conn.close()
    out = []
    for r in rows:
        pub = r["video_published_at"]
        if not pub:
            continue
        age = P.days_since(pub)
        if age < min_age_days:
            continue
        review = _j(r["review"]) or {}
        out.append({
            "draftId": r["id"], "videoId": r["video_id"], "title": r["title"],
            "ageDays": round(age, 1), "views": r["view_count"],
            "summaryAtReviewTime": review.get("summary"),
            "keyPhraseAtReviewTime": review.get("keyPhrase"),
        })
    return out

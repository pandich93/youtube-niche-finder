"""Stage 19: a queue for manually-pasted YouTube transcripts, chunked and
embedded locally (fastembed, zero network cost beyond the one-time model
download), searchable by a hybrid of vector cosine and Postgres full-text
(RRF merge). Transcripts are never fetched automatically -- the user copies
the text off YouTube's own transcript panel and pastes it in; see
PRIVACY.md.
"""
import infrastructure.postgres as db
from domain import transcripts as TR

_EMB = None


def _embeddings():
    global _EMB
    if _EMB is None:
        import infrastructure.embeddings.fastembed_provider as emb
        _EMB = emb
    return _EMB


def request_transcript(video_id: str, reason: str = None, compare_group: str = None,
                       requested_by: str = None) -> dict:
    """Queue a video for manual transcript paste -- shows up in
    list_transcript_queue(status='pending') until someone calls
    save_transcript() for it."""
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO transcript_requests (video_id, reason, compare_group, requested_by, "
        "status, created_at) VALUES (?,?,?,?,'pending',?) "
        "ON CONFLICT (video_id) DO UPDATE SET "
        "reason=COALESCE(excluded.reason, transcript_requests.reason), "
        "compare_group=COALESCE(excluded.compare_group, transcript_requests.compare_group)",
        (video_id, reason, compare_group, requested_by, db.now_iso()))
    conn.commit()
    conn.close()
    return {"videoId": video_id, "status": "pending"}


def list_transcript_queue(status: str = None) -> list:
    conn = db.get_conn()
    if status:
        rows = conn.execute(
            "SELECT tr.*, v.title, v.view_count, v.channel_id, c.title AS channel_title "
            "FROM transcript_requests tr "
            "LEFT JOIN videos v ON v.video_id = tr.video_id "
            "LEFT JOIN channels c ON c.channel_id = v.channel_id "
            "WHERE tr.status = ? ORDER BY tr.created_at DESC", (status,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT tr.*, v.title, v.view_count, v.channel_id, c.title AS channel_title "
            "FROM transcript_requests tr "
            "LEFT JOIN videos v ON v.video_id = tr.video_id "
            "LEFT JOIN channels c ON c.channel_id = v.channel_id "
            "ORDER BY tr.created_at DESC").fetchall()
    conn.close()
    return [{
        "videoId": r["video_id"], "status": r["status"], "reason": r["reason"],
        "compareGroup": r["compare_group"], "requestedBy": r["requested_by"],
        "error": r["error"], "createdAt": r["created_at"],
        "title": r["title"], "views": r["view_count"], "channelId": r["channel_id"],
        "channelTitle": r["channel_title"],
    } for r in rows]


def save_transcript(video_id: str, raw_text: str, language: str = None) -> dict:
    """Parse -> chunk -> embed -> store, all synchronous (fastembed is
    local, no network). Any failure writes status='error' with the message
    instead of raising, so a bad paste shows up in the queue with a
    "Повторить" button rather than a 500."""
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO transcript_requests (video_id, status, created_at) "
        "VALUES (?, 'pending', ?) ON CONFLICT (video_id) DO NOTHING",
        (video_id, db.now_iso()))
    conn.commit()
    conn.close()

    try:
        parsed = TR.parse_transcript(raw_text)
        if not parsed["segments"]:
            raise ValueError("empty transcript after parsing -- paste didn't contain any text")
        chunks = TR.chunk_segments(parsed["segments"])
        emb = _embeddings()
        # embed() always returns a list when given a list, regardless of
        # length -- no singleton-unwrapping needed (unlike emb.embed(str),
        # used below in search_transcripts for one query string).
        vecs = emb.embed([c["text"] for c in chunks])

        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO transcripts (video_id, language, text, has_timestamps, "
                "word_count, created_at) VALUES (?,?,?,?,?,?) ON CONFLICT (video_id) DO UPDATE "
                "SET language=excluded.language, text=excluded.text, "
                "has_timestamps=excluded.has_timestamps, word_count=excluded.word_count, "
                "created_at=excluded.created_at",
                (video_id, language, raw_text, 1 if parsed["hasTimestamps"] else 0,
                 parsed["wordCount"], db.now_iso()))
            conn.execute("DELETE FROM transcript_chunks WHERE video_id = ?", (video_id,))
            for idx, (c, vec) in enumerate(zip(chunks, vecs)):
                conn.execute(
                    "INSERT INTO transcript_chunks (video_id, idx, start_sec, text, embedding) "
                    "VALUES (?,?,?,?,?)",
                    (video_id, idx, c["startSec"], c["text"], emb.to_blob(vec)))
            conn.execute(
                "UPDATE transcript_requests SET status='ready', error=NULL WHERE video_id=?",
                (video_id,))
            conn.commit()
        finally:
            conn.close()
        return {"videoId": video_id, "status": "ready", "chunks": len(chunks),
               "wordCount": parsed["wordCount"], "hasTimestamps": parsed["hasTimestamps"]}
    except Exception as e:
        conn = db.get_conn()
        try:
            conn.execute(
                "UPDATE transcript_requests SET status='error', error=? WHERE video_id=?",
                (str(e), video_id))
            conn.commit()
        finally:
            conn.close()
        return {"videoId": video_id, "status": "error", "error": str(e)}


def reindex_transcript(video_id: str) -> dict:
    """Re-chunk + re-embed an already-saved transcript -- for after a chunk
    size or embedding model change. No re-paste needed, the raw text is
    already stored."""
    conn = db.get_conn()
    row = conn.execute("SELECT text, language FROM transcripts WHERE video_id=?",
                       (video_id,)).fetchone()
    conn.close()
    if not row:
        return {"videoId": video_id, "status": "error",
               "error": "no saved transcript for this video yet"}
    return save_transcript(video_id, row["text"], language=row["language"])


def search_transcripts(query: str, niche: str = None, compare_group: str = None,
                       k: int = 10) -> dict:
    """Hybrid search: vector cosine (fastembed, local) + Postgres full-text
    (tsvector/websearch_to_tsquery, 'simple' config for multilingual text),
    merged by Reciprocal Rank Fusion (RRF, k=60 -- the standard constant,
    not tuned on this corpus)."""
    if not query or not query.strip():
        raise ValueError("query is required")

    conn = db.get_conn()
    try:
        video_ids = None
        if niche:
            video_ids = {r["video_id"] for r in conn.execute(
                "SELECT DISTINCT vn.video_id FROM video_niches vn "
                "JOIN transcript_chunks tc ON tc.video_id = vn.video_id "
                "WHERE vn.niche_slug = ?", (niche,)).fetchall()}
        if compare_group:
            cg_ids = {r["video_id"] for r in conn.execute(
                "SELECT video_id FROM transcript_requests WHERE compare_group = ?",
                (compare_group,)).fetchall()}
            video_ids = cg_ids if video_ids is None else (video_ids & cg_ids)

        where_sql, params = "", []
        if video_ids is not None:
            if not video_ids:
                return {"query": query, "results": [],
                       "hint": "no transcripts match the niche/compare_group filter"}
            where_sql = " WHERE video_id IN (%s)" % ",".join("?" * len(video_ids))
            params = list(video_ids)

        rows = conn.execute(
            f"SELECT video_id, idx, start_sec, text, embedding FROM transcript_chunks{where_sql}",
            params).fetchall()
        if not rows:
            return {"query": query, "results": [], "hint": "no transcripts saved yet"}

        emb = _embeddings()
        q_vec = emb.embed(query)
        vec_scored = sorted(
            ((r, emb.cosine(q_vec, emb.from_blob(r["embedding"]))) for r in rows),
            key=lambda t: t[1], reverse=True)
        vec_rank = {(r["video_id"], r["idx"]): i for i, (r, _) in enumerate(vec_scored)}
        by_key = {(r["video_id"], r["idx"]): r for r, _ in vec_scored}

        ft_where = where_sql + (" AND " if where_sql else " WHERE ") + \
            "to_tsvector('simple', text) @@ websearch_to_tsquery('simple', ?)"
        ft_rows = conn.execute(
            f"SELECT video_id, idx FROM transcript_chunks{ft_where} "
            f"ORDER BY ts_rank(to_tsvector('simple', text), "
            f"websearch_to_tsquery('simple', ?)) DESC LIMIT 100",
            params + [query, query]).fetchall()
        ft_rank = {(r["video_id"], r["idx"]): i for i, r in enumerate(ft_rows)}
    finally:
        conn.close()

    rrf_k = 60
    rrf = {}
    for key, rank in vec_rank.items():
        rrf[key] = rrf.get(key, 0.0) + 1.0 / (rrf_k + rank + 1)
    for key, rank in ft_rank.items():
        rrf[key] = rrf.get(key, 0.0) + 1.0 / (rrf_k + rank + 1)

    top = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:k]
    top_video_ids = list({key[0] for key, _ in top})
    meta = {}
    if top_video_ids:
        conn = db.get_conn()
        try:
            placeholders = ",".join("?" * len(top_video_ids))
            for r in conn.execute(
                f"SELECT v.video_id, v.title, v.view_count, v.channel_id, "
                f"c.title AS channel_title FROM videos v "
                f"JOIN channels c ON c.channel_id = v.channel_id "
                f"WHERE v.video_id IN ({placeholders})", top_video_ids).fetchall():
                meta[r["video_id"]] = dict(r)
        finally:
            conn.close()

    results = []
    for (video_id, idx), score in top:
        r = by_key.get((video_id, idx))
        m = meta.get(video_id, {})
        start_sec = r["start_sec"] if r else None
        results.append({
            "videoId": video_id, "chunkIndex": idx, "text": r["text"] if r else None,
            "startSec": start_sec,
            "url": f"https://youtu.be/{video_id}" + (f"?t={start_sec}" if start_sec is not None else ""),
            "title": m.get("title"), "channelTitle": m.get("channel_title"),
            "views": m.get("view_count"), "rrfScore": round(score, 5),
        })
    return {"query": query, "results": results}

"""Topic alerts (plan 19): a user names a topic in plain words, and any video
first seen in the last WINDOW_HOURS -- from the RSS watch, a niche
collection, the trending chart, any source -- whose title+description
embedding is close enough raises a personal `topic_match` event. Only the
topic's owner sees it and gets it (events.user_id), whatever their watchlist.

Zero quota: it compares embeddings already in the database. A video waits
for its embedding (the worker's hourly backfill), which is why each run looks
back WINDOW_HOURS instead of only at the last cycle; (topic, video) pairs
never repeat. Videos seen before the topic was created never match it, so a
new topic does not flood the feed with the backlog.

What "new" covers is what the database collects: the tracked channels' RSS,
WORKER_QUERIES and anything collected by hand -- not all of YouTube.
"""
from datetime import datetime, timedelta, timezone

import numpy as np

import infrastructure.postgres as db
from application import alerts as AL
from domain.users import LOCAL_USER_ID

WINDOW_HOURS = 48
DEFAULT_THRESHOLD = 0.6
MAX_TOPICS_PER_USER = 50
NOTE = ("Matches videos the database collects (tracked channels' RSS, niche collections, "
        "trending), not all of YouTube.")


def _embed(text: str) -> np.ndarray:
    import infrastructure.embeddings.fastembed_provider as emb
    return emb.embed(text)


def _vec(blob) -> np.ndarray:
    return np.frombuffer(bytes(blob), dtype=np.float32)


def _shape(r) -> dict:
    return {"id": r["id"], "text": r["text"], "threshold": r["threshold"],
            "createdAt": r["created_at"], "paused": bool(r["paused"])}


def add_topic(text: str, threshold: float = DEFAULT_THRESHOLD,
              user_id: int = LOCAL_USER_ID) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("topic text is empty")
    if len(text) > 300:
        raise ValueError("topic text is longer than 300 characters")
    if not 0 < float(threshold) < 1:
        raise ValueError("threshold must be between 0 and 1")
    vec = np.asarray(_embed(text), dtype=np.float32)
    conn = db.get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM user_topics WHERE user_id = ?",
                         (user_id,)).fetchone()[0]
        if n >= MAX_TOPICS_PER_USER:
            raise ValueError(f"at most {MAX_TOPICS_PER_USER} topics per user")
        row = conn.execute(
            "INSERT INTO user_topics (user_id, text, embedding, threshold, created_at, paused) "
            "VALUES (?,?,?,?,?,0) RETURNING id, text, threshold, created_at, paused",
            (user_id, text, vec.tobytes(), float(threshold), db.now_iso())).fetchone()
        conn.commit()
        return _shape(row)
    finally:
        conn.close()


def list_topics(user_id: int = LOCAL_USER_ID) -> list:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT id, text, threshold, created_at, paused FROM user_topics "
                            "WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
        return [_shape(r) for r in rows]
    finally:
        conn.close()


def set_paused(topic_id: int, paused: bool, user_id: int = LOCAL_USER_ID) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute("UPDATE user_topics SET paused = ? WHERE id = ? AND user_id = ? "
                           "RETURNING id, text, threshold, created_at, paused",
                           (1 if paused else 0, int(topic_id), user_id)).fetchone()
        conn.commit()
        if not row:
            raise ValueError("no such topic")
        return _shape(row)
    finally:
        conn.close()


def remove_topic(topic_id: int, user_id: int = LOCAL_USER_ID) -> dict:
    """Removes the topic only; the events it raised stay in the feed."""
    conn = db.get_conn()
    try:
        cur = conn.execute("DELETE FROM user_topics WHERE id = ? AND user_id = ? RETURNING id",
                           (int(topic_id), user_id)).fetchone()
        conn.commit()
        return {"removed": cur is not None}
    finally:
        conn.close()


def match_new(window_hours: int = WINDOW_HOURS) -> dict:
    """Compare every active topic with the videos first seen in the last
    `window_hours` that have an embedding; emit one topic_match per close
    (topic, video) pair. Safe to run every cycle."""
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    conn = db.get_conn()
    try:
        topics = conn.execute("SELECT id, user_id, text, embedding, threshold, created_at "
                              "FROM user_topics WHERE paused = 0 AND embedding IS NOT NULL"
                              ).fetchall()
        if not topics:
            return {"topics": 0, "videosChecked": 0, "matched": 0}
        videos = conn.execute(
            "SELECT v.video_id, v.channel_id, v.title, v.first_seen_at, v.embedding, "
            "c.title AS channel_title FROM videos v LEFT JOIN channels c "
            "ON c.channel_id = v.channel_id "
            "WHERE v.first_seen_at >= ? AND v.embedding IS NOT NULL", (since,)).fetchall()
        matched = 0
        if videos:
            mat = np.stack([_vec(v["embedding"]) for v in videos])
            mat = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
            for t in topics:
                q = _vec(t["embedding"])
                q = q / max(float(np.linalg.norm(q)), 1e-12)
                sims = mat @ q
                for v, s in zip(videos, sims):
                    if s < t["threshold"] or (v["first_seen_at"] or "") < (t["created_at"] or ""):
                        continue
                    payload = {"topicId": t["id"], "topic": t["text"],
                               "similarity": round(float(s), 3), "videoId": v["video_id"],
                               "channelId": v["channel_id"], "title": v["title"],
                               "channelTitle": v["channel_title"]}
                    if AL._emit(conn, "topic_match", f"{t['id']}:{v['video_id']}", payload,
                                user_id=t["user_id"]):
                        matched += 1
        conn.commit()
        return {"topics": len(topics), "videosChecked": len(videos), "matched": matched}
    finally:
        conn.close()

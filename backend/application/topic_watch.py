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
WORKER_QUERIES and anything collected by hand -- not all of YouTube. A topic
can opt into search_youtube: once a day the worker runs one search.list call
for it (the last 24 hours, newest first) and stores what it finds, so the
next match_new sees it. search.list is 100 calls a day for the whole
installation, hence SEARCH_MAX_PER_DAY topics a day at most; in multi-user
mode the call counts against the topic owner's share.
"""
from datetime import datetime, timedelta, timezone

import numpy as np

import infrastructure.postgres as db
from application import alerts as AL
from domain.users import LOCAL_USER_ID

WINDOW_HOURS = 48
SEARCH_MAX_PER_DAY = 5         # topics searched on YouTube per day, all users
SEARCH_EVERY_HOURS = 20        # one search a day per topic, with slack for the worker's timing
SEARCH_RESULTS = 25
DEFAULT_THRESHOLD = 0.6
MAX_TOPICS_PER_USER = 50
NOTE = ("Matches videos the database collects (tracked channels' RSS, niche collections, "
        "trending), not all of YouTube.")


def _embed(text: str) -> np.ndarray:
    import infrastructure.embeddings.fastembed_provider as emb
    return emb.embed(text)


def _vec(blob) -> np.ndarray:
    return np.frombuffer(bytes(blob), dtype=np.float32)


_COLS = "id, text, threshold, created_at, paused, search_youtube, last_searched_at"


def _shape(r) -> dict:
    return {"id": r["id"], "text": r["text"], "threshold": r["threshold"],
            "createdAt": r["created_at"], "paused": bool(r["paused"]),
            "searchYoutube": bool(r["search_youtube"]), "lastSearchedAt": r["last_searched_at"]}


def add_topic(text: str, threshold: float = DEFAULT_THRESHOLD,
              user_id: int = LOCAL_USER_ID, search_youtube: bool = False) -> dict:
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
            "INSERT INTO user_topics (user_id, text, embedding, threshold, created_at, paused, "
            f"search_youtube) VALUES (?,?,?,?,?,0,?) RETURNING {_COLS}",
            (user_id, text, vec.tobytes(), float(threshold), db.now_iso(),
             1 if search_youtube else 0)).fetchone()
        conn.commit()
        return _shape(row)
    finally:
        conn.close()


def list_topics(user_id: int = LOCAL_USER_ID) -> list:
    conn = db.get_conn()
    try:
        rows = conn.execute(f"SELECT {_COLS} FROM user_topics "
                            "WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
        return [_shape(r) for r in rows]
    finally:
        conn.close()


def set_paused(topic_id: int, paused: bool, user_id: int = LOCAL_USER_ID) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute("UPDATE user_topics SET paused = ? WHERE id = ? AND user_id = ? "
                           f"RETURNING {_COLS}",
                           (1 if paused else 0, int(topic_id), user_id)).fetchone()
        conn.commit()
        if not row:
            raise ValueError("no such topic")
        return _shape(row)
    finally:
        conn.close()


def set_search(topic_id: int, on: bool, user_id: int = LOCAL_USER_ID) -> dict:
    """Turn the daily YouTube search of a topic on or off."""
    conn = db.get_conn()
    try:
        row = conn.execute("UPDATE user_topics SET search_youtube = ? WHERE id = ? AND user_id = ? "
                           f"RETURNING {_COLS}", (1 if on else 0, int(topic_id), user_id)).fetchone()
        conn.commit()
        if not row:
            raise ValueError("no such topic")
        return _shape(row)
    finally:
        conn.close()


def search_topics(api_key: str) -> dict:
    """The worker's daily step: one search.list call per opted-in topic not
    searched for SEARCH_EVERY_HOURS, oldest first, at most SEARCH_MAX_PER_DAY.
    New videos are stored with embeddings and no niche; match_new does the
    rest. Stops quietly when the day's search calls run out."""
    import infrastructure.youtube.client as yt
    from application import collecting as C
    from domain import periods as PD
    from domain.users import multi_user_enabled
    from infrastructure import quota_owner

    out = {"topicsSearched": 0, "videosStored": 0, "searchCalls": 0, "stoppedBy": None}
    if SEARCH_MAX_PER_DAY <= 0:
        return out
    since = (datetime.now(timezone.utc) - timedelta(hours=SEARCH_EVERY_HOURS)).isoformat()
    conn = db.get_conn()
    try:
        topics = conn.execute(
            "SELECT id, user_id, text FROM user_topics WHERE search_youtube = 1 AND paused = 0 "
            "AND (last_searched_at IS NULL OR last_searched_at < ?) "
            "ORDER BY last_searched_at ASC NULLS FIRST, id LIMIT ?",
            (since, SEARCH_MAX_PER_DAY)).fetchall()
        published_after = PD.to_rfc3339(datetime.now(timezone.utc) - timedelta(hours=24))
        for t in topics:
            if C.search_calls_today(conn) >= yt.SEARCH_DAILY_CALL_LIMIT:
                out["stoppedBy"] = "search-quota"
                break
            owner = quota_owner.set_owner(t["user_id"]) if multi_user_enabled() else None
            try:
                resp = yt.search_videos(api_key, t["text"], published_after=published_after,
                                        order="date", max_results=SEARCH_RESULTS)
                C._record_search_calls(conn, 1)
                out["searchCalls"] += 1
                ids = list(dict.fromkeys((i.get("id") or {}).get("videoId")
                                         for i in resp.get("items", [])))
                ids = [i for i in ids if i]
                known = {r["video_id"] for r in conn.execute(
                    "SELECT video_id FROM videos WHERE video_id IN (%s)" % ",".join("?" * len(ids)),
                    ids).fetchall()} if ids else set()
                new = [i for i in ids if i not in known]
                if new:
                    items = yt.videos_list(api_key, new)
                    chans = list({(v.get("snippet") or {}).get("channelId") for v in items} - {None})
                    now = db.now_iso()
                    C.store_channels(conn, yt.channels_list(api_key, chans) if chans else [], now)
                    out["videosStored"] += C.store_videos(conn, items, embed=True, now=now)
            except yt.QuotaExceeded:
                out["stoppedBy"] = "quota"
                conn.commit()
                break
            finally:
                if owner is not None:
                    quota_owner.reset(owner)
            conn.execute("UPDATE user_topics SET last_searched_at = ? WHERE id = ?",
                         (db.now_iso(), t["id"]))
            conn.commit()
            out["topicsSearched"] += 1
    finally:
        conn.close()
    return out


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

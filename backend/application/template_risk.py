"""Template risk (plan 01): how much a channel's recent uploads look like one
template repeated, per channel and summarised per niche. Reads only what is
already in the database -- zero YouTube quota, no LLM. The maths lives in
domain/template_risk.py; this module loads the rows and shapes the answer.

Shorts and long-form are never mixed: a Shorts feed is templated by nature
and would drag a channel's long-form score up. Long-form is analysed when the
channel has enough of it, otherwise everything it uploaded recently.
"""
import numpy as np

import infrastructure.postgres as db
from domain import metrics as M
from domain import template_risk as TR

DEFAULT_LAST_N = 30
MAX_NICHE_CHANNELS = 300
TOP_N = 10


def _load_videos(conn, channel_id: str, last_n: int) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT title, duration_seconds, published_at, embedding FROM videos "
        "WHERE channel_id = ? ORDER BY published_at DESC LIMIT ?",
        (channel_id, last_n)).fetchall()]


def _score(videos: list) -> dict:
    longform = [v for v in videos if not M.is_short(v["duration_seconds"])]
    chosen, fmt = (longform, "long-form") if len(longform) >= TR.MIN_VIDEOS else (videos, "all")
    vectors = [np.frombuffer(bytes(v["embedding"]), dtype=np.float32)
               for v in chosen if v["embedding"] is not None]
    signals = {
        "similarity": TR.title_self_similarity(vectors),
        "templateShare": TR.title_template_share([v["title"] for v in chosen]),
        "durationCv": TR.duration_uniformity([v["duration_seconds"] for v in chosen]),
        "cadenceCv": TR.cadence_regularity([v["published_at"] for v in chosen]),
        "videos": len(chosen),
    }
    return {"videosAnalysed": len(chosen), "format": fmt,
            "embeddedVideos": len(vectors), "signals": signals,
            **TR.template_risk_score(signals)}


def template_risk(channel_id: str, last_n: int = DEFAULT_LAST_N) -> dict:
    """Template-risk score of one channel from its `last_n` most recent
    uploads: score 0-100, level low/medium/high (or insufficient-data with
    fewer than MIN_VIDEOS videos), the four signals behind it and the
    reasons that pushed it up. A heuristic, not YouTube's verdict."""
    conn = db.get_conn()
    try:
        ch = conn.execute("SELECT channel_id, title, subscriber_count FROM channels "
                          "WHERE channel_id = ?", (channel_id,)).fetchone()
        if not ch:
            return {"channelId": channel_id, "found": False,
                    "hint": "Run collect_channel / track_channel first."}
        result = _score(_load_videos(conn, channel_id, last_n))
    finally:
        conn.close()
    return {"channelId": channel_id, "found": True, "channelTitle": ch["title"],
            "subscribers": ch["subscriber_count"], "quotaUsed": 0, **result}


def niche_template_risk(niche: str, last_n: int = DEFAULT_LAST_N, top_n: int = TOP_N) -> dict:
    """The same score for every channel that has videos in a niche: how many
    are low / medium / high risk, the share of high-risk channels among the
    scored ones, and the most templated channels. A niche where most
    channels are high risk is one where the copycats are about to be swept."""
    conn = db.get_conn()
    try:
        ids = [r["channel_id"] for r in conn.execute(
            "SELECT DISTINCT v.channel_id FROM video_niches vn "
            "JOIN videos v ON v.video_id = vn.video_id WHERE vn.niche_slug = ? "
            "LIMIT ?", (niche, MAX_NICHE_CHANNELS)).fetchall()]
        if not ids:
            return {"niche": niche, "found": False,
                    "hint": "No collected videos in this niche -- run collect_niche first."}
        titles = {}
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            for r in conn.execute(
                    "SELECT channel_id, title, subscriber_count FROM channels WHERE channel_id IN (%s)"
                    % ",".join("?" * len(chunk)), chunk).fetchall():
                titles[r["channel_id"]] = r
        levels = {"low": 0, "medium": 0, "high": 0}
        insufficient, scored = 0, []
        for cid in ids:
            res = _score(_load_videos(conn, cid, last_n))
            if res["level"] == "insufficient-data":
                insufficient += 1
                continue
            levels[res["level"]] += 1
            ch = titles.get(cid)
            scored.append({"channelId": cid, "title": ch["title"] if ch else None,
                           "subscribers": ch["subscriber_count"] if ch else None,
                           "score": res["score"], "level": res["level"],
                           "reasons": res["reasons"], "videosAnalysed": res["videosAnalysed"]})
    finally:
        conn.close()
    scored.sort(key=lambda c: -c["score"])
    return {"niche": niche, "found": True, "channelsAnalysed": len(scored),
            "channelsInsufficient": insufficient, "levels": levels,
            "highRiskSharePercent": (round(levels["high"] / len(scored) * 100, 1)
                                     if scored else None),
            "mostTemplated": scored[:top_n], "quotaUsed": 0, "note": TR.DISCLAIMER}

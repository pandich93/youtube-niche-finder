"""Signals for YouTube's three "inauthentic content" categories, per channel
and per niche (plan 22) -- the rules are in domain/policy_signals.py.
Reads only the database: titles, YouTube's topic names, the creator's
synthetic-media disclosure, thumbnail vectors and the AI "faceless" label.
Zero quota, no LLM call.
"""
import json

import numpy as np

import infrastructure.postgres as db
from application import template_risk as TRA
from domain import policy_signals as P

LAST_N = 30
MAX_NICHE_CHANNELS = 150
TOP_N = 5


def _list(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        out = json.loads(raw)
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


def _faceless(raw):
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    v = (raw or {}).get("is_faceless")
    return v if isinstance(v, bool) else None


def _thumb_similarity(blobs) -> float | None:
    vecs = [np.frombuffer(bytes(b), dtype=np.float32) for b in blobs if b is not None]
    if len(vecs) < P.MIN_TITLES:
        return None
    m = np.stack(vecs)
    m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
    sims = m @ m.T
    n = len(vecs)
    return round(float((sims.sum() - n) / (n * (n - 1))), 3)


def _for(conn, channel_id: str, template: dict) -> dict:
    rows = conn.execute(
        "SELECT title, topic_categories, contains_synthetic_media, thumb_embedding FROM videos "
        "WHERE channel_id = ? ORDER BY published_at DESC LIMIT ?", (channel_id, LAST_N)).fetchall()
    ch = conn.execute("SELECT llm_labels FROM channels WHERE channel_id = ?",
                      (channel_id,)).fetchone()
    videos = [{"title": r["title"], "topicCategories": _list(r["topic_categories"]),
               "containsSyntheticMedia": r["contains_synthetic_media"]} for r in rows]
    return P.signals(template=template,
                     thumb_similarity=_thumb_similarity([r["thumb_embedding"] for r in rows]),
                     videos=videos, faceless=_faceless(ch["llm_labels"] if ch else None))


def channel_policy_signals(channel_id: str) -> dict:
    template = TRA.template_risk(channel_id)
    if not template.get("found"):
        return {"channelId": channel_id, "found": False, "hint": template.get("hint")}
    conn = db.get_conn()
    try:
        out = _for(conn, channel_id, template)
    finally:
        conn.close()
    return {"channelId": channel_id, "found": True, "channelTitle": template.get("channelTitle"),
            "videosAnalysed": template.get("videosAnalysed"), **out, "quotaUsed": 0}


def niche_policy_signals(niche: str) -> dict:
    """How many channels of a niche show each category's signals -- whether a
    faceless format here is one YouTube's reviewers are likely to look at."""
    conn = db.get_conn()
    try:
        ids = [r["channel_id"] for r in conn.execute(
            "SELECT DISTINCT v.channel_id FROM video_niches vn "
            "JOIN videos v ON v.video_id = vn.video_id WHERE vn.niche_slug = ? LIMIT ?",
            (niche, MAX_NICHE_CHANNELS)).fetchall()]
    finally:
        conn.close()
    if not ids:
        return {"niche": niche, "found": False,
                "hint": "No collected videos in this niche -- run collect_niche first."}
    counts = {k: {"high": 0, "watch": 0, "none": 0, "insufficient-data": 0}
              for k in P.POLICY}
    flagged = {k: [] for k in P.POLICY}
    conn = db.get_conn()
    try:
        titles = {r["channel_id"]: r["title"] for r in conn.execute(
            "SELECT channel_id, title FROM channels WHERE channel_id IN (%s)"
            % ",".join("?" * len(ids)), ids).fetchall()}
        for cid in ids:
            res = _for(conn, cid, TRA.template_risk(cid))
            for key, cat in res["categories"].items():
                counts[key][cat["level"]] += 1
                if cat["level"] in ("high", "watch"):
                    flagged[key].append({"channelId": cid, "channelTitle": titles.get(cid),
                                         "level": cat["level"], "reasons": cat["reasons"]})
    finally:
        conn.close()
    out = {}
    for key in P.POLICY:
        scored = len(ids) - counts[key]["insufficient-data"]
        flagged[key].sort(key=lambda x: x["level"] != "high")
        out[key] = {**counts[key], "channelsScored": scored,
                    "shareFlagged": (round((counts[key]["high"] + counts[key]["watch"]) / scored, 2)
                                     if scored else None),
                    "channels": flagged[key][:TOP_N], "policy": P.POLICY[key],
                    "policyUrl": P.POLICY_URL}
    return {"niche": niche, "found": True, "channels": len(ids), "categories": out,
            "note": "Signals visible from public data, not YouTube's decision.", "quotaUsed": 0}

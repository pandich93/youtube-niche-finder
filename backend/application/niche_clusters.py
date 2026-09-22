"""Stage 08: cluster channels by their videos' average embedding (k-means,
domain/niche_clusters.py) into informal "niches" nobody had to define by
hand, then name each cluster via LLM (or a tags-based fallback) and compute
opportunity metrics -- median outlier score, total view velocity, faceless
share (stage 03), and a competition count (channels over 100k subs).
Recomputed wholesale, not incrementally: k-means cluster labels aren't
stable identities across reruns, so there is no meaningful "diff" between
two runs' cluster sets.
"""
import json
import os

import infrastructure.postgres as db
from application import discovery as trends
from application import llm_gateway as gw
from domain import niche_clusters as NC
from infrastructure.llm import factory

MIN_CHANNELS = int(os.environ.get("NICHE_CLUSTERS_MIN_CHANNELS", "10"))
COMPETITION_SUBS = int(os.environ.get("NICHE_CLUSTERS_COMPETITION_SUBS", "100000"))

CLUSTER_NAME_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "audience": {"type": "string"},
    },
    "required": ["name", "description", "audience"],
}

_CLUSTER_NAME_SYSTEM = (
    "You name a cluster of YouTube channels for a niche-research dashboard, "
    "given up to 15 of their videos' titles (closest to the cluster's "
    "centroid) and the cluster's most common curated tags. Output a short "
    "name (2-5 words), a one-sentence description of what unites these "
    "channels, and a short description of the likely audience. Base this "
    "only on the titles/tags given -- don't invent a niche the titles don't "
    "support."
)


def _embeddings():
    import infrastructure.embeddings.fastembed_provider as emb
    return emb


def _channel_centroids(conn):
    """channel_id -> mean of its videos' embeddings, for every channel with
    at least one embedded video."""
    emb = _embeddings()
    rows = conn.execute(
        "SELECT channel_id, embedding FROM videos WHERE embedding IS NOT NULL").fetchall()
    by_channel = {}
    for r in rows:
        by_channel.setdefault(r["channel_id"], []).append(emb.from_blob(r["embedding"]))
    import numpy as np
    return {cid: np.mean(vecs, axis=0) for cid, vecs in by_channel.items()}


def _fallback_name(tags: list) -> dict:
    top = [t for t, _ in tags[:3]]
    name = " / ".join(top) if top else "Untitled cluster"
    return {"name": name, "description": f"Channels tagged {', '.join(top)}" if top else "",
           "audience": ""}


def compute_clusters(k: int = None) -> dict:
    conn = db.get_conn()
    try:
        centroids_by_channel = _channel_centroids(conn)
        channel_ids = list(centroids_by_channel)
        if len(channel_ids) < MIN_CHANNELS:
            return {"clusters": [], "channelsConsidered": len(channel_ids),
                   "hint": f"need at least {MIN_CHANNELS} channels with embedded videos "
                           f"(backfill_embeddings helps) -- have {len(channel_ids)}"}

        import numpy as np
        matrix = np.array([centroids_by_channel[cid] for cid in channel_ids])
        k = k or NC.choose_k(len(channel_ids))
        clustered = NC.kmeans(matrix, k=k)
        labels = clustered["labels"]

        groups = {}
        for cid, label in zip(channel_ids, labels):
            groups.setdefault(label, []).append(cid)

        rows = conn.execute(
            "SELECT channel_id, subscriber_count, llm_labels FROM channels "
            "WHERE channel_id = ANY(?)", (channel_ids,)).fetchall()
        channel_meta = {r["channel_id"]: dict(r) for r in rows}

        conn.execute("DELETE FROM niche_clusters")
        model = factory.default_model() or "auto"
        out_clusters = []
        for label, cids in groups.items():
            metrics = _cluster_metrics(cids, channel_meta)
            video_rows = trends.load_window(period="all", channel_ids=cids)
            top_titles = sorted(video_rows, key=lambda r: r.get("view_count") or 0,
                                reverse=True)[:15]
            titles = [r["title"] for r in top_titles if r.get("title")]
            tag_counts = _top_tags(conn, cids)

            naming = None
            if titles:
                user_input = "Titles:\n" + "\n".join(f"- {t}" for t in titles)
                if tag_counts:
                    user_input += "\nTop tags: " + ", ".join(t for t, _ in tag_counts[:10])
                naming = gw.run("cluster_name", _CLUSTER_NAME_SYSTEM, user_input,
                                CLUSTER_NAME_SCHEMA)
            if naming is None:
                naming = _fallback_name(tag_counts)

            cluster_id = f"cluster-{label}"
            conn.execute(
                "INSERT INTO niche_clusters (cluster_id, name, description, audience, "
                "channel_count, median_outlier_score, total_velocity, faceless_share, "
                "competition_count, channel_ids, model, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (cluster_id, naming["name"], naming["description"], naming["audience"],
                 len(cids), metrics["medianOutlierScore"], metrics["totalVelocity"],
                 metrics["facelessShare"], metrics["competitionCount"],
                 json.dumps(cids), model, db.now_iso()))
            out_clusters.append({
                "clusterId": cluster_id, **naming, "channelCount": len(cids),
                "channelIds": cids, **metrics,
            })
        conn.commit()
        out_clusters.sort(key=_opportunity_score, reverse=True)
        return {"clusters": out_clusters, "channelsConsidered": len(channel_ids), "k": k}
    finally:
        conn.close()


def _top_tags(conn, channel_ids: list) -> list:
    rows = conn.execute(
        "SELECT vt.tag, COUNT(*) AS n FROM video_tags vt "
        "JOIN videos v ON v.video_id = vt.video_id "
        "WHERE v.channel_id = ANY(?) GROUP BY vt.tag ORDER BY n DESC LIMIT 10",
        (channel_ids,)).fetchall()
    return [(r["tag"], r["n"]) for r in rows]


def _cluster_metrics(channel_ids: list, channel_meta: dict) -> dict:
    rows = trends.load_window(period="all", channel_ids=channel_ids)
    outliers = [r["outlierScore"] for r in rows if r.get("outlierScore") is not None]
    velocities = [r["vph24h"] for r in rows if r.get("vph24h") is not None]
    faceless_flags = []
    competition = 0
    for cid in channel_ids:
        m = channel_meta.get(cid) or {}
        labels = m.get("llm_labels")
        if labels and "is_faceless" in labels:
            faceless_flags.append(bool(labels["is_faceless"]))
        if (m.get("subscriber_count") or 0) > COMPETITION_SUBS:
            competition += 1
    import statistics as st
    return {
        "medianOutlierScore": round(st.median(outliers), 2) if outliers else None,
        "totalVelocity": round(sum(velocities), 1) if velocities else 0.0,
        "facelessShare": round(sum(faceless_flags) / len(faceless_flags), 2)
            if faceless_flags else None,
        "competitionCount": competition,
    }


def _opportunity_score(c: dict) -> float:
    """High outlier + low competition = worth a look. None outlier treated
    as 0 (no signal, not "great") so untested clusters don't rank first by
    accident."""
    outlier = c.get("medianOutlierScore") or 0
    return outlier / (1 + (c.get("competitionCount") or 0))


def niche_map() -> dict:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM niche_clusters ORDER BY created_at DESC").fetchall()
    conn.close()
    clusters = [{
        "clusterId": r["cluster_id"], "name": r["name"], "description": r["description"],
        "audience": r["audience"], "channelCount": r["channel_count"],
        "medianOutlierScore": r["median_outlier_score"], "totalVelocity": r["total_velocity"],
        "facelessShare": r["faceless_share"], "competitionCount": r["competition_count"],
        "channelIds": json.loads(r["channel_ids"]) if r["channel_ids"] else [],
        "createdAt": r["created_at"],
    } for r in rows]
    clusters.sort(key=_opportunity_score, reverse=True)
    return {"clusters": clusters,
           "hint": None if clusters else "no clusters computed yet -- run compute_clusters"}

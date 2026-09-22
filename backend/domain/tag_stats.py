"""Outlier-hit rate per curated tag (theme/trigger/format/...) within one
niche and tag_group -- the data-backed version of "which angle tends to
break out", instead of eyeballing it in chat.

Same hit-rate/lift math as domain/keywords.py's score(): lift is the hit
rate for videos carrying a tag divided by the hit rate across the whole
niche. The population that base rate is measured over (video_rows) is kept
separate from which of those videos carry which tag (tag_rows), so a tag's
lift is never biased by how much of the niche happens to be tagged yet --
an untagged video still counts toward the niche's base rate.
"""
import statistics as st


def compute(video_rows, tag_rows, outlier_threshold: float = 3.0, min_videos: int = 1) -> dict:
    """video_rows: dicts with video_id/views/outlier -- the niche population.
    tag_rows: dicts with video_id/tag -- which of those videos carry which
    tag (already filtered to one tag_group by the caller). A tag_row whose
    video_id isn't in video_rows is ignored (outside the population, e.g. a
    stale tag on a video that moved out of the niche)."""
    population = {r["video_id"]: r for r in video_rows if r.get("video_id")}
    total_videos = len(population)
    hit_video_ids = {vid for vid, r in population.items()
                     if (r.get("outlier") or 0) >= outlier_threshold}
    base_rate = (len(hit_video_ids) / total_videos) if total_videos else 0.0

    by_tag = {}
    for tr in tag_rows:
        vid, tag = tr.get("video_id"), tr.get("tag")
        if vid not in population or not tag:
            continue
        by_tag.setdefault(tag, set()).add(vid)

    out = []
    for tag, video_ids in by_tag.items():
        n = len(video_ids)
        if n < min_videos:
            continue
        hits = len(video_ids & hit_video_ids)
        hit_rate = hits / n if n else 0.0
        lift = round(hit_rate / base_rate, 2) if base_rate > 0 else None
        views = [population[vid].get("views") or 0 for vid in video_ids]
        outliers = [population[vid]["outlier"] for vid in video_ids
                   if population[vid].get("outlier") is not None]
        out.append({
            "tag": tag,
            "videos": n,
            "hits": hits,
            "hitRate": round(hit_rate * 100, 2),
            "lift": lift,
            "medianViews": int(st.median(views)) if views else 0,
            "medianOutlier": round(st.median(outliers), 2) if outliers else None,
        })

    out.sort(key=lambda x: (x["lift"] if x["lift"] is not None else 0, x["videos"]), reverse=True)
    return {
        "tags": out,
        "totalVideos": total_videos,
        "baseRate": round(base_rate * 100, 2),
    }

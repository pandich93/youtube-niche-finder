"""What outliers of a niche have in common (plan 29) -- the rule is in
domain/outlier_traits.py. Reads the videos load_window returns (long-form and
Shorts apart) and compares outliers with ordinary videos. Zero quota, no LLM.
Thumbnail looks are not repeated here: they are the CLIP styles of
thumbnail_styles, which already show each style's median outlier score.
"""
import json

from application import discovery as trends
from domain import outlier_traits as T

NOTE = ("A correlation with outliers, not a cause, over the videos we collected -- an estimate of "
        "niche-finder, not YouTube data. Shorts and long videos are compared apart, times are UTC, "
        "and with a dozen features checked one or two can pass by chance: look at the sample sizes. "
        "Thumbnail looks: see the thumbnail styles of the niche.")


def _tags_count(raw):
    if isinstance(raw, list):
        return len(raw)
    try:
        v = json.loads(raw) if raw else []
        return len(v) if isinstance(v, list) else None
    except (TypeError, ValueError):
        return None


def _video(r) -> dict:
    return T.video(r["title"], r.get("outlierScoreAgeAdjusted") or r.get("outlierScore"),
                   duration=r.get("duration_seconds"), published=r.get("published_at"),
                   tags=_tags_count(r.get("tags")), channel=r.get("channel_id"))


def outlier_traits(niche: str = None, period: str = "all", min_outlier: float = T.OUTLIER_MIN,
                   max_ordinary: float = T.ORDINARY_MAX) -> dict:
    """What the outliers (>= min_outlier x their channel's usual) of a niche
    (or of everything collected) share that ordinary videos (<= max_ordinary)
    do not -- or lack: a number or "?" in the title, the title and video
    length, tags, the weekday and time of publishing. Long videos and Shorts
    are compared apart; a group under 10 videos says "not enough data"."""
    rows = trends.load_window(period=period, niche=niche)
    out = {"niche": niche, "period": period, "minOutlier": min_outlier, "maxOrdinary": max_ordinary,
           "videos": len(rows), "minGroup": T.MIN_GROUP, "minDiffPp": T.MIN_DIFF_PP,
           "minRatio": T.MIN_RATIO, "formats": {}, "note": NOTE}
    if not rows:
        out["hint"] = "nothing collected for this niche and period yet"
    for name, is_short in (("long", False), ("short", True)):
        vids = [_video(r) for r in rows if bool(r.get("isShort")) == is_short]
        res = T.compare(vids, outlier_min=min_outlier, ordinary_max=max_ordinary)
        res["videos"] = len(vids)
        res["significant"] = sum(1 for t in res["traits"] if t["significant"])
        out["formats"][name] = res
    return out

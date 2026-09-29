"""Your own channels' real numbers (plan 14) next to our public-data
estimates. Pure functions, no DB, no network.

RPM here is what YouTube Studio calls RPM: estimated revenue per 1,000 views
of ALL views (monetized or not), so it compares with domain.metrics
rpm_range(), which is already discounted for unmonetized views. The Analytics
API has no thumbnail impressions or click-through rate -- those exist only in
YouTube Studio -- so nothing here pretends to know CTR.
"""
import statistics as st


def rpm(revenue, views):
    if revenue is None or not views:
        return None
    return round(revenue / views * 1000, 2)


def calibrate(real_rpm, rng: dict) -> dict:
    """Where the real RPM falls against our estimated low / mid / high."""
    if real_rpm is None or not rng:
        return {"realRpm": real_rpm, "estimate": rng, "position": "unknown", "vsMid": None}
    position = ("below" if real_rpm < rng["low"] else "above" if real_rpm > rng["high"]
                else "inside")
    return {"realRpm": real_rpm, "estimate": rng, "position": position,
            "vsMid": round(real_rpm / rng["mid"], 2) if rng.get("mid") else None}


def versus_niche(own_videos, niche_views) -> dict:
    """own_videos: [{views, averageViewPercentage}] of your channel over the
    same window; niche_views: views of the niche's videos we collected."""
    own = [v["views"] for v in own_videos if v.get("views") is not None]
    retention = [v["averageViewPercentage"] for v in own_videos
                 if v.get("averageViewPercentage") is not None]
    niche = [x for x in niche_views if x is not None]
    own_med = int(st.median(own)) if own else None
    niche_med = int(st.median(niche)) if niche else None
    return {
        "ownVideos": len(own), "nicheVideos": len(niche),
        "ownMedianViews": own_med, "nicheMedianViews": niche_med,
        "ratio": round(own_med / niche_med, 2) if own_med is not None and niche_med else None,
        "shareAboveNicheMedian": (round(sum(1 for x in own if x > niche_med) / len(own), 2)
                                  if own and niche_med is not None else None),
        "ownMedianRetentionPct": round(st.median(retention), 1) if retention else None,
    }

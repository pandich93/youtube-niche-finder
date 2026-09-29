"""YouTube Partner Program thresholds from public data (plan 12). Pure, no DB.

YouTube does not publish whether someone else's channel is monetized, and the
page signals checked in the plan-12 spike were either unreliable (ads run on
non-partner channels too) or invisible without a signed-in viewer (the "Join"
button). So this says only which YPP thresholds a channel VISIBLY meets:

  expanded tier  500 subscribers + 3 public uploads in 90 days
                 + 3,000 watch hours in 12 months OR 3M Shorts views in 90 days
  full tier      1,000 subscribers
                 + 4,000 watch hours in 12 months OR 10M Shorts views in 90 days

Watch hours are not in the Data API, so they are always None. Uploads and
Shorts views come from the videos WE collected, so both are lower bounds: a
channel can meet a threshold we cannot see. Meeting thresholds is not being
monetized either -- a channel still has to apply and be accepted.

Statuses: below-threshold (under 500 subscribers, so no tier is possible),
subscribers-met (a subscriber bar is met, the rest is unknown),
shorts-path-met (a tier is fully met through Shorts views we can see),
unknown (subscriber count hidden or missing).
"""
WINDOW_DAYS = 90
TIERS = {
    "expanded": {"subscribers": 500, "uploads90d": 3, "shortsViews90d": 3_000_000,
                 "watchHours": 3_000},
    "full": {"subscribers": 1_000, "uploads90d": None, "shortsViews90d": 10_000_000,
             "watchHours": 4_000},
}
NOTE = ("Not a monetization status: YouTube does not publish it. Only the YPP thresholds this "
        "channel visibly meets; uploads and Shorts views are a lower bound from collected videos, "
        "watch hours are not available through the API, and meeting a threshold still needs an "
        "accepted application.")


def ypp_eligibility(subscribers, hidden: bool, videos) -> dict:
    """videos: [{ageDays, views, isShort}] of this channel that we collected."""
    recent = [v for v in videos if v.get("ageDays") is not None and v["ageDays"] <= WINDOW_DAYS]
    uploads = len(recent)
    shorts_views = sum(int(v.get("views") or 0) for v in recent if v.get("isShort"))
    known = subscribers is not None and not hidden

    tiers, full_path = {}, False
    for name, t in TIERS.items():
        subs_met = bool(known and subscribers >= t["subscribers"])
        up = None
        if t["uploads90d"] is not None:
            up = {"seen": uploads, "needed": t["uploads90d"], "met": uploads >= t["uploads90d"]}
        shorts = {"seenAtLeast": shorts_views, "needed": t["shortsViews90d"],
                  "met": shorts_views >= t["shortsViews90d"]}
        tiers[name] = {"subscribers": subs_met if known else None,
                       "subscribersNeeded": t["subscribers"], "uploads90d": up,
                       "shortsViews90d": shorts, "watchHours": None,
                       "watchHoursNeeded": t["watchHours"]}
        if subs_met and shorts["met"] and (up is None or up["met"]):
            full_path = True

    if not known:
        status = "unknown"
    elif subscribers < TIERS["expanded"]["subscribers"]:
        status = "below-threshold"
    elif full_path:
        status = "shorts-path-met"
    else:
        status = "subscribers-met"
    return {"status": status, "subscribers": subscribers if known else None,
            "tiers": tiers, "windowDays": WINDOW_DAYS, "note": NOTE}

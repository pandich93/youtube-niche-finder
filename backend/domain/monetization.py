"""YouTube Partner Program thresholds from public data (plan 12). Pure, no DB.

YouTube does not publish whether someone else's channel is monetized, and the
page signals checked in the plan-12 spike were either unreliable (ads run on
non-partner channels too) or invisible without a signed-in viewer (the "Join"
button). So this says only which YPP thresholds a channel VISIBLY meets:

  expanded tier  500 subscribers + 3 public uploads in 90 days
                 + 3,000 watch hours in 12 months OR 3M Shorts views in 90 days
  full tier      1,000 subscribers
                 + 4,000 watch hours in 12 months OR 10M Shorts views in 90 days

From 2027-02-01 (plan 17; YouTube Help answer 12843009) new creators need
1,000 subscribers + 8,000 qualified watch hours in 365 days OR 20M qualified
Shorts views in 90 days, and a channel stays active with 1,000 qualified
watch hours in 365 days, 1M qualified Shorts views in 90 days, or 2 long
videos / 5 Shorts uploaded in 90 days. The help page does not say whether the
expanded tier changes, so it keeps its 2026 numbers. Before the switch the
answer also carries the same check under the 2027 rules as `upcoming`.

Watch hours are not in the Data API, so they are always None. Uploads and
Shorts views come from the videos WE collected, so both are lower bounds: a
channel can meet a threshold we cannot see. Meeting thresholds is not being
monetized either -- a channel still has to apply and be accepted.

Statuses: below-threshold (under 500 subscribers, so no tier is possible),
subscribers-met (a subscriber bar is met, the rest is unknown),
shorts-path-met (a tier is fully met through Shorts views we can see),
unknown (subscriber count hidden or missing).
"""
from datetime import date, datetime, timezone

WINDOW_DAYS = 90
TIERS = {
    "expanded": {"subscribers": 500, "uploads90d": 3, "shortsViews90d": 3_000_000,
                 "watchHours": 3_000},
    "full": {"subscribers": 1_000, "uploads90d": None, "shortsViews90d": 10_000_000,
             "watchHours": 4_000},
}
RULES_2027_FROM = date(2027, 2, 1)
TIERS_2027 = {
    "expanded": TIERS["expanded"],
    "full": {"subscribers": 1_000, "uploads90d": None, "shortsViews90d": 20_000_000,
             "watchHours": 8_000},
}
# 2027 activity: any one of these keeps a channel active
ACTIVITY_2027 = {"longUploads90d": 2, "shortsUploads90d": 5, "shortsViews90d": 1_000_000}
# Filter order (plan 12): "at least this far". unknown never passes a filter --
# a hidden subscriber count proves nothing.
FILTER_STATUSES = ("subscribers-met", "shorts-path-met")
_RANK = {"below-threshold": 0, "subscribers-met": 1, "shorts-path-met": 2}
NOTE = ("Not a monetization status: YouTube does not publish it. Only the YPP thresholds this "
        "channel visibly meets; uploads and Shorts views are a lower bound from collected videos, "
        "watch hours are not available through the API, and meeting a threshold still needs an "
        "accepted application.")


def tiers_on(day: date) -> dict:
    """The thresholds in force on `day`."""
    return TIERS_2027 if day >= RULES_2027_FROM else TIERS


def _activity(recent: list, shorts_views: int) -> dict:
    """2027 activity from the uploads we collected. Watch hours could also
    satisfy it and are not visible, so not seeing enough is None, not False."""
    long_n = sum(1 for v in recent if not v.get("isShort"))
    shorts_n = sum(1 for v in recent if v.get("isShort"))
    a = ACTIVITY_2027
    met = (long_n >= a["longUploads90d"] or shorts_n >= a["shortsUploads90d"]
           or shorts_views >= a["shortsViews90d"])
    return {"longUploads90d": long_n, "shortsUploads90d": shorts_n,
            "shortsViews90dAtLeast": shorts_views, "met": True if met else None}


def ypp_eligibility(subscribers, hidden: bool, videos, on: date = None) -> dict:
    """videos: [{ageDays, views, isShort}] of this channel that we collected.
    `on` picks the rules (default: today, UTC)."""
    on = on or datetime.now(timezone.utc).date()
    out = _eligibility(subscribers, hidden, videos, tiers_on(on))
    if on >= RULES_2027_FROM:
        out["rules"] = "2027"
    else:
        out["rules"] = "2026"
        up = _eligibility(subscribers, hidden, videos, TIERS_2027)
        out["upcoming"] = {"effective": RULES_2027_FROM.isoformat(), "status": up["status"],
                           "tiers": up["tiers"], "activity": up["activity"]}
        del out["activity"]
    return out


def _eligibility(subscribers, hidden: bool, videos, rules: dict) -> dict:
    recent = [v for v in videos if v.get("ageDays") is not None and v["ageDays"] <= WINDOW_DAYS]
    uploads = len(recent)
    shorts_views = sum(int(v.get("views") or 0) for v in recent if v.get("isShort"))
    known = subscribers is not None and not hidden

    tiers, full_path = {}, False
    for name, t in rules.items():
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
    elif subscribers < rules["expanded"]["subscribers"]:
        status = "below-threshold"
    elif full_path:
        status = "shorts-path-met"
    else:
        status = "subscribers-met"
    return {"status": status, "subscribers": subscribers if known else None,
            "tiers": tiers, "windowDays": WINDOW_DAYS, "note": NOTE,
            "activity": _activity(recent, shorts_views)}


def check_filter(min_status):
    """None passes through; anything outside FILTER_STATUSES is an error."""
    if min_status is not None and min_status not in FILTER_STATUSES:
        raise ValueError(f"min_ypp_status must be one of {', '.join(FILTER_STATUSES)}")
    return min_status


def meets(status: str, min_status: str) -> bool:
    return status in _RANK and _RANK[status] >= _RANK[min_status]


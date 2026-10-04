"""Your own channel's formats (plan 24), from YouTube Analytics rows of
day x creatorContentType (SHORTS, VIDEO_ON_DEMAND, LIVE_STREAM, STORY).
Pure, no DB.

  summary        views, watch minutes and new subscribers per format over a
                 window, each format's share of views and subscribers per
                 1,000 views -- do Shorts bring subscribers, or only views?
  shorts_vs_long weekly Shorts views against long-form views: Pearson r over
                 WEEKS_MIN+ weeks. A correlation, never a cause -- both can
                 rise with one good week of the channel.
  watch_hours    long-form + live watch hours in the last 365 days toward
                 the YPP bar (4,000 now, 8,000 from 2027-02-01) and when the
                 last 30 days' pace gets there. estimatedMinutesWatched is
                 not YouTube's "qualified watch hours", so it is an
                 approximation; the pace ignores old days dropping out of
                 the 365-day window.

Every number here is your own Analytics data; the shares, r and the date are
estimates of niche-finder.
"""
import statistics as st
from datetime import date, timedelta

LONG_TYPES = ("VIDEO_ON_DEMAND", "LIVE_STREAM")
WEEKS_MIN = 8
HOURS_2026, HOURS_2027 = 4000, 8000


def _day(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _within(rows, end: date, days: int):
    start = end - timedelta(days=days - 1)
    return [r for r in rows if start <= _day(r["day"]) <= end]


def summary(rows, end: date, days: int = 90) -> dict:
    window = _within(rows, end, days)
    total = sum(r["views"] or 0 for r in window)
    out = {}
    for r in window:
        t = out.setdefault(r["contentType"], {"views": 0, "minutes": 0, "subscribers": 0})
        t["views"] += r["views"] or 0
        t["minutes"] += r["minutes"] or 0
        t["subscribers"] += r["subscribers"] or 0
    for t in out.values():
        t["viewShare"] = round(t["views"] / total, 3) if total else None
        t["subscribersPer1000Views"] = (round(t["subscribers"] / t["views"] * 1000, 2)
                                        if t["views"] else None)
    return out


def weekly(rows, end: date, weeks: int = 12) -> dict:
    """{contentType: [{weekEnd, views, subscribers}] oldest first} -- weeks
    counted back from `end`, so the last one is always complete."""
    out = {}
    for r in _within(rows, end, weeks * 7):
        idx = (end - _day(r["day"])).days // 7
        series = out.setdefault(r["contentType"], [None] * weeks)
        slot = weeks - 1 - idx
        if series[slot] is None:
            series[slot] = {"weekEnd": (end - timedelta(days=7 * idx)).isoformat(),
                            "views": 0, "subscribers": 0}
        series[slot]["views"] += r["views"] or 0
        series[slot]["subscribers"] += r["subscribers"] or 0
    for kind, series in out.items():
        out[kind] = [s if s is not None else
                     {"weekEnd": (end - timedelta(days=7 * (weeks - 1 - i))).isoformat(),
                      "views": 0, "subscribers": 0} for i, s in enumerate(series)]
    return out


def shorts_vs_long(rows, end: date, weeks: int = 12) -> dict:
    w = weekly(rows, end, weeks)
    shorts = [x["views"] for x in w.get("SHORTS", [])]
    long_ = [sum(x["views"] for x in pair) for pair in
             zip(*[w[k] for k in LONG_TYPES if k in w])] if any(k in w for k in LONG_TYPES) else []
    have = [i for i in range(min(len(shorts), len(long_))) if shorts[i] or long_[i]]
    out = {"weeks": len(have), "r": None, "reading": None, "reason": None,
           "note": "a correlation over weeks, not a cause"}
    if len(have) < WEEKS_MIN:
        out["reason"] = "few-weeks"
        return out
    xs, ys = [shorts[i] for i in have], [long_[i] for i in have]
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        out["reason"] = "no-variation"
        return out
    r = round(st.correlation(xs, ys), 2)
    out["r"] = r
    out["reading"] = ("move-together" if r >= 0.5 else "move-apart" if r <= -0.5
                      else "no-clear-link")
    return out


def watch_hours(rows, end: date) -> dict:
    long_rows = [r for r in rows if r["contentType"] in LONG_TYPES]
    hours365 = sum(r["minutes"] or 0 for r in _within(long_rows, end, 365)) / 60
    pace = sum(r["minutes"] or 0 for r in _within(long_rows, end, 30)) / 60 / 30
    shorts90 = sum(r["views"] or 0 for r in _within(rows, end, 90) if r["contentType"] == "SHORTS")

    def eta(need):
        if hours365 >= need or pace <= 0:
            return None
        return (end + timedelta(days=round((need - hours365) / pace))).isoformat()

    return {"hours365": round(hours365, 1), "shortsViews90d": shorts90,
            "paceHoursPerDay": round(pace, 2), "needed": {"2026": HOURS_2026, "2027": HOURS_2027},
            "reached4000": hours365 >= HOURS_2026, "reached8000": hours365 >= HOURS_2027,
            "eta4000": eta(HOURS_2026), "eta8000": eta(HOURS_2027),
            "note": "estimatedMinutesWatched of long videos and live, not YouTube's qualified "
                    "watch hours; the pace ignores old days leaving the 365-day window"}

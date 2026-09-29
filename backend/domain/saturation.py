"""Niche saturation trend (plan 08): growing / stable / cooling / saturated,
from the last 30 days against the 90 days before them. Pure functions, no DB.

Input is one dict per video of the niche published in the last 120 days:
  channelId, ageDays, projectedViews (views projected to day 30 by the
  maturity curve, so a 5-day-old and a 100-day-old video compare), outlierScore,
  isShort, caughtYoung (first seen within CAUGHT_YOUNG_DAYS of publishing; None
  when unknown)
and {channelId: channel age in days or None}.

Signals, every base value averaged per 30 days:
  supply     videos in the window;
  demand     median projected views of the dominant format (Shorts and
             long-form never mixed), videos younger than MIN_AGE_DAYS left out;
  entrants   channels created inside the window;
  newcomers  share of channels younger than NEWCOMER_DAYS with a video at
             outlierScore >= BREAKOUT.
A niche we mostly did not see young (low caughtYoung share) may show a trend
that is only how it was collected: the status is kept, confidence is "low".
"""
import statistics as st

RECENT_DAYS = 30
BASE_DAYS = 90
WINDOW_DAYS = RECENT_DAYS + BASE_DAYS
MIN_VIDEOS = 20          # per window, below it the answer is "insufficient-data"
MIN_AGE_DAYS = 3         # a projection from under 3 days of views is noise
NEWCOMER_DAYS = 180
MIN_NEWCOMERS = 3
BREAKOUT = 2.0
UP, DOWN = 1.2, 0.8      # ratio recent / base at which a signal counts as moving
BREAKOUT_HIGH, BREAKOUT_LOW = 0.2, 0.1
LOW_COVERAGE = 0.3


def _ratio(recent, base):
    return round(recent / base, 2) if base else None


def _move(ratio):
    if ratio is None:
        return None
    return "up" if ratio >= UP else "down" if ratio <= DOWN else "flat"


def _median(values):
    return int(st.median(values)) if values else None


def saturation(videos, channel_ages) -> dict:
    videos = [v for v in videos if v.get("ageDays") is not None and v["ageDays"] < WINDOW_DAYS]
    recent = [v for v in videos if v["ageDays"] < RECENT_DAYS]
    base = [v for v in videos if v["ageDays"] >= RECENT_DAYS]
    per = BASE_DAYS / RECENT_DAYS

    base_rate = round(len(base) / per, 2)
    supply = {"ratio": _ratio(len(recent), base_rate), "recent": len(recent),
              "basePer30d": base_rate}

    shorts = sum(1 for v in videos if v.get("isShort"))
    short_fmt = shorts > len(videos) - shorts
    def demand_of(vs):
        return _median([v["projectedViews"] for v in vs
                        if bool(v.get("isShort")) == short_fmt and v["ageDays"] >= MIN_AGE_DAYS
                        and v.get("projectedViews") is not None])
    d_recent, d_base = demand_of(recent), demand_of(base)
    demand = {"ratio": _ratio(d_recent, d_base) if d_recent is not None else None,
              "recentMedian": d_recent, "baseMedian": d_base}

    channels = {v["channelId"] for v in videos}
    ages = {c: channel_ages.get(c) for c in channels}
    known = {c: a for c, a in ages.items() if a is not None}
    e_recent = sum(1 for a in known.values() if a < RECENT_DAYS)
    e_base = sum(1 for a in known.values() if RECENT_DAYS <= a < WINDOW_DAYS)
    e_base_rate = round(e_base / per, 2)
    entrants = {"ratio": round((e_recent + 1) / (e_base_rate + 1), 2) if known else None,
                "recent": e_recent, "basePer30d": e_base_rate,
                "unknownAge": len(ages) - len(known)}

    young = {c for c, a in known.items() if a < NEWCOMER_DAYS}
    broke = {v["channelId"] for v in videos
             if v["channelId"] in young and (v.get("outlierScore") or 0) >= BREAKOUT}
    newcomers = {"share": round(len(broke) / len(young), 2) if len(young) >= MIN_NEWCOMERS
                 else None, "channels": len(young), "brokeOut": len(broke)}

    seen = [v["caughtYoung"] for v in videos if v.get("caughtYoung") is not None]
    coverage = {"caughtYoungShare": round(sum(seen) / len(seen), 2) if seen else None,
                "known": len(seen)}

    signals = {"supply": supply, "demand": demand, "entrants": entrants,
               "newcomers": newcomers, "coverage": coverage}
    status, reasons = classify(signals, {"recent": len(recent), "base": len(base)})
    low = coverage["caughtYoungShare"] is None or coverage["caughtYoungShare"] < LOW_COVERAGE
    if low:
        reasons.append({"code": "low-coverage"})
    return {"status": status, "confidence": "low" if low else "normal",
            "format": "short" if short_fmt else "long",
            "windows": {"recentDays": RECENT_DAYS, "baseDays": BASE_DAYS},
            "counts": {"recent": len(recent), "base": len(base)},
            "signals": signals, "reasons": reasons,
            "series": {"videos": [base_rate, len(recent)], "demand": [d_base, d_recent]}}


def classify(signals, counts):
    """(status, reasons): one reason per signal, so there are always at least
    four -- the screen shows the numbers behind each one."""
    supply = _move(signals["supply"]["ratio"])
    demand = _move(signals["demand"]["ratio"])
    entrants = _move(signals["entrants"]["ratio"])
    share = signals["newcomers"]["share"]
    newcomers = (None if share is None else "break-out" if share >= BREAKOUT_HIGH
                 else "rarely-break-out" if share < BREAKOUT_LOW else "some")
    reasons = [{"code": f"supply-{supply or 'unknown'}"},
               {"code": f"demand-{demand or 'unknown'}"},
               {"code": f"entrants-{entrants or 'unknown'}"},
               {"code": f"newcomers-{newcomers or 'unknown'}"}]

    if counts["recent"] < MIN_VIDEOS or counts["base"] < MIN_VIDEOS:
        return "insufficient-data", [{"code": "few-videos"}] + reasons
    if supply == "up" and (demand == "down" or newcomers == "rarely-break-out"):
        return "saturated", reasons
    if demand == "up" and (newcomers == "break-out" or entrants == "up"):
        return "growing", reasons
    if demand == "down" and supply != "up":
        return "cooling", reasons
    return "stable", reasons

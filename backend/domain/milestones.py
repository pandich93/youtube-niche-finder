"""Subscriber milestones (plan 17): when a channel reaches its next round
number at its recent pace. Pure, no DB.

An estimate of niche-finder from the snapshots the worker takes, not YouTube
data: a straight line through the pace of the last 30 and the last 90 days,
both shown because growth is rarely linear. Too few snapshots, too short a
history or no growth give no date and say why.
"""
from datetime import timedelta

SUBSCRIBER_MILESTONES = (100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000)
MIN_POINTS = 5
MIN_SPAN_DAYS = 7
MAX_ETA_DAYS = 3650     # beyond ten years a straight line means nothing
NOTE = ("Estimate of niche-finder from our own snapshots at the recent pace, "
        "not YouTube data; growth is rarely a straight line.")


def next_targets(current, milestones=SUBSCRIBER_MILESTONES, n: int = 2) -> list:
    return [m for m in milestones if m > (current or 0)][:n]


def crossed(previous, current, milestones=SUBSCRIBER_MILESTONES) -> list:
    """Milestones passed between two readings (previous < m <= current)."""
    if previous is None or current is None:
        return []
    return [m for m in milestones if previous < m <= current]


def _pace(points, days: int):
    """Units per day between the first point inside the last `days` and the
    latest one; None when that stretch is shorter than MIN_SPAN_DAYS."""
    last_t, last_v = points[-1]
    start = last_t - timedelta(days=days)
    inside = [p for p in points if p[0] >= start]
    first_t, first_v = inside[0]
    span = (last_t - first_t).total_seconds() / 86400
    if span < MIN_SPAN_DAYS:
        return None
    return (last_v - first_v) / span


def _eta(points, target, pace):
    if not pace or pace <= 0:
        return None
    days = (target - points[-1][1]) / pace
    if days > MAX_ETA_DAYS:
        return None
    return (points[-1][0] + timedelta(days=days)).date().isoformat()


def forecast(points, target: int) -> dict:
    """points: [(datetime, value)] in any order."""
    points = sorted(p for p in points if p[1] is not None)
    current = points[-1][1] if points else None
    out = {"target": target, "current": current, "reached": bool(current is not None
                                                                  and current >= target),
           "sample": len(points), "pace30PerDay": None, "eta30": None,
           "pace90PerDay": None, "eta90": None, "reason": None}
    if out["reached"]:
        return out
    if len(points) < MIN_POINTS:
        out["reason"] = "few-snapshots"
        return out
    p30, p90 = _pace(points, 30), _pace(points, 90)
    if p30 is None:
        out["reason"] = "short-history"
        return out
    out["pace30PerDay"] = round(p30, 2)
    out["pace90PerDay"] = round(p90, 2) if p90 is not None else None
    if p30 <= 0 and (p90 is None or p90 <= 0):
        out["reason"] = "no-growth"
        return out
    out["eta30"], out["eta90"] = _eta(points, target, p30), _eta(points, target, p90)
    if out["eta30"] is None and out["eta90"] is None:
        out["reason"] = "too-slow"
    return out

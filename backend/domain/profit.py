"""Net profit (plan 30): what a video or a channel earns minus what it costs
to make. Pure functions, no DB.

Revenue is a range, so profit is a range: revenue at the low / mid / high
effective RPM (niche-finder's estimate, or your real RPM on a connected
channel, where the three are one number) minus the costs of a cost profile:

  video cost    per_video + per_minute x minutes of video
  monthly cost  monthly overhead + uploads in the month x video cost

`verdict`: "profitable" when even the low end is above zero, "loss" when even
the high end is below it, otherwise "uncertain" -- the honest answer for most
niches, whose RPM nobody publishes. The break-even views are the views one
video needs to pay for itself at each RPM. AdSense only: sponsors and other
income are not in it. An estimate of niche-finder, not YouTube data.
"""


def video_cost(profile: dict, duration_seconds) -> float:
    minutes = (duration_seconds or 0) / 60
    return round(profile["per_video_usd"] + profile["per_minute_usd"] * minutes, 2)


def revenue(views, rpm: dict) -> dict:
    """views / 1000 x RPM at each end of the range."""
    return {k: round((views or 0) / 1000 * rpm[k], 2) for k in ("low", "mid", "high")}


def break_even_views(cost: float, rpm: dict) -> dict:
    """Views that make `cost` back at each RPM (None when that RPM is zero)."""
    return {k: (int(round(cost / rpm[k] * 1000)) if rpm[k] else None) for k in ("low", "mid", "high")}


def verdict(profit: dict) -> str:
    if profit["low"] > 0:
        return "profitable"
    if profit["high"] < 0:
        return "loss"
    return "uncertain"


def _result(rev: dict, cost: float, rpm: dict) -> dict:
    profit = {k: round(rev[k] - cost, 2) for k in rev}
    return {"revenue": rev, "cost": round(cost, 2), "profit": profit, "verdict": verdict(profit)}


def per_video(profile: dict, rpm: dict, views, duration_seconds) -> dict:
    """One video: its revenue range, its cost, the profit range, break-even views."""
    cost = video_cost(profile, duration_seconds)
    out = _result(revenue(views, rpm), cost, rpm)
    out["views"] = views
    out["breakEvenViews"] = break_even_views(cost, rpm)
    return out


def per_month(profile: dict, rpm: dict, monthly_views, uploads: int, avg_duration_seconds) -> dict:
    """A month: revenue of the month's views, the overhead plus every upload's cost."""
    each = video_cost(profile, avg_duration_seconds)
    cost = profile["monthly_usd"] + uploads * each
    out = _result(revenue(monthly_views, rpm), cost, rpm)
    out.update({"monthlyViews": monthly_views, "uploads": uploads, "costPerVideo": each,
                "overhead": round(profile["monthly_usd"], 2)})
    return out


def real_rpm(value) -> dict:
    """A measured RPM is one number, not a range."""
    return {"low": value, "mid": value, "high": value}

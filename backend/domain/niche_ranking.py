"""Where to enter (plan 27): one 0-100 score per niche from six signals we
already compute, with every term shown. Pure functions, no DB.

Each term is first turned into 0-100 points on a stated ramp, then weighted:

  demand     25  median projected views, last 30 days vs the 90 before (ratio)
  supply     20  videos per 30 days vs the base rate -- a niche whose output
                 is accelerating is getting crowded (ratio, inverted)
  newcomers  20  share of young channels with a break-out video
  rpm        15  effective RPM of the niche's dominant category (a guess:
                 YouTube publishes none, estimates disagree by up to 7x)
  template   10  share of channels that look like one template (inverted)
  policy     10  worst share of channels with signals for the three
                 "inauthentic content" categories (inverted)

A term that is unknown (None) is left out and the others are re-weighted; the
niche gets no score at all when the trend itself is "insufficient-data" or
less than MIN_COVERAGE of the weight is known -- a small niche must not float
to the top by accident. The weights are a judgement, not a measurement: the
screen shows the breakdown and lets you sort by any column. An estimate of
niche-finder, not YouTube data, and a correlation, not a promise.
"""

WEIGHTS = {"demand": 25, "supply": 20, "newcomers": 20, "rpm": 15, "template": 10, "policy": 10}
LABELS = {"demand": "Спрос", "supply": "Предложение", "newcomers": "Новички пробиваются",
          "rpm": "RPM", "template": "Шаблонные каналы", "policy": "Сигналы по правилам"}
# value that scores 0 and value that scores 100 (a term can run either way)
RAMPS = {"demand": (0.6, 1.4), "supply": (1.6, 0.6), "newcomers": (0.0, 0.3),
         "rpm": (1.0, 8.0), "template": (0.5, 0.0), "policy": (0.7, 0.0)}
MIN_COVERAGE = 0.6
HIGH, MEDIUM = 60, 40


def _points(key: str, value) -> float:
    lo, hi = RAMPS[key]
    return round(max(0.0, min(1.0, (value - lo) / (hi - lo))) * 100, 1)


def score(m: dict) -> dict:
    """m: status, demandRatio, supplyRatio, newcomersShare, rpmMid,
    templateShare, policyShare (each may be None)."""
    values = {"demand": m.get("demandRatio"), "supply": m.get("supplyRatio"),
              "newcomers": m.get("newcomersShare"), "rpm": m.get("rpmMid"),
              "template": m.get("templateShare"), "policy": m.get("policyShare")}
    breakdown, known = [], 0
    for key, weight in WEIGHTS.items():
        v = values[key]
        breakdown.append({"key": key, "label": LABELS[key], "weight": weight, "value": v,
                          "points": None if v is None else _points(key, v), "contribution": None})
        known += weight if v is not None else 0
    coverage = round(known / sum(WEIGHTS.values()), 2)
    out = {"score": None, "band": None, "reason": None, "coverage": coverage,
           "breakdown": breakdown}
    if m.get("status") == "insufficient-data":
        out["reason"] = "insufficient-data"
        return out
    if coverage < MIN_COVERAGE:
        out["reason"] = "few-signals"
        return out
    total = sum(b["points"] * b["weight"] for b in breakdown if b["points"] is not None)
    out["score"] = round(total / known)
    out["band"] = "high" if out["score"] >= HIGH else "medium" if out["score"] >= MEDIUM else "low"
    for b in breakdown:
        b["contribution"] = (None if b["points"] is None
                             else round(b["points"] * b["weight"] / known, 1))
    return out


def sort_key(entry: dict):
    """Best score first; niches without one last, bigger corpus first among them."""
    s = entry["score"]
    return (s is None, -(s or 0), -(entry.get("videos") or 0))

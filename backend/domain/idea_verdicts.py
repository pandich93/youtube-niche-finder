"""Stage 17: turn a list of matched videos for one content idea into a
single actionable verdict -- "should I film this?" Pure function, no DB.

Rule order (freshness always checked before performance):
  1. no matches at all                                  -> free
  2. the most recent match is younger than recent_days   -> recent (someone
     just did this -- skip it regardless of how well it did)
  3. old enough, best outlier among matches >= proven_outlier -> proven
     (demand is validated, worth another take)
  4. everything else                                     -> flopped

"flopped" deliberately covers two different situations that both mean "don't
just copy this without a new angle": a genuine failure (best outlier <=
flop_outlier) and a lukewarm middle band (between flop_outlier and
proven_outlier) where the signal isn't strong enough to call it proven.
performance_band in the return value tells those two apart for anyone who
wants the nuance; the verdict itself only exposes the four buckets the plan
asked for.
"""

VALID_VERDICTS = ("free", "recent", "proven", "flopped")


def verdict(matches: list, recent_days: float = 90, proven_outlier: float = 2.0,
           flop_outlier: float = 0.5) -> dict:
    """matches: [{"ageDays": float, "outlierScore": float | None}, ...] --
    every video already found for one idea. ageDays is required per match;
    outlierScore may be None (too little history for a baseline)."""
    if not matches:
        return {"verdict": "free", "daysSinceLastCoverage": None,
                "bestOutlierScore": None, "performanceBand": None}

    days_since = min(m["ageDays"] for m in matches)
    if days_since <= recent_days:
        return {"verdict": "recent", "daysSinceLastCoverage": round(days_since, 1),
                "bestOutlierScore": None, "performanceBand": None}

    scored = [m["outlierScore"] for m in matches if m.get("outlierScore") is not None]
    best = max(scored) if scored else None
    if best is None:
        band = None  # no baseline for any match -- not enough data to call it either way
    elif best >= proven_outlier:
        band = "proven"
    elif best <= flop_outlier:
        band = "flop"
    else:
        band = "moderate"

    return {
        "verdict": "proven" if band == "proven" else "flopped",
        "daysSinceLastCoverage": round(days_since, 1),
        "bestOutlierScore": best,
        "performanceBand": band,
    }

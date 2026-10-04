"""Collaboration partners (plan 31): which similar channels are worth asking
for a joint video or a shout-out. Pure functions, no DB.

A candidate passes when it is
  similar   its videos read close to yours (embedding centroids, cosine at
            least MIN_SIMILARITY -- the pool is "the closest 200", not "close");
  your size between MIN_RATIO and MAX_RATIO times your subscribers -- a much
            bigger channel rarely says yes, a much smaller one brings little;
  active    it uploaded within ACTIVE_DAYS (among the videos we collected);
  original  its recent uploads do not look like one template (plan 01 risk
            "high") -- a templated channel is about to be swept by YouTube.
Each candidate carries the numbers behind every check, so the reason it was
picked (or left out) can be shown. The order is by similarity; among
channels equally similar (to 0.01) the faster-growing one comes first.
"""

MIN_RATIO, MAX_RATIO = 0.5, 2.0
MIN_SIMILARITY = 0.5
ACTIVE_DAYS = 30


def check(c: dict, own_subs, min_ratio=MIN_RATIO, max_ratio=MAX_RATIO,
          active_days=ACTIVE_DAYS, min_similarity=MIN_SIMILARITY) -> str | None:
    """None when the candidate passes, else why not: 'off-topic',
    'size-unknown', 'too-small', 'too-big', 'inactive', 'templated'.
    c: similarity, subscribers, daysSinceUpload, templateRisk ('low'/'medium'/'high'/None)."""
    if c.get("similarity") is None or c["similarity"] < min_similarity:
        return "off-topic"
    subs = c.get("subscribers")
    if not own_subs or subs is None:
        return "size-unknown"
    ratio = subs / own_subs
    if ratio < min_ratio:
        return "too-small"
    if ratio > max_ratio:
        return "too-big"
    if c.get("daysSinceUpload") is None or c["daysSinceUpload"] > active_days:
        return "inactive"
    if c.get("templateRisk") == "high":
        return "templated"
    return None


def size_ratio(subs, own_subs):
    return round(subs / own_subs, 2) if subs is not None and own_subs else None


def sort_key(c: dict):
    return (-round(c["similarity"], 2), -(c.get("growth30dPct") or 0))

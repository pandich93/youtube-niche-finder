"""Stage 09: the deterministic half of title scoring -- everything that
works without an LLM or a network call. length/number/brackets checks,
matched title patterns (from channel_tracking.py:title_patterns), and a
near-duplicate flag (caller supplies the cosine similarity against the
niche's existing videos; this module stays embedding-agnostic). Pure
functions, no DB, no network.
"""
import re

_HAS_DIGIT = re.compile(r"\d")
_HAS_BRACKET = re.compile(r"[()\[\]]")

LENGTH_MIN, LENGTH_MAX = 30, 70
NEAR_DUPLICATE_THRESHOLD = 0.9


def deterministic_signals(title: str, patterns: list, near_duplicate_score: float = None) -> dict:
    """patterns: [{"keyword": str, ...}, ...] from title_patterns(), already
    scoped to the right niche/channel. near_duplicate_score: max cosine
    similarity against already-published videos, or None if unavailable
    (no embeddings / fastembed unavailable) -- treated as "unknown", not
    "not a duplicate"."""
    title_lower = title.lower()
    matched = [p["keyword"] for p in patterns if p.get("keyword") and p["keyword"].lower() in title_lower]
    return {
        "length": len(title),
        "lengthOk": LENGTH_MIN <= len(title) <= LENGTH_MAX,
        "hasNumber": bool(_HAS_DIGIT.search(title)),
        "hasBrackets": bool(_HAS_BRACKET.search(title)),
        "matchedPatterns": matched,
        "nearDuplicateScore": near_duplicate_score,
        "isLikelyDuplicate": (near_duplicate_score or 0) >= NEAR_DUPLICATE_THRESHOLD,
    }


def deterministic_score(signals: dict) -> int:
    """0-100, starting from a neutral 50 -- this is the fallback score used
    whenever the LLM is off, so it should be a real (if rough) signal on
    its own, not just a placeholder."""
    score = 50
    score += 10 if signals["lengthOk"] else -10
    if signals["hasNumber"]:
        score += 5
    score += min(20, 10 * len(signals["matchedPatterns"]))
    if signals["isLikelyDuplicate"]:
        score -= 30
    return max(0, min(100, score))

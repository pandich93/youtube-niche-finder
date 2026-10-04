"""Signals for YouTube's three "inauthentic content" categories (plan 22),
from public data only. Pure, no DB.

YouTube's channel monetization policies describe inauthentic content in
three groups (support.google.com/youtube/answer/1311392, checked
2026-10-04; Tubefilter 2026-07-13 calls the July 2026 text a rewording,
not a rule change):

  generic_repetitive     made from a template, feels interchangeable from
                         video to video
  unsatisfying           relies on emotionally manipulative formulas or
                         shock
  ai_persona_sensitive   an AI persona presented as a human expert on
                         health, legal, financial or political topics

Each category gets the signals visible from outside, with a level
none / watch / high (or insufficient-data) and examples -- never one
"risk of demonetization" percentage. The decision is YouTube's, made by
people; a persona cannot be seen in public data at all, so that category
never goes above "watch". Reused content is a separate policy and is not
mixed in here.
"""
import re

POLICY_URL = "https://support.google.com/youtube/answer/1311392"
POLICY = {
    "generic_repetitive": "content that feels interchangeable from video to video; "
                          "made with generic or unoriginal templates",
    "unsatisfying": "content that relies heavily on emotionally manipulative formulas "
                    "or appears designed to shock",
    "ai_persona_sensitive": "AI-generated personas delivering information on sensitive topics "
                            "such as health, legal, financial or political advice",
}

MIN_TITLES = 5
SHOCK_HIGH, SHOCK_WATCH = 0.5, 0.25
THUMB_SIMILAR = 0.85          # mean CLIP cosine between a channel's own thumbnails
SENSITIVE_SHARE = 0.3
SYNTHETIC_SHARE = 0.3

_SHOCK_PHRASES = re.compile(
    r"\b(you won'?t believe|shocking|shocked|disturbing|horrifying|terrifying|gone wrong|"
    r"nobody talks about|they don'?t want you to know|insane|unbelievable|heartbreaking|"
    r"вы не поверите|шок\w*|жесть|ужас\w*|кошмар\w*|трагеди\w*|никто не говорит)\b",
    re.IGNORECASE | re.UNICODE)
_SHOCK_EMOJI = re.compile("[\U0001F631\U0001F6A8\U0001F480\U0001F92F‼❗]")

# YouTube's topicDetails names (stored as "Health"; a full Wikipedia URL works too)
_SENSITIVE_TOPICS = {"health": "health", "medicine": "health", "politics": "politics",
                     "law": "legal", "finance": "finance", "economics": "finance"}
_SENSITIVE_WORDS = {
    "health": r"doctor|symptom|disease|diet|cure|medical|health|врач|симптом|болезн|лечени|здоровь",
    "legal": r"lawyer|legal|lawsuit|court|sue\b|attorney|юрист|адвокат|суд\b|в суд|закон",
    "finance": r"invest|stock|crypto|bitcoin|tax|loan|mortgage|trading|инвест|акци|крипт|налог|кредит|ипотек",
    "politics": r"election|politic|president|senate|government|выбор|политик|президент|правительств",
}
_SENSITIVE_RE = {k: re.compile(v, re.IGNORECASE | re.UNICODE) for k, v in _SENSITIVE_WORDS.items()}


def shock_markers(title: str) -> list:
    t = title or ""
    found = [m.group(0).lower() for m in _SHOCK_PHRASES.finditer(t)]
    found += _SHOCK_EMOJI.findall(t)
    if _shouting(t):
        found.append("CAPS")
    return found


def _shouting(title: str) -> bool:
    """A capitalised word that is not an acronym: NASA stays NASA, WON'T BELIEVE shouts."""
    words = re.findall(r"[^\W\d_]+", title)
    upper = [w for w in words if len(w) >= 4 and w.isupper()]
    return len(upper) >= 2 or any(len(w) >= 7 for w in upper)


def shock_share(titles) -> tuple:
    titles = [t for t in titles or [] if (t or "").strip()]
    if not titles:
        return None, []
    hits = [t for t in titles if shock_markers(t)]
    return round(len(hits) / len(titles), 2), hits


def sensitive_topic(title: str, topic_categories) -> str | None:
    for c in topic_categories or []:
        key = (c or "").rstrip("/").rsplit("/", 1)[-1].replace("_", " ").strip().lower()
        if key in _SENSITIVE_TOPICS:
            return _SENSITIVE_TOPICS[key]
    for name, rx in _SENSITIVE_RE.items():
        if rx.search(title or ""):
            return name
    return None


def _cat(key, level, value=None, threshold=None, reasons=(), examples=()):
    return {"level": level, "value": value, "threshold": threshold, "reasons": list(reasons),
            "examples": list(examples)[:5], "policy": POLICY[key], "policyUrl": POLICY_URL}


def signals(template, thumb_similarity, videos, faceless) -> dict:
    """template: template_risk result (or None); thumb_similarity: mean
    cosine between the channel's own thumbnails (None when not embedded);
    videos: [{title, topicCategories, containsSyntheticMedia}] recent first;
    faceless: the channel's AI label (None when not labelled)."""
    titles = [v.get("title") for v in videos]
    enough = len([t for t in titles if t]) >= MIN_TITLES

    # 1. generic / repetitive
    lvl = (template or {}).get("level")
    if lvl in (None, "insufficient-data") and thumb_similarity is None:
        cat1 = _cat("generic_repetitive", "insufficient-data")
    else:
        # every reason is {signal, value, text}: the dashboard words it by `signal`
        reasons = [{"signal": r.get("signal"), "value": r.get("value"), "text": r.get("text")}
                   for r in (template or {}).get("reasons") or [] if isinstance(r, dict)]
        level = {"high": "high", "medium": "watch"}.get(lvl, "none")
        if thumb_similarity is not None and thumb_similarity >= THUMB_SIMILAR:
            reasons.append({"signal": "thumbSimilarity", "value": thumb_similarity,
                            "text": f"thumbnails look alike (mean similarity {thumb_similarity:.2f})"})
            level = "high" if level != "none" else "watch"
        cat1 = _cat("generic_repetitive", level, value=(template or {}).get("score"),
                    reasons=reasons)

    # 2. unsatisfying / shock
    share, hits = shock_share(titles)
    if not enough:
        cat2 = _cat("unsatisfying", "insufficient-data")
    else:
        level = "high" if share >= SHOCK_HIGH else "watch" if share >= SHOCK_WATCH else "none"
        cat2 = _cat("unsatisfying", level, value=share, threshold=SHOCK_WATCH,
                    reasons=[{"signal": "shockShare", "value": share,
                              "text": f"{round(share * 100)}% of recent titles use shock markers"}]
                    if hits else [],
                    examples=hits)

    # 3. AI persona on a sensitive topic -- at most "watch"
    if not enough:
        cat3 = _cat("ai_persona_sensitive", "insufficient-data")
    else:
        topics = [sensitive_topic(v.get("title"), v.get("topicCategories")) for v in videos]
        sens = [t for t in topics if t]
        sens_share = round(len(sens) / len(videos), 2)
        flagged = [v for v in videos if v.get("containsSyntheticMedia") is not None]
        synth_share = (round(sum(1 for v in flagged if v["containsSyntheticMedia"]) / len(flagged), 2)
                       if flagged else None)
        ai_sign = (synth_share is not None and synth_share >= SYNTHETIC_SHARE) or faceless is True
        reasons = []
        if sens:
            top = max(set(sens), key=sens.count)
            reasons.append({"signal": "sensitiveShare", "value": sens_share, "topic": top,
                            "text": f"{round(sens_share * 100)}% of recent videos on {top}"})
        if synth_share:
            reasons.append({"signal": "syntheticShare", "value": synth_share,
                            "text": f"{round(synth_share * 100)}% disclosed as synthetic media"})
        if faceless is True:
            reasons.append({"signal": "faceless", "value": True, "text": "labelled faceless"})
        level = "watch" if sens_share >= SENSITIVE_SHARE and ai_sign else "none"
        cat3 = _cat("ai_persona_sensitive", level, value=sens_share, threshold=SENSITIVE_SHARE,
                    reasons=reasons if level != "none" else [],
                    examples=[v.get("title") for v, t in zip(videos, topics) if t])
    return {"categories": {"generic_repetitive": cat1, "unsatisfying": cat2,
                           "ai_persona_sensitive": cat3},
            "note": "Signals visible from public data, not YouTube's decision -- monetization "
                    "reviews are done by people. Reused content is a separate policy."}

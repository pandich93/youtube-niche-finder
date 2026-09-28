"""How much a channel's recent uploads look like one template repeated --
the pattern YouTube's "inauthentic content" policy (mass-produced,
templated, little variation between videos) targets (plan 01). Pure
formulas, no DB, no network.

Four signals, each turned into a 0..1 sub-score by a linear ramp, then a
weighted mean. No single signal decides: a streamer or a podcast has very
similar titles but varied lengths and an irregular schedule, a conveyor has
all four. Thresholds come from the distribution over real channels in the
local database (title similarity of a multilingual MiniLM embedding sits
around 0.70 for an ordinary channel, above 0.90 for "Track Name (Genre)"
music channels), not from a labelled set of penalised channels -- there is
none -- so this is a heuristic, never YouTube's verdict.
"""
import re
import statistics as st
from datetime import datetime, timezone

import numpy as np

MIN_VIDEOS = 10        # fewer recent uploads than this -> "insufficient-data"
MIN_GAPS = 3           # cadence needs at least this many gaps between uploads
OPENING_WORDS = 3      # a shared "Top 10 facts ..." opening is compared on this many words
MIN_SHARED = 3         # a skeleton has to repeat at least this often to count

# (value where the sub-score is 0, value where it is 1); inverted for signals
# where LOW means templated.
SIM_RAMP = (0.55, 0.90)
SHARE_RAMP = (0.20, 0.70)
DURATION_CV_RAMP = (0.50, 0.10)
CADENCE_CV_RAMP = (1.00, 0.30)
WEIGHTS = {"similarity": 0.40, "templateShare": 0.30, "durationCv": 0.15, "cadenceCv": 0.15}
LOW_BELOW, HIGH_FROM = 35, 65
REASON_FROM = 0.6      # a sub-score at least this strong is reported as a reason

_SEP = re.compile(r"\s*[|–—:(\[]\s*|\s+-\s+")
_WORD = re.compile(r"[\w']+", re.UNICODE)


def _dt(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def title_self_similarity(vectors):
    """Mean pairwise cosine similarity of the title embeddings; None with
    fewer than two vectors."""
    vs = [np.asarray(v, dtype=np.float32) for v in vectors or []]
    if len(vs) < 2:
        return None
    m = np.stack(vs)
    m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
    n = len(vs)
    sims = m @ m.T
    return round(float((sims.sum() - n) / (n * (n - 1))), 4)


def _skeleton_keys(title):
    """(opening, tail) of a title. The opening is its first few words; the
    tail is what follows the last separator ('|', dash, ':', an opening
    bracket) -- the "| Afro House 2026" / "(Melodic Deep House)" part."""
    t = (title or "").strip().lower()
    words = _WORD.findall(t)
    opening = " ".join(words[:OPENING_WORDS]) if words else None
    parts = [p for p in _SEP.split(t) if p.strip()]
    tail = None
    if len(parts) > 1:
        tail = " ".join(_WORD.findall(parts[-1])) or None
    return opening, tail


def title_template_share(titles):
    """Share of titles whose opening or tail also appears in at least
    MIN_SHARED titles of the set -- "Top 10 facts about X" x N, or
    "Name | Afro House 2026" x N. None without titles."""
    titles = [t for t in titles or [] if (t or "").strip()]
    if not titles:
        return None
    keys = [_skeleton_keys(t) for t in titles]
    openings = [k[0] for k in keys if k[0]]
    tails = [k[1] for k in keys if k[1]]
    shared = 0
    for opening, tail in keys:
        if (opening and openings.count(opening) >= MIN_SHARED) or \
                (tail and tails.count(tail) >= MIN_SHARED):
            shared += 1
    return round(shared / len(titles), 4)


def duration_uniformity(durations):
    """Coefficient of variation of video lengths (0 = every video the same
    length); None with fewer than two videos or no length data."""
    ds = [d for d in durations or [] if d is not None]
    if len(ds) < 2 or not st.mean(ds):
        return None
    return round(st.pstdev(ds) / st.mean(ds), 4)


def cadence_regularity(published_at):
    """Coefficient of variation of the gaps between uploads (0 = a
    metronome, high = bursts and silences); None without MIN_GAPS gaps."""
    ds = sorted(d for d in (_dt(x) for x in published_at or []) if d)
    gaps = [(b - a).total_seconds() / 86400 for a, b in zip(ds, ds[1:])]
    if len(gaps) < MIN_GAPS or not st.mean(gaps):
        return None
    return round(st.pstdev(gaps) / st.mean(gaps), 4)


def _ramp(x, zero_at, one_at):
    if x is None:
        return None
    return max(0.0, min(1.0, (x - zero_at) / (one_at - zero_at)))


def _reason(signal, value, sub):
    text = {
        "similarity": f"titles are very alike (mean similarity {value:.2f})",
        "templateShare": f"{value:.0%} of titles reuse the same opening or ending",
        "durationCv": f"video lengths barely vary (variation {value:.2f})",
        "cadenceCv": f"uploads come at a near-constant rhythm (gap variation {value:.2f})",
    }[signal]
    return {"signal": signal, "value": value, "strength": round(sub, 2), "text": text}


DISCLAIMER = ("a heuristic over public upload patterns, not YouTube's verdict; "
              "legitimate series, podcasts and music channels can score high")


def template_risk_score(signals: dict) -> dict:
    """signals: {"similarity", "templateShare", "durationCv", "cadenceCv",
    "videos"} (any signal may be None). Returns {score 0-100, level
    low|medium|high, reasons, note}; with fewer than MIN_VIDEOS videos, or
    without the title-similarity signal, level "insufficient-data" and no
    score. Signals that are None are dropped and the remaining weights
    renormalised."""
    videos = signals.get("videos") or 0
    if videos < MIN_VIDEOS or signals.get("similarity") is None:
        return {"score": None, "level": "insufficient-data", "reasons": [],
                "note": f"needs at least {MIN_VIDEOS} recent videos with embedded titles "
                        f"(has {videos})"}
    ramps = {"similarity": SIM_RAMP, "templateShare": SHARE_RAMP,
             "durationCv": DURATION_CV_RAMP, "cadenceCv": CADENCE_CV_RAMP}
    subs = {k: _ramp(signals.get(k), *r) for k, r in ramps.items()}
    used = {k: v for k, v in subs.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in used)
    score = round(100 * sum(WEIGHTS[k] * v for k, v in used.items()) / total_w)
    level = "low" if score < LOW_BELOW else "medium" if score < HIGH_FROM else "high"
    reasons = [_reason(k, signals[k], v) for k, v in
               sorted(used.items(), key=lambda kv: -WEIGHTS[kv[0]] * kv[1]) if v >= REASON_FROM]
    return {"score": score, "level": level, "reasons": reasons, "note": DISCLAIMER}

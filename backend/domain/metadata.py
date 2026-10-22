"""Pure signal formulas for the metadata-review assistant (plan item 8.8).

Deliberately NOT a single opaque score. Each signal answers one narrow,
falsifiable question against a baseline computed from the user's own corpus,
and every signal carries the sample size behind it -- application.
metadata_review marks anything under MIN_RELIABLE_SAMPLE as "unreliable" so a
thin corpus never quietly moves the summary.

What this module explicitly does NOT attempt: CTR, impressions, watch time,
or any prediction of how the draft will perform. Those need the YouTube
Analytics API with OAuth on your own channel (docs/plan-iteration-8.md,
section 8.10) and are not something a Data API v3 corpus can honestly
estimate.
"""
import re

from domain.i18n import tr

MIN_RELIABLE_SAMPLE = 20
VISIBLE_TITLE_CHARS = 45          # roughly what search/mobile shows before truncating
VISIBLE_DESCRIPTION_CHARS = 150   # shown before "...more" on the watch page
YOUTUBE_TAGS_CHAR_LIMIT = 500

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)
CAPS_WORD_RE = re.compile(r"(?<![\wА-ЯЁ])[A-ZА-ЯЁ]{3,}(?![\wа-яё])")
TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b")

FEATURE_LABELS = {
    "has_digit": "цифра в заголовке",
    "has_brackets": "скобки в заголовке",
    "has_question": "вопрос в заголовке",
    "has_caps_word": "слово КАПСОМ",
    "has_emoji": "эмодзи в заголовке",
}


def percentiles(values) -> dict:
    """Median/p25/p75 by nearest-rank -- no numpy/scipy needed for this."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)

    def pct(p):
        idx = min(n - 1, max(0, round(p * (n - 1))))
        return vals[idx]

    return {"median": pct(0.5), "p25": pct(0.25), "p75": pct(0.75), "sample": n}


def length_signal(signal_id: str, value: int, baseline_values, label: str) -> dict:
    """Generic 'is this length inside the p25-p75 band of the baseline?'
    check, shared by title length and description length."""
    stats = percentiles(baseline_values)
    if not stats or stats["sample"] < MIN_RELIABLE_SAMPLE:
        sample = stats["sample"] if stats else 0
        return {
            "id": signal_id, "value": value, "baseline": stats, "verdict": "unreliable",
            "explanation": tr("{label}: {value} симв.; в базе только {sample} выбросов ниши "
                              "для сравнения (нужно {min}+)",
                              label=tr(label), value=value, sample=sample, min=MIN_RELIABLE_SAMPLE),
        }
    verdict = "ok" if stats["p25"] <= value <= stats["p75"] else "warn"
    return {
        "id": signal_id, "value": value, "baseline": stats, "verdict": verdict,
        "explanation": tr("{label}: {value} симв. против медианы {median} "
                          "(p25-p75: {p25}-{p75}) у выбросов ниши, выборка {sample}",
                          label=tr(label), value=value, median=round(stats["median"]),
                          p25=round(stats["p25"]), p75=round(stats["p75"]), sample=stats["sample"]),
    }


def visible_prefix_signal(title: str, key_phrase: str = None) -> dict:
    """Does anything meaningful sit in the ~45 chars mobile/search actually
    shows before truncating? A structural check, no baseline needed."""
    title = title or ""
    prefix = title[:VISIBLE_TITLE_CHARS]
    truncated = len(title) > VISIBLE_TITLE_CHARS
    hit = bool(key_phrase) and key_phrase.lower() in prefix.lower()
    verdict = "ok" if (not truncated or not key_phrase or hit) else "warn"
    explanation = tr("первые {n} символов: «{prefix}»", n=VISIBLE_TITLE_CHARS, prefix=prefix)
    if key_phrase:
        explanation += (tr(" -- фраза «{phrase}» попадает в видимую часть", phrase=key_phrase) if hit
                        else tr(" -- фраза «{phrase}» НЕ попадает в видимую часть", phrase=key_phrase))
    return {"id": "visible_prefix", "value": prefix, "truncated": truncated,
            "verdict": verdict, "explanation": explanation}


def structural_features(title: str) -> dict:
    t = title or ""
    return {
        "has_digit": bool(re.search(r"\d", t)),
        "has_brackets": bool(re.search(r"[\[(].+[\])]", t)),
        "has_question": "?" in t,
        "has_caps_word": bool(CAPS_WORD_RE.search(t)),
        "has_emoji": bool(EMOJI_RE.search(t)),
    }


def structural_lift(rows, outlier_threshold: float = 3.0) -> list:
    """rows: [{"title": str, "outlier": float|None}]. lift = P(outlier |
    feature present) / P(outlier) -- same idea as domain.keywords' phrase
    lift, applied to five yes/no title features instead of n-grams."""
    total = len(rows)
    if not total:
        return [{"feature": f, "label": tr(lbl), "lift": None, "sample": 0,
                 "verdict": "unreliable"} for f, lbl in FEATURE_LABELS.items()]
    base_hits = sum(1 for r in rows if (r.get("outlier") or 0) >= outlier_threshold)
    base_rate = base_hits / total
    out = []
    for feature, label in FEATURE_LABELS.items():
        label = tr(label)
        with_feature = [r for r in rows if structural_features(r.get("title"))[feature]]
        n = len(with_feature)
        if n < MIN_RELIABLE_SAMPLE or base_rate <= 0:
            out.append({"feature": feature, "label": label, "lift": None,
                       "sample": n, "verdict": "unreliable"})
            continue
        hits = sum(1 for r in with_feature if (r.get("outlier") or 0) >= outlier_threshold)
        out.append({"feature": feature, "label": label,
                   "lift": round((hits / n) / base_rate, 2), "sample": n, "verdict": "ok"})
    return out


def tag_overlap(draft_tags, trending_phrases) -> dict:
    """draft_tags: list[str] as the creator would type them. trending_phrases:
    already-ranked phrase strings (best first), e.g. from
    application.discovery.trending_keywords()["keywords"]."""
    draft_norm = {t.strip().lower() for t in (draft_tags or []) if t and t.strip()}
    trending_norm = [p.strip().lower() for p in (trending_phrases or []) if p]
    matched = [p for p in trending_norm if p in draft_norm]
    missing = [p for p in trending_norm if p not in draft_norm][:10]
    total_chars = sum(len(t) for t in (draft_tags or []))
    over_limit = total_chars > YOUTUBE_TAGS_CHAR_LIMIT
    explanation = tr("{tags} тегов, {chars}/{limit} символов", tags=len(draft_tags or []),
                     chars=total_chars, limit=YOUTUBE_TAGS_CHAR_LIMIT)
    if over_limit:
        explanation += tr(" -- ПРЕВЫШЕН лимит YouTube, лишние теги будут отброшены")
    if trending_norm:
        explanation += tr("; из {trending} трендовых фраз ниши совпало {matched}",
                          trending=len(trending_norm), matched=len(matched))
    return {
        "id": "tags", "matchedTrending": matched, "missingTrending": missing,
        "tagCount": len(draft_tags or []), "totalChars": total_chars,
        "verdict": "warn" if over_limit else "ok", "explanation": explanation,
    }


def description_signal(description: str, key_phrase: str = None) -> dict:
    desc = description or ""
    prefix = desc[:VISIBLE_DESCRIPTION_CHARS]
    has_timestamps = bool(TIMESTAMP_RE.search(desc))
    hit = bool(key_phrase) and key_phrase.lower() in prefix.lower()
    if not desc:
        verdict = "unreliable"
    elif key_phrase and not hit:
        verdict = "warn"
    else:
        verdict = "ok"
    explanation = tr("{n} симв.", n=len(desc))
    if key_phrase:
        explanation += (tr("; фраза «{phrase}» есть в первых {n} символах (видно до «ещё»)",
                           phrase=key_phrase, n=VISIBLE_DESCRIPTION_CHARS) if hit
                        else tr("; фраза «{phrase}» отсутствует в первых {n} символах (видно до «ещё»)",
                                phrase=key_phrase, n=VISIBLE_DESCRIPTION_CHARS))
    explanation += tr("; таймкоды есть") if has_timestamps else tr("; таймкодов нет")
    return {"id": "description", "length": len(desc), "visiblePrefix": prefix,
            "hasTimestamps": has_timestamps, "verdict": verdict, "explanation": explanation}


def summarize(signals) -> dict:
    ok = sum(1 for s in signals if s.get("verdict") == "ok")
    warn = sum(1 for s in signals if s.get("verdict") == "warn")
    unreliable = sum(1 for s in signals if s.get("verdict") == "unreliable")
    return {"ok": ok, "warn": warn, "unreliable": unreliable, "total": len(signals)}

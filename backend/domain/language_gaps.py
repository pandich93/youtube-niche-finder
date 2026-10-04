"""Language gaps (plan 26): a video that is an outlier in one language -- is
there anything like it in another? Pure, no DB.

For one source video (an outlier in the source language) we get its nearest
videos in the target language, each with its outlier score. Verdicts:

  open     nothing in the target language reads close to it
  thin     something does, but none of it is an outlier
  covered  at least one close video in the target language is an outlier

"Open" says "not in the videos we collected", not "does not exist on YouTube":
the target corpus can simply be thin (see corpus_hint). An estimate of
niche-finder over the corpus we collected, not YouTube data.
"""
import re

HIT_SCORE = 2.0
MIN_SIMILARITY = 0.62      # multilingual MiniLM: cross-language cosine runs lower than within a language
SOURCE_MIN_SIMILARITY = 0.6  # within one language, same as repeatability (plan 21)
THIN_CORPUS_VIDEOS = 200
MAX_MATCHES = 3

# YouTube's placeholders for "no language": und = undetermined, zxx = no
# linguistic content, mul = several languages
_NO_LANGUAGE = {"und", "zxx", "mul", "un", "zx", "mu", "zz", "xx"}

# the words after "на": the codes we meet most
_NAMES = {
    "en": "английском языке", "ru": "русском языке", "uz": "узбекском языке",
    "es": "испанском языке", "hi": "хинди", "tr": "турецком языке", "ko": "корейском языке",
    "ar": "арабском языке", "pt": "португальском языке", "de": "немецком языке",
    "fr": "французском языке", "ja": "японском языке", "id": "индонезийском языке",
    "it": "итальянском языке", "pl": "польском языке", "uk": "украинском языке",
    "kk": "казахском языке", "vi": "вьетнамском языке", "th": "тайском языке",
    "zh": "китайском языке", "fa": "персидском языке", "bn": "бенгальском языке",
    "ur": "урду",
}

_RANK = {"open": 0, "thin": 1, "covered": 2}


def normalize_lang(code) -> str | None:
    """'en-US' / 'en_GB' / 'EN' -> 'en'; 'und', 'zxx', '' and junk -> None."""
    if not code:
        return None
    base = re.split(r"[-_]", str(code).strip().lower())[0]
    if not base or base in _NO_LANGUAGE or not re.fullmatch(r"[a-z]{2,3}", base):
        return None
    return base


def lang_label(code: str) -> str:
    """The words after "на" for a normalized code: 'русском языке'."""
    return _NAMES.get(code) or f"языке «{code}»"


def verdict(neighbours, hit_score: float = HIT_SCORE, min_similarity: float = MIN_SIMILARITY) -> dict:
    """neighbours: target-language videos nearest to the source, best first:
    [{videoId, similarity, outlierScore, ...}]. Only those at or above
    min_similarity count as 'similar'; `nearest` is the closest one either way,
    so an 'open' verdict shows how close the closest thing is."""
    ordered = sorted(neighbours or [], key=lambda n: n["similarity"], reverse=True)
    close = [n for n in ordered if n["similarity"] >= min_similarity]
    hit = [n for n in close if (n.get("outlierScore") or 0) >= hit_score]
    if hit:
        v = "covered"
    elif close:
        v = "thin"
    else:
        v = "open"
    return {"verdict": v, "matches": close[:MAX_MATCHES],
            "nearest": ordered[0] if ordered else None, "similarCount": len(close)}


def demand(own_score, same_language_neighbours, hit_score: float = HIT_SCORE) -> dict:
    """How strongly the format works in the source language: the source's own
    multiplier and how many OTHER channels got an outlier with something
    similar (a channel counts once)."""
    channels = {n["channelId"] for n in same_language_neighbours or []
                if (n.get("outlierScore") or 0) >= hit_score}
    return {"score": own_score, "otherChannelsHit": len(channels)}


def sort_key(card: dict):
    """Open first, then thin, then covered; inside a verdict the stronger
    demand first (more other channels that repeated it, then the multiplier)."""
    d = card["demand"]
    return (_RANK[card["verdict"]], -d["otherChannelsHit"], -(d["score"] or 0))


def corpus_hint(videos: int, lang: str) -> str | None:
    """Words for a thin target corpus; None when it is big enough."""
    if videos >= THIN_CORPUS_VIDEOS:
        return None
    return (f"В базе только {videos} видео на {lang_label(lang)}: «открыто» может значить "
            "«не собрано». Соберите каналы этой ниши на этом языке и проверьте снова.")

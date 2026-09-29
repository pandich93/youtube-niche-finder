"""Pure rules for content gaps (plan 03): viewer questions and requests from a
niche's comments that no collected video answers yet. No DB, no network.

extract_questions is the no-LLM path: a comment is kept when it asks something
("?") or requests a video with an explicit marker ("please make", "сделайте
видео"), and dropped when it is praise, spam, a link or a timestamp (a question
about the video it sits under, not a new topic). Quality is lower than the LLM
path, especially outside English and Russian -- application/content_gaps.py
says so in its answer.

Only the text and like count survive: the author is never kept.
"""
import html
import math
import re

MIN_WORDS = 4
MAX_CHARS = 400
# Cosine at which two questions ask the same thing. Measured on the project's
# multilingual MiniLM: paraphrases 0.68-0.92 (a Russian/English pair 0.77),
# but different topics in one frame ("what if the sun / the moon
# disappeared") already 0.68 -- so the cut sits just above that.
CLUSTER_AT = 0.72
# Coverage is the best cosine between a question and a collected video title or
# transcript chunk. check_ideas already calls >= 0.55 "covers it", so the same
# number is where a gap stops being free; 0.65 and up is a close match.
PARTIAL_AT = 0.55
COVERED_AT = 0.65

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_URL = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|ru|org|net|io|me|ly)/", re.I)
_TIMESTAMP = re.compile(r"\b\d{1,2}:\d{2}\b")
_PUNCT = re.compile(r"[^\w\s]", re.U)

# A marker alone is enough: these are requests, question mark or not.
_REQUEST = re.compile(
    r"\b(?:please (?:make|do|cover)|can you (?:make|do|cover|show|explain)|"
    r"could you (?:make|do|cover|show|explain)|(?:make|do) a video|video (?:on|about)|"
    r"tutorial (?:on|about|for)|part 2)\b"
    r"|(?<!\w)(?:сделайте|сделай|снимите|сними|расскажите|расскажи|объясните|объясни|"
    r"покажите|покажи|можно видео|видео про|ролик про|разбор)(?!\w)",
    re.I | re.U)
_SPAM = re.compile(
    r"\b(?:who(?:'s| is) (?:watching|here)|anyone (?:watching|here)|first\b|"
    r"check out my|subscribe to my|my channel)\b"
    r"|(?<!\w)(?:кто (?:смотрит|здесь|тут)|подпишись|мой канал|на моём канале|на моем канале)(?!\w)",
    re.I | re.U)


def clean_text(text) -> str:
    text = html.unescape(text or "").replace("\xa0", " ")
    return _SPACE.sub(" ", _TAG.sub(" ", text)).strip()


def _normalised(text: str) -> str:
    return _SPACE.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def is_question(text: str) -> bool:
    if len(text.split()) < MIN_WORDS or len(text) > MAX_CHARS:
        return False
    if _URL.search(text) or _TIMESTAMP.search(text) or _SPAM.search(text):
        return False
    return "?" in text or bool(_REQUEST.search(text))


def extract_questions(comments) -> list:
    """[{text, likeCount}] for the comments that ask or request something,
    most-liked copy kept when the same question repeats."""
    best = {}
    for cm in comments or []:
        text = clean_text((cm or {}).get("text"))
        if not text or not is_question(text):
            continue
        likes = int((cm or {}).get("likeCount") or 0)
        key = _normalised(text)
        if key not in best or likes > best[key]["likeCount"]:
            best[key] = {"text": text, "likeCount": likes}
    return list(best.values())


def cosine(a, b) -> float:
    na, nb = float((a ** 2).sum()) ** 0.5, float((b ** 2).sum()) ** 0.5
    return float((a * b).sum()) / (na * nb) if na and nb else 0.0


def cluster_questions(items, vectors=None, threshold: float = CLUSTER_AT) -> list:
    """Groups of indices into items that ask the same thing. Greedy: each item
    joins the first group whose first member is within `threshold` cosine.
    Without vectors, only questions with the same normalised text group."""
    groups = []
    if vectors is None:
        by_key = {}
        for i, it in enumerate(items):
            by_key.setdefault(_normalised(it.get("text") or ""), []).append(i)
        return list(by_key.values())
    for i, v in enumerate(vectors):
        for g in groups:
            if cosine(vectors[g[0]], v) >= threshold:
                g.append(i)
                break
        else:
            groups.append([i])
    return groups


def demand(items, sources: int = 1) -> float:
    """How loudly viewers ask: every asker counts once plus their likes, on a
    log scale so one viral comment does not drown the rest, and a question
    heard under several videos weighs more than one heard under one."""
    base = math.log1p(sum(int(it.get("likeCount") or 0) + 1 for it in items))
    return round(base * (1 + 0.5 * max(sources - 1, 0)), 3)


def gap_status(coverage) -> str:
    if coverage is None or coverage < PARTIAL_AT:
        return "free"
    return "covered" if coverage >= COVERED_AT else "partial"


def gap_score(demand_value: float, coverage) -> float:
    return round(demand_value * (1 - min(max(coverage or 0.0, 0.0), 1.0)), 3)

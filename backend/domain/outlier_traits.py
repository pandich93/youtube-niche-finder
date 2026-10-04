"""What outliers of a niche have in common (plan 29): numbers, no LLM. Pure
functions, no DB.

Videos are split by format first -- Shorts and long-form are never mixed --
and inside a format into OUTLIERS (age-adjusted score >= OUTLIER_MIN) and
ORDINARY videos (<= ORDINARY_MAX); the ones in between are left out so the
two groups are clearly apart. For each feature we compare the groups:

  share    a yes/no feature (a number in the title, a "?", published on a
           weekend ...): share of videos with it, in percentage points;
  median   a number (length of the video or of the title, tags): the median,
           as a ratio.

A feature is called "significant" only when both groups have at least
MIN_GROUP videos and the gap is at least MIN_DIFF_PP points (or the medians
are MIN_RATIO apart). With a dozen features checked, one or two will pass by
chance in a small niche: the sample sizes are shown next to every number, and `concentrated` says
when more than half of the outliers come from one channel (its habits, not
the niche's).
A correlation with outliers, never a cause; an estimate of niche-finder over
the videos we collected, not YouTube data. Times are UTC.
"""
import re
import statistics as st
from datetime import datetime, timezone

OUTLIER_MIN = 3.0
ORDINARY_MAX = 1.5
MIN_GROUP = 10
MIN_DIFF_PP = 15
MIN_RATIO = 1.3
CONCENTRATED_SHARE = 0.5   # one channel behind more than this share of the outliers

_DIGIT = re.compile(r"\d")
_BRACKET = re.compile(r"[()\[\]]")
_CAPS_WORD = re.compile(r"\b[A-ZА-ЯЁ]{3,}\b")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⭕]")

def _when(v, test):
    """The test on the publish time; None (not "no") when the time is unknown."""
    return None if v["published"] is None else test(v["published"])


# (key, label, kind, extractor over a video dict)
FEATURES = [
    ("number", "Число в заголовке", "share", lambda v: bool(_DIGIT.search(v["title"]))),
    ("question", "Вопрос в заголовке", "share", lambda v: "?" in v["title"]),
    ("brackets", "Скобки в заголовке", "share", lambda v: bool(_BRACKET.search(v["title"]))),
    ("caps", "Слово ЗАГЛАВНЫМИ", "share", lambda v: bool(_CAPS_WORD.search(v["title"]))),
    ("emoji", "Эмодзи в заголовке", "share", lambda v: bool(_EMOJI.search(v["title"]))),
    ("weekend", "Публикация в выходные", "share", lambda v: _when(v, lambda d: d.weekday() >= 5)),
    ("night", "Публикация 00–05 UTC", "share", lambda v: _when(v, lambda d: d.hour < 6)),
    ("morning", "Публикация 06–11 UTC", "share", lambda v: _when(v, lambda d: 6 <= d.hour < 12)),
    ("afternoon", "Публикация 12–17 UTC", "share", lambda v: _when(v, lambda d: 12 <= d.hour < 18)),
    ("evening", "Публикация 18–23 UTC", "share", lambda v: _when(v, lambda d: d.hour >= 18)),
    ("duration", "Длина видео, секунды", "median", lambda v: v["duration"]),
    ("titleLength", "Длина заголовка, символы", "median", lambda v: len(v["title"])),
    ("tags", "Число тегов", "median", lambda v: v["tags"]),
]


def parse_time(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def video(title, score, duration=None, published=None, tags=None, channel=None) -> dict:
    """The shape the features read: published is an ISO string or datetime."""
    return {"title": title or "", "score": score, "duration": duration, "channel": channel,
            "published": published if isinstance(published, datetime) else parse_time(published),
            "tags": tags}


def _share(values):
    return round(100 * sum(1 for x in values if x) / len(values), 1) if values else None


def compare(videos, outlier_min=OUTLIER_MIN, ordinary_max=ORDINARY_MAX) -> dict:
    """videos: one format's videos from video(). Returns the two groups'
    sizes and, per feature, both values, the gap and whether it is significant."""
    scored = [v for v in videos if v["score"] is not None]
    out = [v for v in scored if v["score"] >= outlier_min]
    reg = [v for v in scored if v["score"] <= ordinary_max]
    by_channel = {}
    for v in out:
        by_channel[v["channel"]] = by_channel.get(v["channel"], 0) + 1
    top = max(by_channel.values()) / len(out) if out else 0
    res = {"outliers": len(out), "ordinary": len(reg), "reliable": False, "traits": [],
           "outlierChannels": len(by_channel), "topChannelShare": round(top, 2),
           "concentrated": top > CONCENTRATED_SHARE}
    if len(out) < MIN_GROUP or len(reg) < MIN_GROUP:
        res["reason"] = "few-videos"
        return res
    res["reliable"] = True
    for key, label, kind, get in FEATURES:
        a = [x for x in (get(v) for v in out) if x is not None]
        b = [x for x in (get(v) for v in reg) if x is not None]
        t = {"key": key, "label": label, "kind": kind, "nOutliers": len(a), "nOrdinary": len(b),
             "outliers": None, "ordinary": None, "diff": None, "direction": None,
             "significant": False}
        if len(a) >= MIN_GROUP and len(b) >= MIN_GROUP:
            if kind == "share":
                t["outliers"], t["ordinary"] = _share(a), _share(b)
                t["diff"] = round(t["outliers"] - t["ordinary"], 1)          # percentage points
                t["significant"] = abs(t["diff"]) >= MIN_DIFF_PP
            else:
                t["outliers"], t["ordinary"] = round(st.median(a), 1), round(st.median(b), 1)
                if t["ordinary"]:
                    t["diff"] = round(t["outliers"] / t["ordinary"], 2)       # ratio of medians
                    t["significant"] = t["diff"] >= MIN_RATIO or t["diff"] <= 1 / MIN_RATIO
            neutral = 0 if kind == "share" else 1
            if t["diff"] is not None and t["diff"] != neutral:
                t["direction"] = "more" if t["diff"] > neutral else "less"
        res["traits"].append(t)
    res["traits"].sort(key=lambda t: (not t["significant"], -abs(_strength(t))))
    return res


def _strength(t):
    """How far apart the groups are, comparable across kinds: points for
    shares, the ratio's distance from 1 (as points) for medians."""
    if t["diff"] is None:
        return 0
    if t["kind"] == "share":
        return t["diff"]
    return (t["diff"] - 1) * 100 if t["diff"] >= 1 else (1 / t["diff"] - 1) * 100 if t["diff"] else 0

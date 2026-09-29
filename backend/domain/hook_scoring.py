"""Plan 10: how strong the first ~30 seconds of a video read, judged from the
transcript text alone. Pure rules, no DB, no network.

Seven features (question, number, promise, intrigue, secondPerson,
firstSentence, pace) earn points out of 100; two kinds of filler (a greeting
opening the text, "welcome back / subscribe" phrases) take points away. The
rules are regexes over English and Russian text, written by hand from common
hook patterns -- they were not fitted to a labelled set of videos, so the
score is a heuristic about the WORDS of the intro. It says nothing about the
picture, the editing or the sound, and a feature being more common among a
niche's outliers is a correlation, never a cause.
"""
import re

SECONDS = 30
UNTIMED_WORDS = 75          # without timestamps the hook is the first N words
MAX_TIMED_WORDS = 110       # a single timestamp may cover the whole video: cut it
LATE_START_SEC = 5          # a transcript that starts later than this is flagged
LEVEL_WEAK_BELOW = 35
LEVEL_STRONG_FROM = 65
MIN_OUTLIERS = 10           # a niche benchmark needs this many hooks per group
MIN_REGULAR = 10
MIN_DIFF_PP = 20            # percentage points between the two groups to call a feature "more common"
GREETING_PENALTY = -8
FILLER_PENALTY_EACH = -5
FILLER_PENALTY_CAP = -10
MAX_TIPS = 3

DISCLAIMER = ("a score of the intro's text, not of its visuals or editing; "
              "a correlation with outliers, not a cause")

MAX_POINTS = {"question": 15, "number": 20, "promise": 20, "intrigue": 20,
              "secondPerson": 10, "firstSentence": 10, "pace": 5}
FEATURES = tuple(MAX_POINTS)

_WORD = re.compile(r"[\w'-]+", re.UNICODE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CYRILLIC = re.compile(r"[а-яё]", re.I)
_LATIN = re.compile(r"[a-z]", re.I)


def normalize(text) -> str:
    """casefold, ё -> е, typographic apostrophe -> ', whitespace collapsed."""
    t = (text or "").casefold().replace("ё", "е").replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", t).strip()


def detect_language(text) -> str:
    """"ru" when more than 30% of the letters are Cyrillic, else "en"."""
    cyr = len(_CYRILLIC.findall(text or ""))
    lat = len(_LATIN.findall(text or ""))
    total = cyr + lat
    return "ru" if total and cyr / total > 0.30 else "en"


def _words(text) -> list:
    return _WORD.findall(text or "")


def level_for(score: int) -> str:
    return "weak" if score < LEVEL_WEAK_BELOW else "ok" if score < LEVEL_STRONG_FROM else "strong"


# ------------------------------------------------------------------ hook text

def hook_text(segments, seconds: int = SECONDS, untimed_words: int = UNTIMED_WORDS,
              max_words: int = MAX_TIMED_WORDS) -> dict:
    """The part of a parsed transcript that counts as the hook.

    Timed: the segments that start inside `seconds` of the first one, cut to
    `max_words`. Untimed: the first `untimed_words` words. spanSec is how
    long the taken text lasted (the start of the first segment past the
    threshold minus the first start); None when that is unknown -- untimed
    text, a transcript that ends inside the window, or a text that had to be
    cut, whose word count no longer matches the time span."""
    segs = [s for s in segments or [] if (s.get("text") or "").strip()]
    timed = [s for s in segs if s.get("startSec") is not None]
    if not timed:
        words = " ".join(s["text"] for s in segs).split()
        return {"text": " ".join(words[:untimed_words]), "mode": "untimed",
                "words": min(len(words), untimed_words), "startsAtSec": None,
                "spanSec": None, "warning": None}

    timed.sort(key=lambda s: s["startSec"])
    first = timed[0]["startSec"]
    threshold = first + seconds
    taken = [s for s in timed if s["startSec"] < threshold]
    later = next((s["startSec"] for s in timed if s["startSec"] >= threshold), None)
    words = " ".join(s["text"] for s in taken).split()
    span = None if later is None else later - first
    if len(words) > max_words:
        words, span = words[:max_words], None
    warning = None
    if first > LATE_START_SEC:
        warning = (f"the transcript starts at {first // 60}:{first % 60:02d}, not at 0:00 -- "
                   "the scored text may not be the real opening of the video")
    return {"text": " ".join(words), "mode": "timed", "words": len(words),
            "startsAtSec": first, "spanSec": span, "warning": warning}


# ---------------------------------------------------------------------- rules

def _rx(*alts, flags=re.I):
    return re.compile("|".join(alts), flags)


_Q_WORD = _rx(
    r"\bhave you ever\b", r"\bdid you ever\b", r"\bdo you\b", r"\bdid you know\b",
    r"\bare you\b", r"\bcan you\b", r"\bwould you\b", r"\bwhat if\b",
    r"\bwhy (?:is|are|do|does|did|most|every|no)\b",
    r"\bhow (?:do|does|did|can|would|much|many|come)\b",
    r"\bwhat (?:is|are|would|do|happens)\b", r"\bever wonder(?:ed)?\b", r"\bimagine\b",
    r"\bа вы\b", r"\bты когда-нибудь\b", r"\bвы когда-нибудь\b", r"\bты знаешь\b",
    r"\bвы знаете\b", r"\bзнаешь ли\b", r"\bпочему\b", r"\bзачем\b",
    r"\bкак (?:ты|вы|так|же|много)\b", r"\bсколько\b", r"\bчто если\b",
    r"\bпредстав(?:ь|ьте)\b")

_YEAR = re.compile(r"\b(?:19|20)\d\d\b(?:\s*(?:года|году|г\b\.?))?")
_TIMECODE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_EN_UNITS = (r"(?:percent|dollars?|days?|weeks?|months?|years?|hours?|minutes?|views?|"
             r"subscribers?|videos?|people|times|ways|things|tips|steps|mistakes|reasons|"
             r"secrets|rules|lessons)\b")
_RU_UNITS = (r"(?:процент|руб|долл|дн|недел|месяц|лет|год|час|минут|секунд|просмотр|подписчик|"
             r"тыс|млн|миллион|раз|способ|шаг|ошибк|совет|причин|секрет|правил|урок|человек|люд)")
_NUM = r"\d+(?:[ .,]\d+)*"
_EN_NUMWORD = (r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|"
               r"forty|fifty|hundred|thousand|million|billion)")
_RU_NUMWORD = (r"(?:миллион\w*|тысяч\w*|сто|два|две|три|четыре|пять|шесть|семь|восемь|девять|"
               r"десять|двадцать|пятьдесят)")
_NUMBER_STRONG = _rx(
    r"[$€£]\s?\d[\d.,]*", r"\d[\d.,]*\s?[$€£₽]",
    rf"{_NUM}\s*%", rf"{_NUM}\s*{_EN_UNITS}", rf"{_NUM}\s*{_RU_UNITS}",
    rf"\b{_EN_NUMWORD}\s+{_EN_UNITS}",
    rf"\b{_RU_NUMWORD}\s+(?:рубл|долл|способ|ошиб|причин|шаг|совет|секрет|дн|месяц|лет)",
    r"\bполгода\b")
_NUMBER_BARE = re.compile(r"\d+")

_PROMISE_STRONG = _rx(
    r"\bby the end(?: of (?:this|the) video)?\b",
    r"\byou(?:'ll| will| can| are going to|'re going to)\s+(?:\w+\s+)?"
    r"(?:learn|know|see|discover|understand|be able)\b",
    r"\bfind out (?:how|why|what)\b",
    r"\bв конце (?:этого )?видео\b",
    r"\b(?:ты узнаешь|вы узнаете|поймешь|поймете|научишься|научитесь|увидишь|увидите)\b")
_PROMISE_MEDIUM = _rx(
    r"\bin this video\b", r"\bi(?:'ll| will| am going to|'m going to)\s+(?:show|teach|explain|reveal)\b",
    r"\bhere(?: is|'s)\s+(?:how|what|why|the (?:\w+ )?thing)\b", r"\bhow to\b",
    r"\bв этом видео\b", r"\bя (?:покажу|расскажу|объясню|научу)\b", r"\bразберем\b",
    r"\bвот (?:как|почему|что)\b",
    r"\bкак (?:сделать|получить|заработать|выбрать|начать)\b")

# one entry per distinct idea: repeating a word never counts twice
_INTRIGUE = {
    "secret": r"\bsecrets?\b|\bсекрет\w*",
    "nobody": r"\b(?:nobody|no one) (?:tells|talks|knows)\b|\bникто не (?:говорит|расскажет|знает)\b",
    "neverTold": r"\bnever told\b",
    "truth": r"\bthe truth\b|\bправд[ау] о\b",
    "realReason": r"\breal reason\b|\bнастоящая причина\b",
    "mistake": r"\bbiggest mistake\b|\bmistakes?\b|\bглавная ошибка\b|\bошибк\w*",
    "myth": r"\bmyths?\b|\bмиф\w*",
    "lie": r"\blies?\b|\bобман\w*",
    "shocking": r"\bshocking\b|\bшок(?:ирующ\w*|\b)",
    "banned": r"\bbanned\b|\bзапрет\w*|\bзапрещ\w*",
    "scam": r"\bscam\b",
    "exposed": r"\bexposed\b|\bразоблач\w*",
    "hidden": r"\bhidden\b|\bскрыва\w*",
    "turnedOut": r"\bturned out\b|\bоказалось\b",
    "butThen": r"\bbut (?:then|here|what)\b|\bно (?:потом|вот|тут)\b",
    "catch": r"\bcatch\b|\bподвох\b",
    "lostEverything": r"\blost everything\b|\bпотерял\w*",
    "almost": r"\balmost (?:died|lost|quit)\b",
    "worst": r"\bworst\b|\bхудш\w*",
    "wontBelieve": r"\bwon't believe\b|\bне поверите\b|\bне ожидал\w*",
    "stayUntil": r"\bstay until\b|\blater in this video\b|\bдо конца видео\b",
    "fail": r"\bпровал\w*",
}
_INTRIGUE_RX = {k: re.compile(v, re.I) for k, v in _INTRIGUE.items()}

_YOU = re.compile(r"\b(?:you|your|yours|yourself|you'll|you're|you've|you'd|"
                  r"ты|тебе|тебя|тобой|твой|твоя|твое|твои|твоих|вы|вам|вас|ваш|ваша|ваше|ваши)\b",
                  re.I)
_YOU_KNOW_FILLER = re.compile(r"(?<!did )(?<!do )\byou know\b", re.I)

_GREETING = re.compile(
    r"^[\W_]*(?:hi|hey there|hey|hello|yo|what's up|good morning|welcome|всем привет|привет|"
    r"здравствуйте|добрый день|приветствую|дорогие друзья|друзья|ребята)\b", re.I)
_FILLER_PHRASES = {
    "welcome to": r"\bwelcome (?:back )?to (?:my|the|our)\b",
    "my name is": r"\bmy name is\b",
    "in today's video": r"\bin today's video\b",
    "before we start": r"\bbefore we (?:start|begin|get started|dive)\b",
    "don't forget to": r"\bdon't forget to\b",
    "subscribe": r"\bsubscribe\b",
    "if you're new here": r"\bif you're new here\b",
    "hit the bell": r"\bhit the bell\b",
    "today we're going to talk": r"\btoday we're (?:going to )?(?:talk|discuss)\b",
    "добро пожаловать": r"\bдобро пожаловать\b",
    "с вами": r"\bс вами\b",
    "меня зовут": r"\bменя зовут\b",
    "в сегодняшнем видео": r"\bв сегодняшнем видео\b",
    "прежде чем начнем": r"\bпрежде чем начн\w*",
    "не забудьте": r"\bне забудьте\b",
    "поставь лайк": r"\bпоставь лайк\b",
    "колокольчик": r"\bколокольчик\b",
    # imperative forms only -- "1000 подписчиков" must not be filler
    "подпишись": r"\bподпишись\b|\bподпишитесь\b|\bподписывайтесь\b",
    "сегодня поговорим": r"\bсегодня (?:мы )?(?:поговорим|обсудим)\b",
}
_FILLER_RX = {k: re.compile(v, re.I) for k, v in _FILLER_PHRASES.items()}
_FILLER_ALL = re.compile("|".join(_FILLER_PHRASES.values()), re.I)

_TIPS = {
    "en": {
        "cutFiller": "Greetings and 'subscribe' lines take up the first seconds; consider "
                     "opening on the topic itself.",
        "question": "A direct question to the viewer is a common opening among strong hooks; "
                    "consider testing one.",
        "number": "A concrete number or sum is a common feature of strong hooks; consider "
                  "adding one if it is true to the video.",
        "promise": "A stated payoff ('by the end of this video you'll know...') is common "
                   "among strong hooks; consider stating what the viewer gets.",
        "intrigue": "A tension or open loop ('but there's a catch') often shows up in strong "
                    "hooks; consider adding one if the video has it.",
        "secondPerson": "Speaking to the viewer directly ('you') is common in strong hooks; "
                        "consider it.",
        "firstSentence": "The first sentence is long; a shorter opening line is easier to "
                         "take in.",
        "pace": "The delivery pace is far from typical speech; check that the intro is not "
                "too slow or rushed.",
    },
    "ru": {
        "cutFiller": "Приветствие и призывы подписаться занимают первые секунды; можно "
                     "начать сразу с темы.",
        "question": "Прямой вопрос зрителю часто встречается в сильных вступлениях; "
                    "можно попробовать.",
        "number": "Конкретная цифра или сумма часто встречается в сильных вступлениях; "
                  "можно добавить, если это правда для видео.",
        "promise": "Обещание результата («в конце видео ты узнаешь...») часто встречается "
                   "в сильных вступлениях; можно назвать, что получит зритель.",
        "intrigue": "Интрига или конфликт («но есть подвох») часто встречается в сильных "
                    "вступлениях; можно добавить, если в видео он есть.",
        "secondPerson": "Прямое обращение к зрителю («ты», «вы») часто встречается в "
                        "сильных вступлениях; можно попробовать.",
        "firstSentence": "Первая фраза длинная; короткая первая фраза легче воспринимается.",
        "pace": "Темп речи заметно отличается от обычного; проверьте, что вступление не "
                "слишком медленное или торопливое.",
    },
}
_TIP_ORDER = ("cutFiller", "question", "number", "promise", "intrigue", "secondPerson",
              "firstSentence", "pace")


def _question(norm, punctuated):
    if punctuated:
        qs = [s for s in _SENTENCE_SPLIT.split(norm) if "?" in s]
        for s in qs:
            m = _Q_WORD.search(s)
            if m:
                return 15, [m.group(0)]
        return (8, ["?"]) if qs else (0, [])
    m = _Q_WORD.search(norm)
    return (15, [m.group(0)]) if m else (0, [])


def _number(norm):
    cleaned = _TIMECODE.sub(" ", _YEAR.sub(" ", norm))
    strong = [m.group(0).strip(" .,") for m in _NUMBER_STRONG.finditer(cleaned)]
    if strong:
        return 20, strong[:3]
    bare = _NUMBER_BARE.findall(cleaned)
    return (8, bare[:3]) if bare else (0, [])


def _promise(norm):
    m = _PROMISE_STRONG.search(norm)
    if m:
        return 20, [m.group(0)]
    m = _PROMISE_MEDIUM.search(norm)
    return (12, [m.group(0)]) if m else (0, [])


def _intrigue(norm):
    found = [k for k, rx in _INTRIGUE_RX.items() if rx.search(norm)]
    return (20 if len(found) >= 2 else 10 if found else 0), found


def _second_person(norm):
    found = _YOU.findall(_YOU_KNOW_FILLER.sub(" ", norm))
    return (10 if len(found) >= 3 else 6 if found else 0), found[:3]


def _content_words(sentence):
    """Words of a sentence without a leading greeting and without filler phrases."""
    s = _GREETING.sub(" ", sentence, count=1)
    return _words(_FILLER_ALL.sub(" ", s))


def _first_sentence(text):
    """(points, evidence) for the first sentence that says something; greeting and
    filler lines do not count. A sentence with fewer than 5 content words is skipped."""
    for s in _SENTENCE_SPLIT.split(normalize(text)):
        words = _content_words(s)
        if len(words) < 5:
            continue
        n = len(words)
        return (10 if n <= 14 else 5 if n <= 22 else 0), [f"{n} words"]
    return 0, []


def _pace(words, span_sec):
    wps = words / span_sec
    pts = 5 if 2.0 <= wps <= 3.6 else 2 if 1.5 <= wps <= 4.2 else 0
    return pts, [f"{wps:.1f} words/s"]


def _penalties(norm):
    out = []
    g = _GREETING.search(norm)
    if g:
        out.append({"id": "greeting", "points": GREETING_PENALTY,
                    "text": f"the text opens with a greeting ('{g.group(0).strip(' .,!-')}')"})
    found = [k for k, rx in _FILLER_RX.items() if rx.search(norm)]
    if found:
        out.append({"id": "filler", "points": max(FILLER_PENALTY_CAP, FILLER_PENALTY_EACH * len(found)),
                    "text": "filler phrases: " + ", ".join(found)})
    return out


def _tips(features, penalties, language):
    """Up to MAX_TIPS suggestions: filler first, then the features that earned
    less than three quarters of their maximum, in _TIP_ORDER."""
    wanted = ["cutFiller"] if penalties else []
    for k in _TIP_ORDER[1:]:
        f = features.get(k)
        if f is None or f["points"] >= f["max"] * 0.75:
            continue
        if k == "firstSentence" and not f["evidence"]:
            continue    # no sentence with content was found: nothing to say about its length
        wanted.append(k)
    return [{"id": k, "text": _TIPS[language][k]} for k in wanted[:MAX_TIPS]]


def score_hook(text, span_sec=None) -> dict:
    """Score the text of a hook. span_sec is how many seconds the text lasted
    (None when unknown: pace is then left out and the other weights are
    renormalised, as template_risk_score does with a missing signal).

    Returns {score 0-100, level weak|ok|strong, language, words, punctuated,
    features {name: {points, max, hit, evidence} | None}, penalties [{id,
    points, text}], tips [{id, text}] (at most 3, in the text's language),
    note}. Empty text -> score None, level "insufficient-data"."""
    raw = (text or "").strip()
    words = len(_words(raw))
    if not words:
        return {"score": None, "level": "insufficient-data", "language": None, "words": 0,
                "punctuated": False, "features": {}, "penalties": [], "tips": [],
                "note": DISCLAIMER}

    norm = normalize(raw)
    language = detect_language(raw)
    marks = len(re.findall(r"[.?!]", raw))
    punctuated = marks >= max(1, words / 40)

    features = {}
    for name, (pts, ev) in (("question", _question(norm, punctuated)), ("number", _number(norm)),
                            ("promise", _promise(norm)), ("intrigue", _intrigue(norm)),
                            ("secondPerson", _second_person(norm))):
        features[name] = {"points": pts, "max": MAX_POINTS[name], "hit": pts > 0, "evidence": ev}
    if punctuated:
        pts, ev = _first_sentence(raw)
        features["firstSentence"] = {"points": pts, "max": MAX_POINTS["firstSentence"],
                                     "hit": pts == MAX_POINTS["firstSentence"], "evidence": ev}
    else:
        features["firstSentence"] = None
    if span_sec:
        pts, ev = _pace(words, span_sec)
        features["pace"] = {"points": pts, "max": MAX_POINTS["pace"], "hit": pts == 5,
                            "evidence": ev}
    else:
        features["pace"] = None

    used = [f for f in features.values() if f is not None]
    base = round(100 * sum(f["points"] for f in used) / sum(f["max"] for f in used))
    penalties = _penalties(norm)
    score = max(0, min(100, base + sum(p["points"] for p in penalties)))
    return {"score": score, "level": level_for(score), "language": language, "words": words,
            "punctuated": punctuated, "features": features, "penalties": penalties,
            "tips": _tips(features, penalties, language), "note": DISCLAIMER}


# ------------------------------------------------------------------ benchmark

def _mean(values):
    return round(sum(values) / len(values), 1) if values else None


def _rates(results):
    """{feature: share of the results where it is hit, among those where it is computable}"""
    out = {}
    for name in FEATURES:
        got = [r["features"][name]["hit"] for r in results
               if r.get("features", {}).get(name) is not None]
        out[name] = round(sum(got) / len(got), 3) if got else None
    return out


def aggregate_hooks(outlier_results, regular_results) -> dict:
    """Compare the hook scores of a niche's outliers with its ordinary videos.
    Inputs are score_hook() results; ones without a score are ignored. A
    feature is "more common in outliers" only when its hit rate is at least
    MIN_DIFF_PP percentage points higher; the comparison is `reliable` only
    with MIN_OUTLIERS and MIN_REGULAR hooks in the two groups."""
    outs = [r for r in outlier_results or [] if r.get("score") is not None]
    regs = [r for r in regular_results or [] if r.get("score") is not None]
    o_rates, r_rates = _rates(outs), _rates(regs)
    features = {}
    for name in FEATURES:
        o, r = o_rates[name], r_rates[name]
        features[name] = {"outlierRate": o, "regularRate": r,
                          "diffPp": None if o is None or r is None else round((o - r) * 100)}
    more = sorted((n for n, f in features.items()
                   if f["diffPp"] is not None and f["diffPp"] >= MIN_DIFF_PP),
                  key=lambda n: -features[n]["diffPp"])
    return {"outliers": {"n": len(outs), "meanScore": _mean([r["score"] for r in outs])},
            "regular": {"n": len(regs), "meanScore": _mean([r["score"] for r in regs])},
            "reliable": len(outs) >= MIN_OUTLIERS and len(regs) >= MIN_REGULAR,
            "features": features, "moreCommonInOutliers": more}

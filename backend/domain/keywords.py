"""N-gram extraction and trend scoring for titles / tags / descriptions.

Two things this computes that generic word-clouds do not:

  lift      P(video is an outlier | title contains phrase) / P(video is an outlier)
            -- a phrase with lift 2.0 doubles the odds of a breakout. This is the
            only keyword metric here that says anything about *performance*
            rather than popularity.

  momentum  share of the phrase this period / share last period
            -- catches phrases that are rising even while still rare, which is
            the whole point of "trending keywords за 24 часа".

YouTube's API has no search-volume field and never has; anything claiming one is
either scraping autocomplete or reselling Google Trends. So we score by what we
can measure honestly: frequency, performance lift, and period-over-period growth.
"""
import json
import math
import re
import statistics as st
from collections import defaultdict

TOKEN_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?|\d+[а-яa-z]*", re.UNICODE)

# Multilingual stopwords: en / ru / uz / es / de / fr / pt / tr + YouTube filler.
STOPWORDS = set("""
a an the and or but if then than that this these those of in on at to for from by with
without about into over under again further once here there when where why how all any
both each few more most other some such no nor not only own same so too very can will
just dont should now is are was were be been being do does did doing have has had having
i me my we our you your he him his she her it its they them their what which who whom
as up down out off above below
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только
ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни
быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут
где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем
чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после
над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо
свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
va bilan uchun bu shu ham lekin ammo yoki emas bor yoq men sen biz siz ular kim nima
qanday qachon qayerda nega juda eng bir ikki uch koʻp kam bugun ertaga kecha
el la los las un una unos unas de del y o pero si no que en por para con sin sobre
der die das ein eine und oder aber wenn dann als dass von im auf zu fur mit ohne uber
le les des du et ou mais si alors que dans sur pour avec sans sous
o os as um uma e ou mas se entao que em por para com sem sobre
ve ile icin bu su ama veya degil var yok ben sen biz siz onlar
video videos youtube shorts short full new official channel subscribe like watch
видео канал подписывайся смотреть новый новое смотри полный обзор
""".split())

GENERIC = {"part", "ep", "episode", "vs", "top", "best", "vlog", "live", "hd", "4k"}


def tokenize(text: str) -> list:
    if not text:
        return []
    return [t.lower() for t in TOKEN_RE.findall(text)]


def clean_tokens(tokens, min_len: int = 2) -> list:
    return [t for t in tokens if len(t) >= min_len and t not in STOPWORDS]


def ngrams(tokens, n_max: int = 3, n_min: int = 1):
    """Contiguous n-grams. Multi-word grams may bridge a stopword-free sequence
    only -- so 'how to make' collapses to 'make', which is intended: we want the
    topical core, not grammar."""
    out = []
    for n in range(n_min, n_max + 1):
        for i in range(len(tokens) - n + 1):
            gram = tokens[i:i + n]
            if n > 1 and (gram[0] in GENERIC or gram[-1] in GENERIC):
                continue
            out.append(" ".join(gram))
    return out


def phrases_for_video(row, use_tags: bool = True, use_title: bool = True,
                      n_max: int = 3) -> set:
    """Distinct phrases in one video (a set, so repeats in a title count once)."""
    grams = set()
    if use_title:
        grams.update(ngrams(clean_tokens(tokenize(row.get("title"))), n_max=n_max))
    if use_tags:
        raw = row.get("tags")
        tags = []
        if raw:
            try:
                tags = json.loads(raw) if isinstance(raw, str) else list(raw)
            except (ValueError, TypeError):
                tags = []
        for tag in tags:
            toks = clean_tokens(tokenize(tag))
            if 1 <= len(toks) <= n_max:
                grams.add(" ".join(toks))
    return {g for g in grams if g and g not in STOPWORDS}


def literal_tags_for_video(row) -> set:
    """Distinct literal tags for one video, exactly as the creator set them
    (lowercased/trimmed for dedup only) -- no tokenization or n-gramming,
    unlike phrases_for_video. Pass as `aggregate`'s phrase_fn when the whole
    tag is the unit of interest (top-tags-by-category), not its words."""
    raw = row.get("tags")
    tags = []
    if raw:
        try:
            tags = json.loads(raw) if isinstance(raw, str) else list(raw)
        except (ValueError, TypeError):
            tags = []
    return {t.strip().lower() for t in tags if isinstance(t, str) and t.strip()}


def aggregate(rows, use_tags=True, use_title=True, n_max=3, outlier_threshold=3.0,
              phrase_fn=None):
    """rows: dicts with title/tags/views/outlier/vsr. Returns per-phrase stats.

    phrase_fn overrides how a video's phrases are derived (default: N-gram
    phrases via phrases_for_video) -- e.g. literal_tags_for_video for
    whole-tag aggregation instead of tokenized N-grams."""
    get_phrases = phrase_fn or (lambda row: phrases_for_video(row, use_tags, use_title, n_max))
    stats = defaultdict(lambda: {"videos": 0, "views": 0, "view_list": [],
                                 "outliers": [], "hits": 0, "examples": [],
                                 "video_ids": set()})
    total_videos = 0
    total_hits = 0
    for row in rows:
        total_videos += 1
        is_hit = (row.get("outlier") or 0) >= outlier_threshold
        total_hits += 1 if is_hit else 0
        for phrase in get_phrases(row):
            s = stats[phrase]
            s["videos"] += 1
            s["video_ids"].add(row.get("video_id"))
            s["views"] += row.get("views") or 0
            s["view_list"].append(row.get("views") or 0)
            if row.get("outlier") is not None:
                s["outliers"].append(row["outlier"])
            if is_hit:
                s["hits"] += 1
            if len(s["examples"]) < 3:
                s["examples"].append({"videoId": row.get("video_id"),
                                      "title": row.get("title"),
                                      "views": row.get("views")})
    base_rate = (total_hits / total_videos) if total_videos else 0.0
    return stats, total_videos, base_rate


def merge_semantic_synonyms(stats: dict, phrase_vectors: dict,
                            similarity_threshold: float = 0.85, namer=None) -> dict:
    """Stage 10: post-aggregate step that merges phrases whose embeddings
    are near-duplicates (paraphrases an n-gram model can't see -- "cold
    shower" / "cold showers" / "ice bath") into one group. Runs on
    aggregate()'s *output*, never touches aggregate() itself, so the
    default n-gram mode is unaffected unless a caller opts in.

    phrase_vectors: {phrase: vector} for (at least) every key in stats --
    computed by the caller, keeping this function embedding-library-
    agnostic. namer(cluster_phrases) -> str picks the canonical name for a
    merged cluster; default is the most frequent (by videos) member.
    Every returned entry carries "mergedFrom": [original phrases] (a
    single-element list for a phrase that didn't merge with anything), so
    a caller can realign a previous period's stats under the same
    canonical names for period-over-period momentum."""
    import numpy as np

    phrases_with_vec = [p for p in stats if phrase_vectors.get(p) is not None]
    phrases_without_vec = [p for p in stats if phrase_vectors.get(p) is None]

    # Vectorized pairwise cosine (one matrix multiply) instead of a
    # cosine() call per pair -- real corpora can have hundreds of
    # candidate phrases even after the caller's own min_videos filter, and
    # nested-Python-loop cosine calls do not scale to that.
    clusters = []  # [{"rep": phrase, "members": [phrase, ...]}]
    if phrases_with_vec:
        matrix = np.array([phrase_vectors[p] for p in phrases_with_vec], dtype=np.float64)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        unit = matrix / norms
        sims = unit @ unit.T  # (n, n) cosine similarity matrix

        rep_indices = []  # index into phrases_with_vec of each cluster's rep
        for i, phrase in enumerate(phrases_with_vec):
            placed = False
            for cluster_idx, rep_i in enumerate(rep_indices):
                if sims[i, rep_i] >= similarity_threshold:
                    clusters[cluster_idx]["members"].append(phrase)
                    placed = True
                    break
            if not placed:
                rep_indices.append(i)
                clusters.append({"rep": phrase, "members": [phrase]})

    for phrase in phrases_without_vec:
        clusters.append({"rep": phrase, "members": [phrase]})

    merged = {}
    for cluster in clusters:
        members = cluster["members"]
        name = members[0] if len(members) == 1 else \
            (namer(members) if namer else max(members, key=lambda p: stats[p]["videos"]))
        combined = {"videos": 0, "views": 0, "view_list": [], "outliers": [], "hits": 0,
                   "examples": [], "video_ids": set(), "mergedFrom": members}
        for m in members:
            s = stats[m]
            combined["videos"] += s["videos"]
            combined["views"] += s["views"]
            combined["view_list"] += s["view_list"]
            combined["outliers"] += s["outliers"]
            combined["hits"] += s["hits"]
            combined["video_ids"] |= s["video_ids"]
            combined["examples"] += s["examples"]
        combined["examples"] = combined["examples"][:3]
        merged[name] = combined
    return merged


def realign_prev_stats(prev_stats: dict, cluster_map: dict) -> dict:
    """cluster_map: {canonical_name: [member_phrases]}, straight from a
    merge_semantic_synonyms() result's "mergedFrom" fields -- sums a
    previous period's raw per-phrase stats under the SAME canonical names
    the current period merged to, so score()'s exact-key momentum lookup
    still lines up after semantic merging."""
    out = {}
    for name, members in cluster_map.items():
        combined = {"videos": 0, "views": 0, "view_list": [], "outliers": [], "hits": 0,
                   "examples": [], "video_ids": set()}
        found = False
        for m in members:
            s = prev_stats.get(m)
            if not s:
                continue
            found = True
            combined["videos"] += s["videos"]
            combined["views"] += s["views"]
            combined["view_list"] += s["view_list"]
            combined["outliers"] += s["outliers"]
            combined["hits"] += s["hits"]
            combined["video_ids"] |= s["video_ids"]
        if found:
            out[name] = combined
    return out


def collapse_redundant(items):
    """Drop "ai" and "robot" when "ai robot" covers exactly the same videos.

    Without this, every ranking is three copies of the same phrase at three
    n-gram lengths, which is the classic failure mode of naive n-gram trends."""
    by_coverage = {}
    for item in items:
        cov = item.get("_coverage")
        if cov:
            best = by_coverage.get(cov)
            if best is None or (len(item["keyword"].split()), item["keyword"]) > \
                    (len(best["keyword"].split()), best["keyword"]):
                by_coverage[cov] = item
    if by_coverage:
        keep_ids = {id(v) for v in by_coverage.values()}
        items = [i for i in items if not i.get("_coverage") or id(i) in keep_ids]

    by_len = sorted(items, key=lambda x: -len(x["keyword"].split()))
    kept = []
    for item in by_len:
        words = item["keyword"].split()
        redundant = any(
            len(k["keyword"].split()) > len(words)
            and k["videos"] == item["videos"]
            and _contains(k["keyword"].split(), words)
            for k in kept
        )
        if not redundant:
            kept.append(item)
    return kept


def _contains(haystack, needle):
    n = len(needle)
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def score(stats, total_videos, base_rate, prev_stats=None, prev_total=0,
          min_videos=3, top_n=30, sort_by="momentum", collapse=True):
    """Turn raw per-phrase counters into the ranked trending-keyword list."""
    out = []
    for phrase, s in stats.items():
        if s["videos"] < min_videos:
            continue
        share = s["videos"] / total_videos if total_videos else 0.0
        prev = (prev_stats or {}).get(phrase)
        prev_count = prev["videos"] if prev else 0
        prev_share = (prev_count / prev_total) if prev_total else 0.0
        # Laplace-smoothed share ratio: a phrase going 0 -> 3 gets a large but
        # finite momentum, and a phrase seen once in a tiny previous window does
        # not explode the score.
        p_now = (s["videos"] + 0.5) / (total_videos + 1) if total_videos else 0.0
        p_prev = (prev_count + 0.5) / (prev_total + 1) if prev_total else None
        momentum = round(min(p_now / p_prev, 99.0), 2) if p_prev else None
        hit_rate = s["hits"] / s["videos"]
        lift = round(hit_rate / base_rate, 2) if base_rate > 0 else None
        med_views = int(st.median(s["view_list"])) if s["view_list"] else 0
        med_outlier = round(st.median(s["outliers"]), 2) if s["outliers"] else None
        out.append({
            "keyword": phrase,
            "videos": s["videos"],
            "isNew": prev_stats is not None and prev is None,
            "share": round(share * 100, 2),
            "previousShare": round(prev_share * 100, 2) if prev_stats is not None else None,
            "momentum": momentum,
            "totalViews": s["views"],
            "medianViews": med_views,
            "medianOutlier": med_outlier,
            "outlierLift": lift,
            "trendScore": round(math.log1p(s["videos"]) * (lift if lift is not None else 1.0)
                               * max(momentum if momentum is not None else 1.0, 0.1), 2),
            "examples": s["examples"],
            "_coverage": frozenset(s["video_ids"]),
        })

    if collapse:
        out = collapse_redundant(out)

    keys = {
        "momentum": lambda x: (x["momentum"] if x["momentum"] is not None else 0, x["videos"]),
        "trend": lambda x: x["trendScore"],
        "count": lambda x: x["videos"],
        "views": lambda x: x["totalViews"],
        "lift": lambda x: (x["outlierLift"] or 0, x["videos"]),
        "median_views": lambda x: x["medianViews"],
    }
    out.sort(key=keys.get(sort_by, keys["momentum"]), reverse=True)
    out = out[:top_n]
    for item in out:
        item.pop("_coverage", None)
    return out

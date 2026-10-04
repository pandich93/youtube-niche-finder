"""Where to enter (plan 27) -- the score is in domain/niche_ranking.py. This
gathers, for every niche, the numbers other screens already show (trend and
newcomers from application/saturation, template and policy shares, the RPM of
the dominant category) and ranks them. The whole pass is cached for an hour:
template and policy signals read every channel of every niche. Zero quota,
no LLM. Reads only.
"""
import time

import infrastructure.postgres as db
from application import discovery as trends
from application import policy_signals as PS
from application import saturation as SAT
from application import search as Q
from application import template_risk as TRA
from domain import metrics as M
from domain import niche_ranking as R
from infrastructure.categories import repository as C

CACHE_TTL_SEC = 3600
MAX_COMPARE = 3
NOTE = ("An estimate of niche-finder, not YouTube data: the weights are a judgement, the RPM is "
        "a guess (no one publishes it) and everything is computed over the channels we collected.")

_cache = {"at": None, "data": None}


def reset_cache():
    _cache["at"], _cache["data"] = None, None


def _dominant_rpm(rows) -> tuple:
    """(category id, effective RPM) of the category most videos of the niche are in."""
    counts = {}
    for r in rows:
        if r.get("category_id"):
            cid = str(r["category_id"])
            counts[cid] = counts.get(cid, 0) + 1
    if not counts:
        return None, None
    top = max(counts, key=counts.get)
    return top, M.rpm_effective(C.rpm_niche(top))


def _template_share(slug):
    res = TRA.niche_template_risk(slug)
    pct = res.get("highRiskSharePercent") if res.get("found") else None
    return None if pct is None else round(pct / 100, 3)


def _policy_share(slug):
    res = PS.niche_policy_signals(slug)
    if not res.get("found"):
        return None
    shares = [c["shareFlagged"] for c in res["categories"].values() if c["shareFlagged"] is not None]
    return max(shares) if shares else None


def _compute() -> dict:
    rows = trends.load_window(period=SAT.WINDOW)
    ids = {r["video_id"] for r in rows}
    by_id = {r["video_id"]: r for r in rows}
    conn = db.get_conn()
    try:
        niches = {r["slug"]: dict(r) for r in conn.execute(
            "SELECT n.slug, n.label, COUNT(vn.video_id) AS video_count FROM niches n "
            "LEFT JOIN video_niches vn ON vn.niche_slug = n.slug GROUP BY n.slug, n.label").fetchall()}
        members = {s: set() for s in niches}
        for r in conn.execute("SELECT video_id, niche_slug FROM video_niches").fetchall():
            if r["video_id"] in ids and r["niche_slug"] in members:
                members[r["niche_slug"]].add(r["video_id"])
    finally:
        conn.close()
    sat = SAT.grouped(rows, members)
    out = []
    for slug, info in niches.items():
        s = sat[slug]
        sig = s["signals"]
        category, rpm = _dominant_rpm([by_id[v] for v in members[slug]])
        metrics = {"status": s["status"], "demandRatio": sig["demand"]["ratio"],
                   "supplyRatio": sig["supply"]["ratio"], "newcomersShare": sig["newcomers"]["share"],
                   "rpmMid": rpm, "templateShare": _template_share(slug),
                   "policyShare": _policy_share(slug)}
        out.append({"niche": slug, "label": info["label"], "videos": info["video_count"],
                    "status": s["status"], "confidence": s["confidence"], "format": s["format"],
                    "recentVideos": s["counts"]["recent"], "categoryId": category,
                    "metrics": metrics, **R.score(metrics)})
    out.sort(key=R.sort_key)
    return {"niches": out, "computedAt": db.now_iso(), "cacheTtlSeconds": CACHE_TTL_SEC,
            "weights": R.WEIGHTS, "note": NOTE}


def rank_niches(refresh: bool = False) -> dict:
    """Every niche with a 0-100 score and the breakdown behind it, best first;
    niches too small to judge come last without a score. Cached for an hour
    (refresh=True recomputes)."""
    if (refresh or _cache["data"] is None or _cache["at"] is None
            or time.time() - _cache["at"] > CACHE_TTL_SEC):
        _cache["data"], _cache["at"] = _compute(), time.time()
    return _cache["data"]


def compare_niches(slugs: list) -> dict:
    """2-3 niches side by side: the ranking entry of each plus the overview
    numbers (videos, channels, median outlier, ...) the ranking does not use."""
    slugs = list(dict.fromkeys(slugs or []))
    if not 2 <= len(slugs) <= MAX_COMPARE:
        raise ValueError(f"pick 2 to {MAX_COMPARE} different niches")
    ranked = {n["niche"]: n for n in rank_niches()["niches"]}
    missing = [s for s in slugs if s not in ranked]
    cols = []
    for s in slugs:
        if s in missing:
            continue
        o = Q.niche_overview(s)
        cols.append({**ranked[s], "overview": {k: o.get(k) for k in (
            "video_count", "channel_count", "median_outlier_score", "median_subscribers",
            "median_views_per_video", "shorts_share_percent", "small_channel_breakouts",
            "saturation_hint")} if o.get("found") else None})
    return {"niches": cols, "missing": missing, "weights": R.WEIGHTS, "note": NOTE}

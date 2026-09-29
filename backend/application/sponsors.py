"""Sponsor map (plan 09): which brands pay creators in a niche or channel, and
how often -- read from the video descriptions already in the database.

scan_sponsors() reads descriptions through domain/sponsors.py and stores the
signals in video_sponsors; it is both the worker step and the backfill. It
never touches the network or YouTube quota. sponsor_map() / channel_sponsors()
only aggregate what was stored.

Everything here is a lower bound: a sponsor that is only spoken in the video
and never written in the description cannot be seen.
"""
import statistics
from collections import defaultdict

import infrastructure.postgres as db
from domain import periods as P
from domain import sponsors as SP

NOTE = "lower bound: only sponsors named in video descriptions"
_SPONSORED_KINDS = ("sponsor", "promo_code")
_CHUNK = 400

_PENDING = ("FROM videos v LEFT JOIN channels c ON c.channel_id = v.channel_id "
            "LEFT JOIN sponsor_scan s ON s.video_id = v.video_id "
            "WHERE s.video_id IS NULL OR s.rules_version < ? "
            "OR s.desc_hash <> md5(coalesce(v.description, ''))")


# ---------------------------------------------------------------- scanning

def _own_names(row) -> list:
    return [n for n in (row["channel_title"], row["custom_url"]) if n]


def scan_sponsors(limit: int = 5000, batch: int = 500) -> dict:
    """Scan up to `limit` videos whose description was never scanned, changed
    since the last scan (md5), or was scanned by older rules
    (SPONSOR_RULES_VERSION). Per batch: drop the video's old signals, insert
    the new ones, record the scan. Returns {scanned, withSignals, rows,
    remaining}; run it again while `remaining` > 0. Zero quota."""
    conn = db.get_conn()
    scanned = with_signals = rows = 0
    try:
        while scanned < limit:
            chunk = conn.execute(
                "SELECT v.video_id, v.description, c.title AS channel_title, c.custom_url "
                + _PENDING + " ORDER BY v.video_id LIMIT ?",
                (SP.SPONSOR_RULES_VERSION, min(batch, limit - scanned))).fetchall()
            if not chunk:
                break
            now = db.now_iso()
            ids = [r["video_id"] for r in chunk]
            conn.execute("DELETE FROM video_sponsors WHERE video_id IN (%s)"
                         % ",".join("?" * len(ids)), ids)
            signals, scans = [], []
            for r in chunk:
                found = SP.extract_sponsor_signals(r["description"], own_names=_own_names(r))
                with_signals += 1 if found else 0
                signals += [(r["video_id"], s["brand"], s["kind"], s["evidence"], now)
                            for s in found]
                scans.append((r["video_id"], SP.description_hash(r["description"]),
                              SP.SPONSOR_RULES_VERSION, now))
            if signals:
                conn.executemany(
                    "INSERT INTO video_sponsors (video_id, brand, kind, evidence, detected_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT (video_id, brand, kind) DO NOTHING",
                    signals)
            conn.executemany(
                "INSERT INTO sponsor_scan (video_id, desc_hash, rules_version, scanned_at) "
                "VALUES (?,?,?,?) ON CONFLICT (video_id) DO UPDATE SET "
                "desc_hash = EXCLUDED.desc_hash, rules_version = EXCLUDED.rules_version, "
                "scanned_at = EXCLUDED.scanned_at", scans)
            conn.commit()
            scanned += len(chunk)
            rows += len(signals)
        remaining = conn.execute("SELECT COUNT(*) AS n " + _PENDING,
                                 (SP.SPONSOR_RULES_VERSION,)).fetchone()["n"]
    finally:
        conn.close()
    return {"scanned": scanned, "withSignals": with_signals, "rows": rows,
            "remaining": remaining}


# ---------------------------------------------------------------- reading

def _signals(conn, video_ids) -> dict:
    """video_id -> [(brand, kind, evidence)]"""
    out = defaultdict(list)
    ids = list(video_ids)
    for i in range(0, len(ids), _CHUNK):
        part = ids[i:i + _CHUNK]
        q = ("SELECT video_id, brand, kind, evidence FROM video_sponsors "
             "WHERE video_id IN (%s) ORDER BY brand, kind" % ",".join("?" * len(part)))
        for r in conn.execute(q, part).fetchall():
            out[r["video_id"]].append((r["brand"], r["kind"], r["evidence"]))
    return out


def _avg(values):
    return round(sum(values) / len(values), 1) if values else 0


def _med(values):
    return round(statistics.median(values), 1) if values else 0


def _brands(videos, signals, kinds, top_n) -> list:
    """Group signals of the given kinds by brand: distinct videos and channels,
    newest video date, up to 3 example videos (newest first)."""
    groups = defaultdict(dict)   # brand -> {video_id: (video, evidence, kind)}
    for v in videos:
        for brand, kind, evidence in signals.get(v["video_id"], ()):
            if kind not in kinds:
                continue
            prev = groups[brand].get(v["video_id"])
            if prev is None or (kind == "sponsor" and prev[2] != "sponsor"):
                groups[brand][v["video_id"]] = (v, evidence, kind)
    out = []
    for brand, per_video in groups.items():
        items = sorted(per_video.values(), key=lambda t: t[0]["published_at"] or "", reverse=True)
        kind_names = {t[2] for t in items}
        out.append({
            "brand": brand,
            "kind": "sponsor" if "sponsor" in kind_names else sorted(kind_names)[0],
            "videos": len(items),
            "channels": len({t[0]["channel_id"] for t in items}),
            "lastSeen": items[0][0]["published_at"],
            "examples": [{"videoId": v["video_id"], "title": v["title"],
                          "channelId": v["channel_id"], "evidence": ev}
                         for v, ev, _ in items[:3]],
        })
    out.sort(key=lambda b: (-b["videos"], -b["channels"], b["brand"]))
    return out[:top_n]


def _summary(videos, signals, top_n) -> dict:
    """The numbers shared by the niche and the channel view. Shares and view
    averages use only videos that were already scanned, so a half-finished
    backfill does not dilute them; scanCoverage says how much was scanned."""
    scanned = [v for v in videos if v["scanned"]]
    sponsored = [v for v in scanned if any(k in _SPONSORED_KINDS
                                           for _, k, _ in signals.get(v["video_id"], ()))]
    affiliate = [v for v in scanned if any(k == "affiliate"
                                           for _, k, _ in signals.get(v["video_id"], ()))]
    sponsored_ids = {v["video_id"] for v in sponsored}
    views_with = [v["view_count"] or 0 for v in sponsored]
    views_without = [v["view_count"] or 0 for v in scanned if v["video_id"] not in sponsored_ids]
    n = len(scanned)
    return {
        "videos": len(videos),
        "videosWithSponsor": len(sponsored),
        "sponsorShare": round(len(sponsored) / n, 3) if n else 0,
        "videosWithAffiliate": len(affiliate),
        "affiliateShare": round(len(affiliate) / n, 3) if n else 0,
        "avgViewsWithSponsor": _avg(views_with),
        "avgViewsWithout": _avg(views_without),
        "medianViewsWithSponsor": _med(views_with),
        "medianViewsWithout": _med(views_without),
        "scanCoverage": round(n / len(videos), 3) if videos else 0,
        "topBrands": _brands(scanned, signals, _SPONSORED_KINDS, top_n),
        "affiliateBrands": _brands(scanned, signals, ("affiliate",), top_n),
        "note": NOTE,
    }


_SELECT = ("SELECT v.video_id, v.channel_id, v.title, v.view_count, v.published_at, "
           "(s.video_id IS NOT NULL) AS scanned FROM videos v "
           "LEFT JOIN sponsor_scan s ON s.video_id = v.video_id ")


def _load(conn, where, params, period):
    start, _ = P.window(period)
    q, ps = _SELECT + where, list(params)
    if start:
        q += " AND v.published_at >= ?"
        ps.append(start)
    videos = [dict(r) for r in conn.execute(q, ps).fetchall()]
    return videos, _signals(conn, [v["video_id"] for v in videos])


def sponsor_map(niche: str, period: str = "all", top_n: int = 10) -> dict:
    """Share of a niche's videos with a named sponsor, the brands behind them
    (videos, channels, last seen, example videos), affiliate brands kept
    separate, and views with vs without a sponsor. Zero quota."""
    conn = db.get_conn()
    try:
        where = "JOIN video_niches vn ON vn.video_id = v.video_id WHERE vn.niche_slug = ?"
        anywhere = conn.execute("SELECT 1 FROM video_niches WHERE niche_slug = ? LIMIT 1",
                                (niche,)).fetchone()
        if not anywhere:
            return {"niche": niche, "period": period, "found": False,
                    "hint": "no videos collected for this niche yet -- run collect_niche first",
                    "note": NOTE}
        videos, signals = _load(conn, where, (niche,), period)
    finally:
        conn.close()
    return {"niche": niche, "period": period, "found": True,
            **_summary(videos, signals, top_n)}


def channel_sponsors(channel_id: str, period: str = "all", top_n: int = 10) -> dict:
    """The same picture for one channel's own uploads."""
    conn = db.get_conn()
    try:
        videos, signals = _load(conn, "WHERE v.channel_id = ?", (channel_id,), period)
        anywhere = conn.execute("SELECT 1 FROM videos WHERE channel_id = ? LIMIT 1",
                                (channel_id,)).fetchone()
    finally:
        conn.close()
    if not anywhere:
        return {"channelId": channel_id, "period": period, "found": False,
                "hint": "no videos of this channel in the database yet", "note": NOTE}
    return {"channelId": channel_id, "period": period, "found": True,
            **_summary(videos, signals, top_n)}

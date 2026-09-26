"""Self-calibrating maturity curve: the worker fits it from our own snapshots
and every process (web, MCP, worker) switches domain.metrics over to it.

The shipped MATURITY_CURVE stays in charge until a fitted curve passes the
checks in metrics.fit_maturity_curve (enough videos watched from publication
to 28+ days, a point at every key age). A passing curve is stored in meta
"maturity_curve"; every check, passing or not, in "maturity_curve_check", so
status() can say why the manual curve is still in use. MATURITY_CURVE_AUTO=0
turns the whole thing off. A later failing check never drops a stored curve:
a short worker outage must not flip every age-adjusted score back.
"""
import json
import os
import time
from datetime import datetime, timezone

import infrastructure.postgres as db
from domain import metrics as M

CURVE_KEY = "maturity_curve"
CHECK_KEY = "maturity_curve_check"
MIN_VIDEOS = 30
CACHE_TTL_SEC = 600

_loaded_at = None


def auto_enabled() -> bool:
    return os.environ.get("MATURITY_CURVE_AUTO", "1").strip().lower() not in ("0", "false", "no")


def reset_cache():
    global _loaded_at
    _loaded_at = None


def _ts(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _histories(conn) -> dict:
    """{video_id: [(age_days, views), ...]} from every stats snapshot."""
    rows = conn.execute(
        "SELECT h.video_id, h.captured_at, h.view_count, v.published_at "
        "FROM video_stats_history h JOIN videos v ON v.video_id = h.video_id"
    ).fetchall()
    out = {}
    for r in rows:
        pub, cap = _ts(r["published_at"]), _ts(r["captured_at"])
        if pub and cap:
            out.setdefault(r["video_id"], []).append(
                ((cap - pub).total_seconds() / 86400, r["view_count"] or 0))
    return out


def calibrate(min_videos: int = MIN_VIDEOS) -> dict:
    """Fit the curve from the database without storing anything."""
    conn = db.get_conn()
    try:
        return M.fit_maturity_curve(_histories(conn), min_videos=min_videos)
    finally:
        conn.close()


def _read_json(conn, key):
    raw = db.get_meta(conn, key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def apply_calibration() -> dict:
    """Worker step: fit, record the check, and store the curve if it passed
    (and MATURITY_CURVE_AUTO is on). Reloads this process's curve."""
    fit = calibrate()
    now = datetime.now(timezone.utc).isoformat()
    applied = bool(fit["calibrated"] and auto_enabled())
    reason = fit.get("reason")
    if fit["calibrated"] and not applied:
        reason = "MATURITY_CURVE_AUTO=0"
    conn = db.get_conn()
    try:
        db.set_meta(conn, CHECK_KEY, json.dumps({
            "checkedAt": now, "calibrated": fit["calibrated"], "applied": applied,
            "videosUsed": fit["videosUsed"], "missingAges": fit["missingAges"],
            "reason": reason}))
        if applied:
            db.set_meta(conn, CURVE_KEY, json.dumps({
                "curve": fit["curve"], "calibratedAt": now,
                "videosUsed": fit["videosUsed"]}))
        conn.commit()
        ensure_loaded(conn, force=True)
    finally:
        conn.close()
    return {"applied": applied, "videosUsed": fit["videosUsed"],
            "missingAges": fit["missingAges"], "reason": reason}


def _stored_curve(conn):
    data = _read_json(conn, CURVE_KEY)
    try:
        curve = {float(k) if "." in str(k) else int(k): float(v)
                 for k, v in (data or {}).get("curve", {}).items()}
    except (TypeError, ValueError):
        return None, None
    return (curve, data) if 0 in curve and 30 in curve else (None, None)


def ensure_loaded(conn, force: bool = False):
    """Point domain.metrics at the stored curve (or the shipped one). Cheap to
    call before every age-normalised calculation: re-reads meta at most once
    per CACHE_TTL_SEC unless forced. Any problem means the shipped curve."""
    global _loaded_at
    if not force and _loaded_at is not None and time.monotonic() - _loaded_at < CACHE_TTL_SEC:
        return
    curve = None
    if auto_enabled():
        try:
            curve, _ = _stored_curve(conn)
        except Exception:
            curve = None
    M.set_maturity_curve(curve)
    _loaded_at = time.monotonic()


def status(conn) -> dict:
    """Which curve is in use, since when, from how many videos, and -- when
    the manual one is, or the latest check failed -- why."""
    curve, data = (None, None)
    if auto_enabled():
        try:
            curve, data = _stored_curve(conn)
        except Exception:
            curve = None
    check = _read_json(conn, CHECK_KEY) or {}
    if not auto_enabled():
        reason = "MATURITY_CURVE_AUTO=0"
    elif check:
        reason = check.get("reason")
    else:
        reason = "not checked yet -- the worker checks once a day"
    return {
        "source": "calibrated" if curve else "manual",
        "calibratedAt": (data or {}).get("calibratedAt"),
        "videosUsed": (data or {}).get("videosUsed"),
        "checkedAt": check.get("checkedAt"),
        "reason": reason,
    }

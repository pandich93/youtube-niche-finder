"""Net profit (plan 30) -- the arithmetic is in domain/profit.py. Cost
profiles are personal (cost_profiles.user_id); the revenue side reuses what
the project already estimates: the niche RPM range of a channel's or video's
category, or your real RPM on a connected channel. Zero quota, no LLM.
"""
import statistics as st
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from application import channel_tracking as T
from application import discovery as trends
from application import monetization as MO
from application import own_channels as OWN
from domain import metrics as M
from domain import profit as P
from domain.users import LOCAL_USER_ID
from infrastructure.categories import repository as C

MAX_PROFILES = 50
DEFAULT_VIDEOS_PER_MONTH = 4
YPP_WARNING = ("The channel does not visibly meet the full YPP tier (1,000 subscribers): ad revenue "
               "starts with it, so the real AdSense income may be zero until then.")
NOTE = ("AdSense only -- sponsors and other income are not counted. The RPM is an estimate "
        "(nobody publishes one; estimates disagree by up to 7x), so profit is a range and "
        "'uncertain' is the usual honest answer; a connected channel replaces it with your real RPM.")
_COLS = "id, name, per_video_usd, per_minute_usd, monthly_usd, created_at"


def _money(value, field) -> float:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number") from None
    if v < 0 or v != v or v == float("inf"):
        raise ValueError(f"{field} must be zero or more")
    return round(v, 2)


def list_profiles(user_id: int = LOCAL_USER_ID) -> list:
    conn = db.get_conn()
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT {_COLS} FROM cost_profiles WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()]
    finally:
        conn.close()


def save_profile(name: str, per_video_usd: float = 0, per_minute_usd: float = 0,
                 monthly_usd: float = 0, profile_id: int = None,
                 user_id: int = LOCAL_USER_ID) -> dict:
    """Create a profile, or change the one with profile_id (or with this name).
    Costs in US dollars: a fixed price per video, a price per minute of video,
    and a monthly overhead (subscriptions)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("a profile needs a name")
    values = (_money(per_video_usd, "per_video_usd"), _money(per_minute_usd, "per_minute_usd"),
              _money(monthly_usd, "monthly_usd"))
    conn = db.get_conn()
    try:
        if profile_id is not None:
            row = conn.execute("SELECT id FROM cost_profiles WHERE id = ? AND user_id = ?",
                               (int(profile_id), user_id)).fetchone()
            if not row:
                raise ValueError("profile not found")
        else:
            row = conn.execute("SELECT id FROM cost_profiles WHERE user_id = ? AND name = ?",
                               (user_id, name)).fetchone()
        if row:
            clash = conn.execute("SELECT id FROM cost_profiles WHERE user_id = ? AND name = ? "
                                 "AND id != ?", (user_id, name, row["id"])).fetchone()
            if clash:
                raise ValueError("another profile already has this name")
            out = conn.execute(
                "UPDATE cost_profiles SET name = ?, per_video_usd = ?, per_minute_usd = ?, "
                f"monthly_usd = ? WHERE id = ? AND user_id = ? RETURNING {_COLS}",
                (name, *values, row["id"], user_id)).fetchone()
        else:
            n = conn.execute("SELECT COUNT(*) FROM cost_profiles WHERE user_id = ?",
                             (user_id,)).fetchone()[0]
            if n >= MAX_PROFILES:
                raise ValueError(f"at most {MAX_PROFILES} cost profiles")
            out = conn.execute(
                "INSERT INTO cost_profiles (user_id, name, per_video_usd, per_minute_usd, "
                f"monthly_usd, created_at) VALUES (?,?,?,?,?,?) RETURNING {_COLS}",
                (user_id, name, *values, db.now_iso())).fetchone()
        conn.commit()
        return dict(out)
    finally:
        conn.close()


def delete_profile(profile_id: int, user_id: int = LOCAL_USER_ID) -> dict:
    """Remove one cost profile of yours (a setting you typed in, not collected data)."""
    conn = db.get_conn()
    try:
        row = conn.execute("DELETE FROM cost_profiles WHERE id = ? AND user_id = ? RETURNING id",
                           (int(profile_id), user_id)).fetchone()
        conn.commit()
        return {"removed": bool(row), "id": int(profile_id)}
    finally:
        conn.close()


def _profile(profile, user_id) -> dict:
    """The profile by id or name; with none given the first one; none at all
    -> costs of zero (revenue only), and the result says so."""
    profiles = list_profiles(user_id)
    if profile in (None, ""):
        return profiles[0] if profiles else {"id": None, "name": None, "per_video_usd": 0.0,
                                             "per_minute_usd": 0.0, "monthly_usd": 0.0}
    for p in profiles:
        if str(p["id"]) == str(profile) or p["name"] == str(profile):
            return p
    raise ValueError("cost profile not found")


def _real_rpm(channel_id, user_id):
    try:
        for c in OWN.rpm_calibration(user_id)["channels"]:
            if c["channelId"] == channel_id and c.get("realRpm"):
                return c["realRpm"]
    except Exception:
        return None
    return None


def _channel(channel_id, prof, user_id, videos_per_month):
    a = T.channel_analytics(channel_id)
    if not a.get("found"):
        return {"found": False, "hint": "Run collect_channel / track_channel first."}
    nm = a["revenue"]["nicheModel"]
    rpm, basis = nm["rpm_range"], "niche estimate"
    real = _real_rpm(channel_id, user_id)
    if real:
        rpm, basis = P.real_rpm(real), "your real RPM (connected channel)"
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn = db.get_conn()
    try:
        ch = conn.execute("SELECT title FROM channels WHERE channel_id = ?", (channel_id,)).fetchone()
        durations = [r["duration_seconds"] or 0 for r in conn.execute(
            "SELECT duration_seconds FROM videos WHERE channel_id = ? AND published_at >= ? "
            "AND COALESCE(is_short, 0) = 0", (channel_id, since)).fetchall()]
    finally:
        conn.close()
    uploads = videos_per_month if videos_per_month is not None else len(durations)
    avg = st.mean(durations) if durations else 0
    ypp = MO.for_channel(channel_id)
    full = (ypp.get("tiers") or {}).get("full") or {}
    return {"found": True, "target": {"channelId": channel_id, "title": ch["title"] if ch else None},
            "rpm": rpm, "rpmBasis": basis,
            "ypp": {"status": ypp.get("status"), "fullTierSubscribers": full.get("subscribers")},
            "yppWarning": None if full.get("subscribers") or real else YPP_WARNING,
            "month": P.per_month(prof, rpm, a["revenue"]["monthlyViewsUsed"], uploads, avg),
            "uploadsBasis": "set by you" if videos_per_month is not None
            else "long videos published in the last 30 days"}


def _video(video_id, prof):
    conn = db.get_conn()
    try:
        v = conn.execute("SELECT video_id, title, view_count, duration_seconds, category_id "
                         "FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    finally:
        conn.close()
    if not v:
        return {"found": False, "hint": "Video not in the database -- collect its channel first."}
    rpm = M.rpm_range(C.rpm_niche(v["category_id"]))
    return {"found": True, "target": {"videoId": video_id, "title": v["title"]},
            "rpm": rpm, "rpmBasis": "niche estimate",
            "video": P.per_video(prof, rpm, v["view_count"], v["duration_seconds"])}


def _niche(niche, prof, videos_per_month):
    rows = trends.load_window(period="all", niche=niche)
    if not rows:
        return {"found": False, "hint": "nothing collected under this niche yet -- run collect_niche"}
    shorts = sum(1 for r in rows if r.get("isShort"))
    is_short = shorts > len(rows) - shorts
    fmt = [r for r in rows if bool(r.get("isShort")) == is_short]
    views = int(st.median([r["view_count"] or 0 for r in fmt]))
    duration = int(st.median([r["duration_seconds"] or 0 for r in fmt]))
    cats = {}
    for r in fmt:
        if r.get("category_id"):
            cats[str(r["category_id"])] = cats.get(str(r["category_id"]), 0) + 1
    category = max(cats, key=cats.get) if cats else None
    rpm = M.rpm_range(C.rpm_niche(category))
    one = P.per_video(prof, rpm, views, duration)
    n = videos_per_month if videos_per_month is not None else DEFAULT_VIDEOS_PER_MONTH
    return {"found": True, "target": {"niche": niche, "format": "short" if is_short else "long",
                                      "videosCompared": len(fmt)},
            "rpm": rpm, "rpmBasis": "niche estimate (dominant category)",
            "video": one,
            "month": P.per_month(prof, rpm, views * n, n, duration),
            "uploadsBasis": "set by you" if videos_per_month is not None
            else f"{DEFAULT_VIDEOS_PER_MONTH} videos a month (default)",
            "typicalVideo": "the niche's median views and length in its main format"}


def profit_estimate(channel_id: str = None, video_id: str = None, niche: str = None,
                    profile=None, videos_per_month: int = None,
                    user_id: int = LOCAL_USER_ID) -> dict:
    """Revenue range minus the costs of one cost profile (id or name; the
    first when omitted), for a channel (a month), a video (its lifetime) or
    a niche (the typical video, and a month of videos_per_month of them)."""
    given = [x for x in (channel_id, video_id, niche) if x]
    if len(given) != 1:
        raise ValueError("pass exactly one of channel_id, video_id, niche")
    if videos_per_month is not None and int(videos_per_month) < 0:
        raise ValueError("videos_per_month must be zero or more")
    vpm = None if videos_per_month is None else int(videos_per_month)
    prof = _profile(profile, user_id)
    if channel_id:
        out = _channel(channel_id, prof, user_id, vpm)
    elif video_id:
        out = _video(video_id, prof)
    else:
        out = _niche(niche, prof, vpm)
    out["profile"] = {k: prof[k] for k in ("id", "name", "per_video_usd", "per_minute_usd",
                                           "monthly_usd")}
    if prof["id"] is None:
        out["hint"] = ("no cost profile yet -- the costs are zero; add one "
                       "(save_cost_profile) to see profit")
    out["note"] = NOTE
    return out

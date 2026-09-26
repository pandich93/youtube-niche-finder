"""Every performance formula the tool uses, in one place.

Sources for the formulas (see docs/research-tools.md for the full write-up):
  * outlier multiplier      -- NexLev (mean baseline, verified numerically),
                               1of10 / OutlierKit / ViewStats (median baseline)
  * age normalisation       -- vidIQ "within a similar timeframe",
                               1of10 "published around the same time"
  * VPH / acceleration      -- vidIQ view velocity, computed from our own snapshots
  * revenue range           -- Social Blade's published $0.25-$4.00 per 1000 views
  * momentum / grade        -- Social Blade "recent growth vs all time growth"

The deliberate difference from NexLev: we default to a MEDIAN baseline over the
previous N uploads instead of a lifetime mean, because a single viral video
destroys a mean baseline (observed avg/median ratios up to ~27x).
"""
import math
import statistics as st
from datetime import datetime, timezone

# ------------------------------------------------------------------ outliers

DEFAULT_BASELINE_N = 10
SHORTS_MAX_SECONDS = 180  # YouTube raised the Shorts limit to 3 min in Oct 2024


def avg_channel_views(channel_view_count: int, channel_video_count: int) -> int:
    """NexLev's baseline: lifetime mean views per video (kept for parity)."""
    if not channel_video_count:
        return 0
    return int(channel_view_count) // int(channel_video_count)


def outlier_score(video_view_count: int, channel_view_count: int,
                  channel_video_count: int) -> float:
    """NexLev-compatible outlier score (views / lifetime mean views)."""
    avg = avg_channel_views(channel_view_count, channel_video_count)
    if avg <= 0:
        return 0.0
    return video_view_count / avg


def baseline_median(prior_views, n: int = DEFAULT_BASELINE_N) -> float | None:
    """Median of the last `n` prior uploads. None if too little data."""
    vals = [v for v in prior_views if v is not None][-n:]
    if len(vals) < 3:
        return None
    return float(st.median(vals))


def outlier_vs_median(video_views: int, prior_views, n: int = DEFAULT_BASELINE_N):
    base = baseline_median(prior_views, n)
    if not base or base <= 0:
        return None
    return video_views / base


def _parse_iso_dt(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def period_baseline_median(channel_videos, target_published_at, target_is_short: bool,
                           window_days: int = 15, min_videos: int = 5) -> float | None:
    """Median views of the channel's OTHER videos of the same format (Shorts
    vs long-form) published within +/- window_days of the target's own
    publish date -- "was this good against its actual season", as opposed to
    baseline_median()'s "against its own last N uploads" (a channel whose
    recent uploads were unusually big otherwise understates a good video).
    Falls back to the median across every video of that format on the
    channel when the window doesn't reach min_videos. None if the channel
    has no video of that format at all.

    channel_videos: [{"published_at": iso str, "view_count": int,
                      "is_short": bool}, ...] -- the channel's OTHER videos;
    whether to include the target itself is the caller's call, same
    convention as baseline_median()'s prior_views."""
    same_format = [v for v in channel_videos
                   if bool(v.get("is_short")) == bool(target_is_short)
                   and v.get("view_count") is not None]
    if not same_format:
        return None

    target_dt = _parse_iso_dt(target_published_at)
    if target_dt is not None:
        window_seconds = window_days * 86400
        in_window = [v["view_count"] for v in same_format
                    if (dt := _parse_iso_dt(v.get("published_at"))) is not None
                    and abs((dt - target_dt).total_seconds()) <= window_seconds]
        if len(in_window) >= min_videos:
            return float(st.median(in_window))

    return float(st.median(v["view_count"] for v in same_format))


OUTLIER_BANDS = [
    (1.5, "normal"),
    (2.0, "above average"),
    (3.0, "noteworthy"),
    (5.0, "strong outlier"),
    (10.0, "viral"),
    (float("inf"), "mega outlier"),
]


def outlier_band(score) -> str | None:
    if score is None:
        return None
    for threshold, label in OUTLIER_BANDS:
        if score < threshold:
            return label
    return "mega outlier"


# ------------------------------------------------------- age normalisation

# Share of a video's 30-day views typically accumulated by day t (long-form).
# This is the shipped default; once enough history exists the worker fits a
# curve from our own snapshots (fit_maturity_curve) and application code
# switches to it with set_maturity_curve().
MATURITY_CURVE = {0: 0.20, 1: 0.35, 2: 0.45, 3: 0.52, 5: 0.61,
                  7: 0.68, 10: 0.75, 14: 0.82, 21: 0.91, 30: 1.00}

# A fitted curve is only trusted if it has a point at each of these ages --
# they are where the curve bends and where most "is it early?" questions land.
# Day 0 is never observed (no snapshot at the moment of publication), so the
# fitted curve keeps the shipped day-0 floor, capped at the fitted day 1.
REQUIRED_CURVE_AGES = (1, 2, 3, 5, 7, 14, 21)
# Views are interpolated to whole days only between snapshots at most this
# many days apart; a longer gap (worker down) leaves those days unmeasured.
MAX_SNAPSHOT_GAP_DAYS = 3

_active_curve = None


def set_maturity_curve(curve):
    """Make `curve` ({age_days: share}) what maturity() uses by default;
    None goes back to the shipped MATURITY_CURVE."""
    global _active_curve
    _active_curve = dict(curve) if curve else None


def active_maturity_curve() -> dict:
    return _active_curve or MATURITY_CURVE


def _views_at_day_30(points):
    """30-day views of one video: interpolated between the snapshots either
    side of day 30 when one lies past it, else the latest snapshot at 28+
    days (a slight underestimate, but never an extrapolation)."""
    after = [p for p in points if p[0] >= 30]
    before = [p for p in points if p[0] < 30]
    if after and before:
        (a0, v0), (a1, v1) = before[-1], after[0]
        return v0 + (30 - a0) / (a1 - a0) * (v1 - v0) if a1 > a0 else v1
    if after:
        return after[0][1]
    return before[-1][1] if before and before[-1][0] >= 28 else None


def fit_maturity_curve(histories: dict, min_videos: int = 30,
                       min_samples_per_age: int = 20,
                       required_ages=REQUIRED_CURVE_AGES) -> dict:
    """Fit MATURITY_CURVE from snapshot histories {video_id: [(age_days,
    views), ...]}.

    Only videos watched from publication count: first snapshot at <=1 day and
    one at >=28 days, so every video contributes its own early days rather
    than an old video's plateau. Each video's views are interpolated to whole
    days 1..29 (only between snapshots <= MAX_SNAPSHOT_GAP_DAYS apart) and
    divided by its day-30 views; a day's point is the median share and needs
    `min_samples_per_age` videos. The result is made non-decreasing and
    capped at 1.0 (noise can dip it), with 30 -> 1.0 and day 0 taken from the
    shipped curve, capped at day 1.
    `calibrated` is True only with >= `min_videos` videos and a point at every
    age in `required_ages`; otherwise `reason` says what is missing and
    no `curve` is returned."""
    samples = {}
    used = 0
    for points in histories.values():
        points = sorted(points)
        if not points or points[0][0] > 1 or points[-1][0] < 28:
            continue
        base = _views_at_day_30(points)
        if not base or base <= 0:
            continue
        used += 1
        per_day = {}
        for (a0, v0), (a1, v1) in zip(points, points[1:]):
            if a1 <= a0 or a1 - a0 > MAX_SNAPSHOT_GAP_DAYS:
                continue
            for day in range(max(1, math.ceil(a0)), min(math.floor(a1), 29) + 1):
                per_day.setdefault(day, v0 + (day - a0) / (a1 - a0) * (v1 - v0))
        for day, views in per_day.items():
            samples.setdefault(day, []).append(views / base)

    counts = {day: len(v) for day, v in sorted(samples.items())}
    curve, running = {}, 0.0
    for day in sorted(samples):
        if counts[day] < min_samples_per_age:
            continue
        running = min(1.0, max(running, st.median(samples[day])))
        curve[day] = round(running, 3)
    curve[30] = 1.0
    curve[0] = min(MATURITY_CURVE[0], curve.get(1, MATURITY_CURVE[0]))
    curve = dict(sorted(curve.items()))

    missing = [a for a in required_ages if a not in curve]
    out = {"calibrated": False, "videosUsed": used, "samplesPerAge": counts,
           "missingAges": missing, "minVideos": min_videos,
           "minSamplesPerAge": min_samples_per_age}
    if used < min_videos:
        out["reason"] = (f"needs {min_videos} videos tracked from publication "
                         f"to 28+ days, has {used}")
    elif missing:
        out["reason"] = (f"fewer than {min_samples_per_age} videos at ages "
                         f"{missing}")
    else:
        out["calibrated"] = True
        out["curve"] = curve
    return out


def maturity(age_days: float, curve: dict = None) -> float:
    """Fraction of 30-day views a video of this age is expected to have."""
    curve = curve or active_maturity_curve()
    if age_days is None:
        return 1.0
    if age_days >= 30:
        return 1.0
    if age_days <= 0:
        return curve[0]
    keys = sorted(curve)
    for a, b in zip(keys, keys[1:]):
        if a <= age_days <= b:
            w = (age_days - a) / (b - a) if b > a else 0.0
            return curve[a] + w * (curve[b] - curve[a])
    return curve[keys[0]]


def projected_30d_views(views: int, age_days: float) -> float:
    """Views normalised to a common 30-day horizon, so a 2-day-old video can be
    compared fairly against a 3-week-old one."""
    return views / max(maturity(age_days), 0.05)


def age_adjusted_outlier(views: int, baseline: float, age_days: float):
    if not baseline or baseline <= 0:
        return None
    return views / (baseline * max(maturity(age_days), 0.05))


# --------------------------------------------------------------- ratios

def views_per_subscriber(views: int, subscribers) -> float:
    """VSR. >1 means the video reached far beyond the channel's own audience;
    this is the metric that actually powers 'viral videos on small channels'."""
    return views / max(int(subscribers or 0), 1)


def engagement_rate(views: int, likes, comments) -> float | None:
    """(likes + comments) / views, in %. None when likes are hidden."""
    if not views:
        return None
    if likes is None and comments is None:
        return None
    return round(((likes or 0) + (comments or 0)) / views * 100, 3)


def like_rate(views: int, likes) -> float | None:
    if not views or likes is None:
        return None
    return round(likes / views * 100, 3)


def comment_rate(views: int, comments) -> float | None:
    if not views or comments is None:
        return None
    return round(comments / views * 100, 4)


def is_short(duration_seconds) -> bool:
    return bool(duration_seconds) and duration_seconds <= SHORTS_MAX_SECONDS


# ----------------------------------------------------------------- velocity

def vph_lifetime(views: int, hours_old: float) -> float:
    return views / max(hours_old, 1.0)


def vph_from_history(rows, hours: float = 24.0):
    """rows: [(captured_at_dt, view_count)] ascending. Returns views/hour over
    the most recent `hours` window, or None when history is too thin."""
    if not rows or len(rows) < 2:
        return None
    latest_t, latest_v = rows[-1]
    target = latest_t.timestamp() - hours * 3600
    prev = None
    for t, v in rows[:-1]:
        if t.timestamp() <= target:
            prev = (t, v)
    if prev is None:
        prev = rows[0]
    dt_hours = (latest_t.timestamp() - prev[0].timestamp()) / 3600
    if dt_hours <= 0:
        return None
    return max(0.0, (latest_v - prev[1]) / dt_hours)


def acceleration(vph_now, vph_before):
    """>1.5 == the video is heating up; <0.7 == cooling down."""
    if not vph_now or not vph_before:
        return None
    return round(vph_now / max(vph_before, 0.01), 2)


# ------------------------------------------------------------------ revenue

# Social Blade publishes this range explicitly.
SB_RPM_LOW, SB_RPM_HIGH = 0.25, 4.00

# NexLev-style niche RPM (their rpm.base), discounted by the ~0.70 factor
# observed in their own responses (rpm.total / rpm.base = 0.68-0.73).
NICHE_RPM = {
    "finance": 25.0, "business": 22.0, "ecommerce": 25.0, "insurance": 24.0,
    "software": 18.0, "tech": 12.0, "education": 9.0, "health": 10.0,
    "travel": 8.0, "food": 6.0, "news": 6.0, "religion": 6.6, "sports": 5.0,
    "music": 3.0, "gaming": 3.8, "entertainment": 4.0, "kids": 2.0, "default": 5.0,
}
MONETISATION_DISCOUNT = 0.70


def revenue_range(monthly_views: int) -> dict:
    return {
        "low_usd": round(monthly_views / 1000 * SB_RPM_LOW, 2),
        "high_usd": round(monthly_views / 1000 * SB_RPM_HIGH, 2),
        "model": "Social Blade published range $0.25-$4.00 per 1000 views",
    }


def rpm_effective(niche: str = "default") -> float:
    """Effective RPM (after the monetisation discount) for a NexLev-style
    niche label -- the per-video/per-category number search filters compare
    against, and the building block revenue_niche() uses for a full estimate."""
    rpm_base = NICHE_RPM.get((niche or "default").lower(), NICHE_RPM["default"])
    return round(rpm_base * MONETISATION_DISCOUNT, 3)


def revenue_niche(monthly_views: int, niche: str = "default") -> dict:
    rpm_base = NICHE_RPM.get((niche or "default").lower(), NICHE_RPM["default"])
    rpm_total = rpm_effective(niche)
    return {
        "rpm_base": rpm_base,
        "rpm_effective": rpm_total,
        "monthly_usd": round(monthly_views / 1000 * rpm_total, 2),
        "model": "monthly_views/1000 * niche RPM * 0.70 monetisation discount",
    }


# ------------------------------------------------------------ channel growth

def growth(now_value, then_value, days: float):
    if now_value is None or then_value is None or days <= 0:
        return None
    delta = now_value - then_value
    return {
        "delta": delta,
        "per_day": round(delta / days, 2),
        "percent": round(delta / then_value * 100, 3) if then_value else None,
    }


def project(current, per_day, days):
    if current is None or per_day is None:
        return None
    return int(current + per_day * days)


GRADES = ["D", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+", "A++"]


def momentum(recent_per_day, lifetime_per_day):
    """Social Blade's stated core idea: recent growth vs all-time growth."""
    if not lifetime_per_day or lifetime_per_day <= 0 or recent_per_day is None:
        return None
    return round(recent_per_day / lifetime_per_day, 3)


def grade(momentum_value):
    if momentum_value is None or momentum_value <= 0:
        return None
    idx = int(math.log2(max(momentum_value, 0.06)) * 2) + 5
    return GRADES[max(0, min(len(GRADES) - 1, idx))]


def skew(values):
    """mean/median ratio -- how much a channel depends on rare viral hits."""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return None
    med = st.median(vals)
    if med <= 0:
        return None
    return round(st.mean(vals) / med, 2)


def uploads_per_week(video_count: int, days_active: float) -> float | None:
    if not days_active or days_active <= 0:
        return None
    return round(video_count / (days_active / 7.0), 3)


def safe_median(values):
    vals = [v for v in values if v is not None]
    return st.median(vals) if vals else None

"""Time-window parsing shared by every 'за последние 24 часа / 7 дней' feature."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_PACIFIC = ZoneInfo("America/Los_Angeles")

_RE = re.compile(r"^\s*(\d+)\s*([hdwm])\s*$", re.IGNORECASE)

ALIASES = {
    "24h": "24h", "day": "24h", "today": "24h", "сутки": "24h",
    "48h": "48h", "2d": "48h",
    "week": "7d", "неделя": "7d",
    "month": "30d", "месяц": "30d",
    "quarter": "90d",
    "all": "all", "alltime": "all", "все": "all",
}

PRESETS = ["1h", "6h", "24h", "48h", "7d", "14d", "30d", "60d", "90d", "180d", "365d", "all"]


def parse_period(period: str) -> timedelta | None:
    """'24h' -> timedelta(hours=24). 'all'/None -> None (no lower bound)."""
    if period is None:
        return None
    p = ALIASES.get(str(period).strip().lower(), str(period).strip().lower())
    if p in ("all", "none", ""):
        return None
    m = _RE.match(p)
    if not m:
        raise ValueError(
            f"Bad period {period!r}. Use e.g. 24h, 48h, 7d, 30d, 90d, all "
            f"(known presets: {', '.join(PRESETS)})"
        )
    n, unit = int(m.group(1)), m.group(2).lower()
    return {
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
        "m": timedelta(days=30 * n),
    }[unit]


def now():
    return datetime.now(timezone.utc)


def cutoff(period: str, ref: datetime = None) -> datetime | None:
    """Start of the window, or None for 'all'."""
    delta = parse_period(period)
    if delta is None:
        return None
    return (ref or now()) - delta


def window(period: str, ref: datetime = None, offset: int = 0):
    """(start, end) ISO strings for a window shifted `offset` periods into the past.

    offset=0 -> the current window, offset=1 -> the immediately preceding one
    (used for period-over-period growth). Returns (None, iso_end) for 'all'.
    """
    ref = ref or now()
    delta = parse_period(period)
    if delta is None:
        return None, ref.isoformat()
    end = ref - delta * offset
    start = end - delta
    return start.isoformat(), end.isoformat()


def hours_since(iso_ts: str, ref: datetime = None) -> float:
    if not iso_ts:
        return 0.0
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, ((ref or now()) - ts).total_seconds() / 3600.0)


def days_since(iso_ts: str, ref: datetime = None) -> float:
    return hours_since(iso_ts, ref) / 24.0


def to_rfc3339(dt: datetime) -> str:
    """YouTube API wants 1970-01-01T00:00:00Z style."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pacific_date_key(ref: datetime = None) -> str:
    """YYYY-MM-DD in America/Los_Angeles -- YouTube/Google API daily quotas
    (e.g. search.list's 100-calls/day bucket) reset at midnight Pacific,
    not UTC."""
    return (ref or now()).astimezone(_PACIFIC).strftime("%Y-%m-%d")

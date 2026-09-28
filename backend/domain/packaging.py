"""Pure formulas for "repackaging" -- a creator swapping a video's title or
thumbnail after publishing (plan 05). No DB, no network.

Thumbnail changes cannot be seen in the Data API: a video's thumbnail URL is
fixed (i.ytimg.com/vi/<id>/mqdefault.jpg) and simply starts serving a new
image. So application/packaging.py fingerprints the image itself with a
64-bit difference hash (infrastructure/thumbnails.py), and this module only
compares fingerprints and measures what happened to views around a change.
"""
from datetime import datetime, timedelta, timezone

# dHash distance (out of 64 bits) above which two fingerprints are a
# different image rather than the same one re-encoded by the CDN. JPEG
# re-encoding and resizing move a handful of bits; a new design moves dozens.
THUMB_CHANGE_BITS = 10
EFFECT_WINDOW_HOURS = 48


def _dt(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def hamming(a_hex, b_hex):
    """Differing bits between two hex fingerprints, None if either is unusable."""
    if not a_hex or not b_hex:
        return None
    try:
        return bin(int(a_hex, 16) ^ int(b_hex, 16)).count("1")
    except ValueError:
        return None


def thumbnail_changed(old_hash, new_hash, threshold: int = THUMB_CHANGE_BITS) -> bool:
    """True when new_hash is a different image from old_hash. The first
    fingerprint of a video (old_hash None) is a baseline, never a change."""
    d = hamming(old_hash, new_hash)
    return d is not None and d > threshold


def _vph(points):
    """Views per hour between the first and last (captured_at, views) point."""
    if len(points) < 2:
        return None
    (t0, v0), (t1, v1) = points[0], points[-1]
    hours = (t1 - t0).total_seconds() / 3600
    if hours <= 0:
        return None
    return round(max(0, (v1 or 0) - (v0 or 0)) / hours, 1)


def change_effect(history, changed_at: str, window_hours: float = EFFECT_WINDOW_HOURS) -> dict:
    """Views per hour in the window before a change vs the window after it.

    history: [(captured_at_iso, view_count), ...] in any order. The snapshot
    taken at the change itself closes the "before" side and opens the
    "after" side. Each side needs two snapshots; otherwise enoughData=False.
    This is an observation, not a cause: a video's views also decay on their
    own with age, which the caller should say next to the number."""
    at = _dt(changed_at)
    pts = sorted((d, v) for d, v in ((_dt(ts), v) for ts, v in history or []) if d)
    empty = {"vphBefore": None, "vphAfter": None, "ratio": None, "enoughData": False,
             "windowHours": window_hours}
    if not at:
        return empty
    w = timedelta(hours=window_hours)
    before = _vph([p for p in pts if at - w <= p[0] <= at])
    after = _vph([p for p in pts if at <= p[0] <= at + w])
    ratio = round(after / before, 2) if before and after is not None else None
    return {"vphBefore": before, "vphAfter": after, "ratio": ratio,
            "enoughData": before is not None and after is not None,
            "windowHours": window_hours}

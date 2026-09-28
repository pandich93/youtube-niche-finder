"""Pure event-detection formulas for the alerts worker (plan item 8.9).

Each detector takes already-loaded rows and a threshold and returns candidate
events: {"kind", "refId", "payload"}. No DB access and no dedup here --
application/alerts.py checks the `events` table before inserting, so a
detector can run every worker cycle without ever producing a duplicate row.
That's also why `refId` is not always a bare video/channel id: for anything
that can legitimately recur for the same subject (a video re-titled twice, an
acceleration spike that cools down and later spikes again), the timestamp of
the specific occurrence is folded into refId so each real occurrence gets its
own dedupe key, while a "first time ever" event (a video crossing the outlier
bar) dedupes on the bare id -- it only ever needs to fire once.
"""
from datetime import datetime, timezone

OUTLIER_THRESHOLD_DEFAULT = 3.0
ACCELERATION_THRESHOLD_DEFAULT = 2.0
SILENCE_DAYS_DEFAULT = 14
# A channel/video the API stops returning is only "gone" after this many
# misses spread over at least this many hours -- one flaky response (or a
# partial batch) must never page anyone.
GONE_MIN_MISSES = 2
GONE_MIN_HOURS = 6.0


def _dt(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def detect_outliers(rows, threshold: float = OUTLIER_THRESHOLD_DEFAULT) -> list:
    """rows: [{"video_id","title","channel_id","view_count","outlier_score"}].
    One candidate per video whose outlier score is at/above threshold. The
    caller dedupes by (kind, refId) so this lands once per video, on
    whichever cycle first sees it cross the bar."""
    out = []
    for r in rows:
        score = r.get("outlier_score")
        if score is not None and score >= threshold:
            out.append({
                "kind": "outlier", "refId": r["video_id"],
                "payload": {"videoId": r["video_id"], "title": r.get("title"),
                            "channelId": r.get("channel_id"),
                            "outlierScore": round(score, 2),
                            "views": r.get("view_count")},
            })
    return out


def detect_acceleration(rows, threshold: float = ACCELERATION_THRESHOLD_DEFAULT) -> list:
    """rows: [{"video_id","title","channel_id","acceleration","vph24h","captured_at"}].
    `captured_at` (the snapshot the acceleration was computed from) folds into
    refId, so the same video can alert again on a later snapshot once it has
    cooled down and re-accelerated -- unlike detect_outliers, this is not a
    once-ever fact about the video."""
    out = []
    for r in rows:
        acc = r.get("acceleration")
        if acc is not None and acc >= threshold:
            ref = f"{r['video_id']}:{r.get('captured_at') or ''}"
            out.append({
                "kind": "acceleration", "refId": ref,
                "payload": {"videoId": r["video_id"], "title": r.get("title"),
                            "channelId": r.get("channel_id"),
                            "acceleration": round(acc, 2),
                            "vph24h": r.get("vph24h"),
                            "capturedAt": r.get("captured_at")},
            })
    return out


def detect_title_changes(rows) -> list:
    """rows: [{"video_id","channel_id","changed_at","old_value","new_value"}],
    straight from video_changes WHERE field='title'. One candidate per row;
    refId folds in changed_at since a video can be retitled more than once
    and each retitling is its own event."""
    out = []
    for r in rows:
        ref = f"{r['video_id']}:{r.get('changed_at') or ''}"
        out.append({
            "kind": "title_change", "refId": ref,
            "payload": {"videoId": r["video_id"], "channelId": r.get("channel_id"),
                        "changedAt": r.get("changed_at"),
                        "oldTitle": r.get("old_value"), "newTitle": r.get("new_value")},
        })
    return out


def detect_silence_breaks(channel_uploads: dict, silence_days: float = SILENCE_DAYS_DEFAULT) -> list:
    """channel_uploads: {channel_id: [(published_at_iso, video_id, title), ...]},
    any order. Flags the first upload after a gap of >= silence_days since
    that SAME channel's own previous upload -- "went quiet, then came back",
    which needs at least two uploads to even define. A channel that always
    posts every N >= silence_days days would alert on every video under this
    flat rule; comparing against that channel's own typical gap instead of a
    fixed number would fix that, but is deliberately left for a later pass --
    see docs/plan-iteration-8.md 8.9."""
    out = []
    for channel_id, uploads in (channel_uploads or {}).items():
        ordered = sorted(uploads, key=lambda u: u[0] or "")
        for i in range(1, len(ordered)):
            prev_pub = _dt(ordered[i - 1][0])
            cur_pub, cur_id, cur_title = ordered[i]
            cur_dt = _dt(cur_pub)
            if not prev_pub or not cur_dt:
                continue
            gap_days = (cur_dt - prev_pub).total_seconds() / 86400
            if gap_days >= silence_days:
                out.append({
                    "kind": "silence_break", "refId": cur_id,
                    "payload": {"videoId": cur_id, "title": cur_title,
                                "channelId": channel_id,
                                "gapDays": round(gap_days, 1)},
                })
    return out


def gone_transition(state, missing: bool, now_iso: str,
                    min_misses: int = GONE_MIN_MISSES,
                    min_hours: float = GONE_MIN_HOURS):
    """One step of the "is it gone?" state for a single channel/video.

    state: None (not a candidate) or {"first_missing_at", "last_missing_at",
    "miss_count", "confirmed_at"}; returns the next state, or None when the
    item was returned by the API again (a transient miss, drop it). Only ever
    called after a SUCCESSFUL API call -- a failed call (quota, network) is
    not evidence of anything, see application/collecting.py::_track_gone.
    Confirmation needs both enough misses and enough time between the first
    and the latest one, and sticks once reached."""
    if not missing:
        return None
    if state is None:
        state = {"first_missing_at": now_iso, "last_missing_at": now_iso,
                 "miss_count": 1, "confirmed_at": None}
    else:
        state = dict(state, last_missing_at=now_iso,
                     miss_count=(state.get("miss_count") or 0) + 1)
    if not state.get("confirmed_at") and state["miss_count"] >= min_misses:
        first, cur = _dt(state["first_missing_at"]), _dt(now_iso)
        if first and cur and (cur - first).total_seconds() / 3600 >= min_hours:
            state["confirmed_at"] = now_iso
    return state


def detect_gone(rows) -> list:
    """rows: gone_items joined with the last known numbers --
    channel: {"kind": "channel", "ref_id", "first_missing_at", "confirmed_at",
              "title", "subscriber_count", "view_count", "video_count", "last_seen_at"}
    video:   {"kind": "video", "ref_id", "first_missing_at", "confirmed_at",
              "title", "channel_id", "view_count", "outlier_score"}.
    Unconfirmed rows are skipped. refId folds in first_missing_at, so a
    channel that comes back and later disappears again alerts a second time."""
    out = []
    for r in rows:
        if not r.get("confirmed_at"):
            continue
        ref = f"{r['ref_id']}:{r.get('first_missing_at') or ''}"
        if r["kind"] == "channel":
            out.append({
                "kind": "channel_gone", "refId": ref,
                "payload": {"channelId": r["ref_id"], "title": r.get("title"),
                            "subscribers": r.get("subscriber_count"),
                            "views": r.get("view_count"),
                            "videoCount": r.get("video_count"),
                            "lastSeenAt": r.get("last_seen_at"),
                            "goneSince": r.get("first_missing_at")},
            })
        elif r["kind"] == "video":
            out.append({
                "kind": "video_gone", "refId": ref,
                "payload": {"videoId": r["ref_id"], "title": r.get("title"),
                            "channelId": r.get("channel_id"),
                            "views": r.get("view_count"),
                            "outlierScore": r.get("outlier_score"),
                            "goneSince": r.get("first_missing_at")},
            })
    return out

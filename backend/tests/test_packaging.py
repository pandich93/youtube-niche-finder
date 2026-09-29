"""Tests for application/packaging.py (plan 05): thumbnail fingerprinting for
tracked channels, the repackaging feed and per-video history, and serving
archived images. Same throwaway-schema setup as test_mcp_tools.py
(tests/schema_scope.py); images are generated with Pillow and
infrastructure.thumbnails.fetch_thumbnail is monkeypatched -- no network.
Run with pytest, or directly: python3 tests/test_packaging.py
"""
import io
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

from PIL import Image, ImageDraw  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.thumbnails as TH  # noqa: E402
from application import packaging as PKG  # noqa: E402

NOW = datetime.now(timezone.utc)
CH = "UC" + "packtracked".ljust(22, "0")
CH_OTHER = "UC" + "packuntracked".ljust(22, "0")


def _img(color, stripes=False):
    img = Image.new("RGB", (320, 180), color)
    d = ImageDraw.Draw(img)
    if stripes:
        for x in range(0, 320, 40):
            d.rectangle((x, 0, x + 20, 180), fill=(10, 60, 200))
    else:
        d.rectangle((10, 10, 150, 170), fill=(230, 40, 40))
        d.ellipse((190, 40, 300, 150), fill=(250, 220, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


DESIGN_A = _img((20, 20, 20))
DESIGN_B = _img((240, 240, 240), stripes=True)


def setup_module(_=None):
    db.init_db()


def _reset():
    conn = db.get_conn()
    for t in ("thumbnail_archive", "video_changes", "video_stats_history", "videos",
              "channels", "tracked_channels"):
        conn.execute(f"DELETE FROM {t}")
    now = NOW.isoformat()
    for cid in (CH, CH_OTHER):
        conn.execute("INSERT INTO channels (channel_id, title) VALUES (?,?)", (cid, cid[:14]))
    conn.execute("INSERT INTO tracked_channels (channel_id, added_at, active) VALUES (?,?,1)",
                 (CH, now))
    for vid, cid, days in (("pv_new", CH, 2), ("pv_new2", CH, 5), ("pv_old", CH, 90),
                           ("pv_other", CH_OTHER, 2)):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count) "
                     "VALUES (?,?,?,?,?)",
                     (vid, cid, f"title {vid}", (NOW - timedelta(days=days)).isoformat(), 1000))
    conn.commit()
    conn.close()


def _serve(monkeypatch, images: dict):
    seen = []

    def fake(video_id):
        seen.append(video_id)
        return images.get(video_id)
    monkeypatch.setattr(TH, "fetch_thumbnail", fake)
    return seen


def _rows(sql, params=()):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


# ------------------------------------------------------------ fingerprinting

def test_first_pass_baselines_only_recent_videos_of_tracked_channels(monkeypatch):
    _reset()
    seen = _serve(monkeypatch, {"pv_new": DESIGN_A, "pv_new2": DESIGN_A})
    out = PKG.fingerprint_thumbnails(period="30d", pause_seconds=0)
    assert sorted(seen) == ["pv_new", "pv_new2"]          # not pv_old, not pv_other
    assert out["checked"] == 2 and out["baselined"] == 2 and out["changed"] == 0, out
    assert len(_rows("SELECT * FROM thumbnail_archive")) == 2
    assert _rows("SELECT * FROM video_changes") == []


def test_same_image_again_adds_nothing(monkeypatch):
    _reset()
    _serve(monkeypatch, {"pv_new": DESIGN_A, "pv_new2": DESIGN_A})
    PKG.fingerprint_thumbnails(pause_seconds=0)
    out = PKG.fingerprint_thumbnails(pause_seconds=0)
    assert out["baselined"] == 0 and out["changed"] == 0, out
    assert len(_rows("SELECT * FROM thumbnail_archive")) == 2


def test_new_design_is_archived_and_logged_as_a_change(monkeypatch):
    _reset()
    _serve(monkeypatch, {"pv_new": DESIGN_A, "pv_new2": DESIGN_A})
    PKG.fingerprint_thumbnails(pause_seconds=0)
    _serve(monkeypatch, {"pv_new": DESIGN_B, "pv_new2": DESIGN_A})
    out = PKG.fingerprint_thumbnails(pause_seconds=0)
    assert out["changed"] == 1, out
    versions = _rows("SELECT * FROM thumbnail_archive WHERE video_id='pv_new' "
                     "ORDER BY captured_at")
    assert len(versions) == 2
    ch = _rows("SELECT * FROM video_changes WHERE video_id='pv_new'")
    assert len(ch) == 1 and ch[0]["field"] == "thumbnail_image"
    assert ch[0]["old_value"] == versions[0]["dhash"]
    assert ch[0]["new_value"] == versions[1]["dhash"]
    assert ch[0]["changed_at"] == versions[1]["captured_at"]


def test_unreachable_thumbnail_is_counted_not_raised(monkeypatch):
    _reset()
    _serve(monkeypatch, {"pv_new": DESIGN_A})               # pv_new2 -> None
    out = PKG.fingerprint_thumbnails(pause_seconds=0)
    assert out["failed"] == 1 and out["baselined"] == 1, out


# ------------------------------------------------------------ feed / history

def _history(vid, points):
    conn = db.get_conn()
    for hours_ago, views in points:
        conn.execute("INSERT INTO video_stats_history (video_id, captured_at, view_count) "
                     "VALUES (?,?,?)", (vid, (NOW - timedelta(hours=hours_ago)).isoformat(), views))
    conn.commit()
    conn.close()


def _title_change(vid, hours_ago, old, new):
    conn = db.get_conn()
    conn.execute("INSERT INTO video_changes (video_id, changed_at, field, old_value, new_value) "
                 "VALUES (?,?,?,?,?)",
                 (vid, (NOW - timedelta(hours=hours_ago)).isoformat(), "title", old, new))
    conn.commit()
    conn.close()


def test_feed_lists_title_and_thumbnail_changes_with_images_and_effect(monkeypatch):
    _reset()
    _serve(monkeypatch, {"pv_new": DESIGN_A})
    PKG.fingerprint_thumbnails(pause_seconds=0)
    _serve(monkeypatch, {"pv_new": DESIGN_B})
    PKG.fingerprint_thumbnails(pause_seconds=0)
    _title_change("pv_new2", 10, "Old title", "New title")
    _history("pv_new2", [(20, 1000), (10, 2000), (0, 7000)])

    feed = PKG.packaging_feed(period="30d")
    assert feed["count"] == 2
    by_field = {c["field"]: c for c in feed["changes"]}
    thumb, title = by_field["thumbnail_image"], by_field["title"]
    assert thumb["videoId"] == "pv_new" and thumb["channelId"] == CH
    assert thumb["beforeImage"].startswith("/api/thumbnails/pv_new/")
    assert thumb["afterImage"].startswith("/api/thumbnails/pv_new/")
    assert thumb["beforeImage"] != thumb["afterImage"]
    assert title["old"] == "Old title" and title["new"] == "New title"
    assert title["effect"]["vphBefore"] == 100.0 and title["effect"]["vphAfter"] == 500.0
    assert "note" in feed

    only_titles = PKG.packaging_feed(period="30d", field="title")
    assert [c["field"] for c in only_titles["changes"]] == ["title"]
    other = PKG.packaging_feed(period="30d", channel_id=CH_OTHER)
    assert other["count"] == 0


def test_feed_for_a_user_lists_only_channels_on_their_active_watchlist():
    # plan 15: a user's digest must not carry swaps from channels only
    # somebody else tracks
    _reset()
    _title_change("pv_new2", 10, "Old", "New")
    _title_change("pv_other", 10, "Old other", "New other")
    conn = db.get_conn()
    conn.execute("INSERT INTO users (id, email, password_hash, created_at) "
                 "VALUES (2, 'pkg2@example.com', 'x', ?) ON CONFLICT DO NOTHING", (NOW.isoformat(),))
    conn.execute("INSERT INTO tracked_channels (user_id, channel_id, added_at, active) "
                 "VALUES (2, ?, ?, 1)", (CH_OTHER, NOW.isoformat()))
    conn.commit()
    conn.close()

    assert PKG.packaging_feed(period="30d")["count"] == 2
    mine = PKG.packaging_feed(period="30d", user_id=1)["changes"]
    theirs = PKG.packaging_feed(period="30d", user_id=2)["changes"]
    assert [c["videoId"] for c in mine] == ["pv_new2"]
    assert [c["videoId"] for c in theirs] == ["pv_other"]

    conn = db.get_conn()
    conn.execute("UPDATE tracked_channels SET active = 0 WHERE user_id = 2")
    conn.commit()
    conn.close()
    assert PKG.packaging_feed(period="30d", user_id=2)["count"] == 0


def test_feed_ignores_url_based_thumbnail_rows():
    # record_video_stats' URL comparison may log field='thumbnail' -- that is
    # not an image change and must not show up as one.
    _reset()
    conn = db.get_conn()
    conn.execute("INSERT INTO video_changes (video_id, changed_at, field, old_value, new_value) "
                 "VALUES (?,?,?,?,?)", ("pv_new", NOW.isoformat(), "thumbnail",
                                        "https://a/hq.jpg", "https://a/maxres.jpg"))
    conn.commit()
    conn.close()
    assert PKG.packaging_feed(period="30d")["count"] == 0


def test_history_lists_versions_and_changes_for_one_video(monkeypatch):
    _reset()
    _serve(monkeypatch, {"pv_new": DESIGN_A})
    PKG.fingerprint_thumbnails(pause_seconds=0)
    _serve(monkeypatch, {"pv_new": DESIGN_B})
    PKG.fingerprint_thumbnails(pause_seconds=0)
    _title_change("pv_new", 1, "a", "b")
    h = PKG.packaging_history("pv_new")
    assert h["videoId"] == "pv_new"
    assert h["count"] == 2
    assert len(h["thumbnails"]) == 2
    assert all(t["image"].startswith("/api/thumbnails/pv_new/") for t in h["thumbnails"])


def test_thumbnail_image_returns_the_archived_bytes(monkeypatch):
    _reset()
    _serve(monkeypatch, {"pv_new": DESIGN_A})
    PKG.fingerprint_thumbnails(pause_seconds=0)
    row = _rows("SELECT captured_at FROM thumbnail_archive WHERE video_id='pv_new'")[0]
    assert PKG.thumbnail_image("pv_new", row["captured_at"]) == DESIGN_A
    assert PKG.thumbnail_image("pv_new", "1999-01-01T00:00:00+00:00") is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

"""Multi-user plan P1-05: feeds gathered for every tracked channel do not show
what other users watch. With accounts on, title/thumbnail changes, the
repackaging feed, the watchlist and change totals and the digest's rising
channels are limited to the user's own watchlist (and, for rising channels,
its niches); /api/health tells a visitor who did not sign in only that the
server is up. Without accounts everything stays whole-server, as before.
Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_feeds_scoped.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import channel_tracking as T  # noqa: E402
from application import digest as DG  # noqa: E402
from application import packaging as PKG  # noqa: E402
from application import search as Q  # noqa: E402

NOW = datetime.now(timezone.utc)
CH_A, CH_B, CH_NICHE = ("UC" + s.ljust(22, "0") for s in ("feedsa", "feedsb", "feedsn"))
PW = "correct horse battery"


def setup_module(_=None):
    db.init_db()


@pytest.fixture
def users(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    conn = db.get_conn()
    for t in ("tracked_channels", "video_changes", "video_niches", "sessions", "login_attempts",
              "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("INSERT INTO niches (slug, query, label) VALUES ('feeds-niche', 'q', 'l') "
                 "ON CONFLICT (slug) DO NOTHING")
    for i, cid in enumerate((CH_A, CH_B, CH_NICHE)):
        conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                     (cid, cid[2:8], 1000))
        vid = f"feedsv{i}"
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count) "
                     "VALUES (?,?,?,?,?)", (vid, cid, f"t{i}", (NOW - timedelta(days=3)).isoformat(), 900))
        conn.execute("INSERT INTO video_changes (video_id, changed_at, field, old_value, new_value) "
                     "VALUES (?,?,?,?,?)", (vid, (NOW - timedelta(days=1)).isoformat(), "title",
                                            f"old {i}", f"new {i}"))
    # A's channel and CH_NICHE share a niche; B's channel is elsewhere
    for vid in ("feedsv0", "feedsv2"):
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?, 'feeds-niche')", (vid,))
    conn.commit()
    conn.close()
    a, b = AUTH.create_user("fa@example.com", PW), AUTH.create_user("fb@example.com", PW)
    T.track(CH_A, None, user_id=a)
    T.track(CH_B, None, user_id=b)
    monkeypatch.setenv("NF_MULTI_USER", "1")
    return a, b


def _channels(rows):
    return {r["channel_id"] for r in rows}


def test_title_changes_show_only_your_watchlist(users):
    a, b = users
    assert _channels(T.title_changes(period="7d", user_id=a)["changes"]) == {CH_A}
    assert _channels(T.title_changes(period="7d", user_id=b)["changes"]) == {CH_B}
    assert _channels(T.title_changes(period="7d", user_id=None)["changes"]) == {CH_A, CH_B, CH_NICHE}


def test_the_repackaging_feed_shows_only_your_watchlist(users):
    a, _ = users
    mine = PKG.packaging_feed(period="7d", user_id=a)["changes"]
    everyone = PKG.packaging_feed(period="7d", user_id=None)["changes"]
    assert {c["channelId"] for c in mine} == {CH_A}
    assert {c["channelId"] for c in everyone} == {CH_A, CH_B, CH_NICHE}


def test_totals_count_only_your_watchlist(users):
    a, _ = users
    mine, everyone = Q.db_stats(user_id=a), Q.db_stats(user_id=None)
    assert mine["tracked_channels"] == 1 and everyone["tracked_channels"] == 2
    assert mine["title_thumbnail_changes"] == 1 and everyone["title_thumbnail_changes"] == 3


def test_digest_rising_channels_are_your_channels_and_their_niches(users, monkeypatch):
    a, b = users
    rising = [{"channelId": c, "multiplier": 3.0} for c in (CH_A, CH_B, CH_NICHE)]
    monkeypatch.setattr(T, "recently_added_outlier_channels",
                        lambda **kw: {"channels": rising, "channelsMatched": 3})
    got_a = {c["channelId"] for c in DG.build_digest(period="7d", user_id=a)["risingChannels"]["items"]}
    got_b = {c["channelId"] for c in DG.build_digest(period="7d", user_id=b)["risingChannels"]["items"]}
    assert got_a == {CH_A, CH_NICHE} and got_b == {CH_B}
    monkeypatch.setenv("NF_MULTI_USER", "0")
    assert DG.build_digest(period="7d", user_id=1)["risingChannels"]["total"] == 3


def _signed_in(email):
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def test_http_feeds_and_totals_follow_the_signed_in_user(users):
    c = _signed_in("fa@example.com")
    assert _channels(c.get("/api/title-changes?period=7d").json()["changes"]) == {CH_A}
    assert {x["channelId"] for x in c.get("/api/packaging?period=7d").json()["changes"]} == {CH_A}
    assert c.get("/api/stats").json()["tracked_channels"] == 1
    assert c.get("/api/health").json()["db"]["tracked_channels"] == 1


def test_health_tells_a_visitor_only_that_the_server_is_up(users, monkeypatch):
    anon = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert anon.get("/api/health").json() == {"ok": True}
    monkeypatch.setenv("NF_MULTI_USER", "0")
    assert anon.get("/api/health").json()["db"]["tracked_channels"] == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

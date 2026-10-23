"""Per-user isolation through HTTP (plan 15, sub-stage 5.4; multi-user P1-17):
with NF_MULTI_USER=1, two signed-in users each get their own watchlist, swipe
file, drafts and briefs, content calendar, transcript queue, alert read marks,
topics, notification settings, cost profiles and own channels with their synced numbers, and
B cannot read or change A's through any of those routes. Own channels are
written straight into the tables: Google is never called. Throwaway schema;
no network.
Run with pytest, or directly: python3 tests/test_http_isolation.py
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

PW = "correct horse battery"
CH = "UC" + "httpiso".ljust(22, "0")
NOW = datetime.now(timezone.utc)


def setup_module(_=None):
    db.init_db()


@pytest.fixture
def ab(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    conn = db.get_conn()
    for t in ("tracked_channels", "saved_items", "drafts", "transcript_requests", "event_reads",
              "events", "sessions", "own_video_metrics", "own_channel_daily", "own_channels",
              "user_topics", "user_settings", "cost_profiles", "transcripts", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                 (CH, "Shared channel", 1000))
    conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count) "
                 "VALUES (?,?,?,?,?)", ("httpisov1", CH, "v", (NOW - timedelta(days=2)).isoformat(), 10))
    conn.commit()
    conn.close()
    clients = []
    for email in ("a@example.com", "b@example.com"):
        c = TestClient(api.app, headers={"X-NF-Client": "tests"})
        c.uid = AUTH.create_user(email, PW)
        assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
        clients.append(c)
    return clients


def test_watchlists_are_separate(ab):
    a, b = ab
    assert a.post("/api/channels/track", json={"channel_id": CH, "note": "A's"}).status_code == 200
    assert [c["channel_id"] for c in a.get("/api/channels/tracked").json()["channels"]] == [CH]
    assert b.get("/api/channels/tracked").json()["channels"] == []
    b.delete(f"/api/channels/tracked/{CH}")                       # B untracks nothing of A's
    assert [c["channel_id"] for c in a.get("/api/channels/tracked").json()["channels"]] == [CH]
    assert a.get(f"/api/inspect/channel?ref={CH}&fetch=false").json()["tracked"] is True
    assert b.get(f"/api/inspect/channel?ref={CH}&fetch=false").json()["tracked"] is False


def test_swipe_files_are_separate(ab):
    a, b = ab
    item = a.post("/api/saved", json={"kind": "video", "refId": "httpisov1", "folder": "ideas"}).json()
    assert b.get("/api/saved").json()["items"] == []
    assert b.get("/api/saved/folders").json()["folders"] == []
    assert b.delete(f"/api/saved/{item['id']}").status_code == 404
    assert [i["id"] for i in a.get("/api/saved").json()["items"]] == [item["id"]]


def test_drafts_are_separate(ab):
    a, b = ab
    d = a.post("/api/drafts", json={"title": "A's draft"}).json()
    assert b.get("/api/drafts").json()["drafts"] == []
    assert b.post(f"/api/drafts/{d['id']}/link", json={"videoId": "httpisov1"}).status_code == 404
    assert a.get("/api/drafts").json()["drafts"][0]["videoId"] is None
    assert a.post(f"/api/drafts/{d['id']}/link", json={"videoId": "httpisov1"}).status_code == 200


def test_transcript_queues_are_separate(ab):
    a, b = ab
    a.post("/api/transcripts/request", json={"videoId": "httpisov1", "reason": "A"})
    assert [q["videoId"] for q in a.get("/api/transcripts/queue").json()["queue"]] == ["httpisov1"]
    assert b.get("/api/transcripts/queue").json()["queue"] == []


def test_reindexing_a_transcript_touches_only_your_own_queue(ab, monkeypatch):
    a, b = ab
    from application import transcripts as TR
    monkeypatch.setattr(TR, "save_transcript",
                        lambda vid, text, language=None, user_id=1, may_overwrite=True:
                        {"videoId": vid, "userId": user_id})
    uid_b = [u["id"] for u in AUTH.list_users() if u["email"] == "b@example.com"][0]
    conn = db.get_conn()
    # b saved it: only its author (or an admin) may re-index a shared transcript (P0-02)
    conn.execute("INSERT INTO transcripts (video_id, text, created_at, created_by) VALUES (?,?,?,?) "
                 "ON CONFLICT (video_id) DO UPDATE SET created_by = excluded.created_by",
                 ("httpisov1", "hello", db.now_iso(), uid_b))
    conn.commit()
    conn.close()
    assert b.post("/api/transcripts/httpisov1/reindex").json()["userId"] == uid_b
    assert a.post("/api/transcripts/httpisov1/reindex").status_code == 403


def test_alert_read_marks_are_separate(ab):
    a, b = ab
    a.post("/api/channels/track", json={"channel_id": CH})
    b.post("/api/channels/track", json={"channel_id": CH})
    conn = db.get_conn()
    conn.execute("INSERT INTO events (kind, ref_id, payload, created_at, channel_id) VALUES (?,?,?,?,?)",
                 ("outlier", "httpisov1", '{"channelId": "%s"}' % CH, db.now_iso(), CH))
    conn.commit()
    conn.close()
    assert a.get("/api/events").json()["unseenCount"] == 1
    a.post("/api/events/seen", json={"all": True})
    assert a.get("/api/events").json()["unseenCount"] == 0
    assert b.get("/api/events").json()["unseenCount"] == 1        # A's read mark is A's


def test_a_signed_out_client_sees_nothing(ab):
    a, _ = ab
    a.post("/api/drafts", json={"title": "A's draft"})
    anon = TestClient(api.app, headers={"X-NF-Client": "tests"})
    for path in ("/api/drafts", "/api/saved", "/api/channels/tracked", "/api/events",
                 "/api/transcripts/queue", "/api/own/channels", "/api/cost-profiles"):
        assert anon.get(path).status_code == 401, path


# ------------------------------------------------------------ P1-17: more personal data

def _rows(sql, params=()):
    conn = db.get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def test_saved_items_with_the_same_ref_stay_apart(ab):
    a, b = ab
    ia = a.post("/api/saved", json={"kind": "video", "refId": "httpisov1", "folder": "ideas"}).json()
    assert b.get("/api/saved?folder=ideas").json()["items"] == []
    assert b.get("/api/saved?kind=video").json()["items"] == []
    ib = b.post("/api/saved", json={"kind": "video", "refId": "httpisov1", "note": "B's"}).json()
    assert [i["id"] for i in b.get("/api/saved").json()["items"]] == [ib["id"]]
    assert b.delete(f"/api/saved/{ib['id']}").status_code == 200
    assert [i["id"] for i in a.get("/api/saved").json()["items"]] == [ia["id"]]
    assert a.get("/api/saved/folders").json()["folders"][0]["folder"] == "ideas"


# own channels: rows written straight into the tables, as a sync would leave them

OWN_A = "UC" + "ownisoa".ljust(22, "0")


def _connect(uid, channel_id, views, revenue, video_id="ownisov1"):
    conn = db.get_conn()
    conn.execute("INSERT INTO own_channels (user_id, channel_id, title, published_at, scopes, "
                 "token_enc, connected_at, last_synced_at) VALUES (?,?,?,?,?,?,?,?)",
                 (uid, channel_id, f"Own {uid}", "2020-01-01T00:00:00Z",
                  "yt-analytics.readonly yt-analytics-monetary.readonly", None,
                  "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"))
    for window in ("28d", "lifetime"):
        conn.execute("INSERT INTO own_video_metrics (user_id, channel_id, video_id, window_name, "
                     "start_date, end_date, views, minutes_watched, avg_view_duration, avg_view_pct, "
                     "subscribers_gained, revenue, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (uid, channel_id, video_id, window, "2026-01-01", "2026-01-28", views, views * 2,
                      60.0, 40.0, 1, revenue, "2026-01-02T00:00:00+00:00"))
    for day in ("2026-01-01", "2026-01-02"):
        conn.execute("INSERT INTO own_channel_daily (user_id, channel_id, day, content_type, views, "
                     "minutes_watched, subscribers_gained) VALUES (?,?,?,?,?,?,?)",
                     (uid, channel_id, day, "SHORTS", views, views, 1))
    conn.commit()
    conn.close()


def _own_rows(uid):
    return {t: _rows(f"SELECT * FROM {t} WHERE user_id = ? ORDER BY 1, 2, 3", (uid,))
            for t in ("own_channels", "own_video_metrics", "own_channel_daily")}


def test_own_channels_and_their_numbers_are_separate(ab, monkeypatch):
    a, b = ab
    from application import own_channels as OWN
    revoked = []
    monkeypatch.setattr(OWN.OA, "revoke", lambda token: revoked.append(token) or True)
    monkeypatch.setattr(OWN, "_require_configured", lambda connecting=True: None)
    _connect(a.uid, OWN_A, views=1000, revenue=5.0)
    before = _own_rows(a.uid)

    mine = a.get("/api/own/channels").json()["channels"]
    assert [c["channelId"] for c in mine] == [OWN_A] and mine[0]["last28d"]["views"] == 1000
    assert b.get("/api/own/channels").json()["channels"] == []
    assert b.get("/api/own/status").json()["connectedChannels"] == 0
    assert a.get("/api/own/status").json()["connectedChannels"] == 1
    assert b.get("/api/own/rpm-calibration").json()["channels"] == []
    assert [c["channelId"] for c in a.get("/api/own/rpm-calibration").json()["channels"]] == [OWN_A]

    # by id: B gets 404, A gets the numbers
    assert b.get(f"/api/own/channels/{OWN_A}/vs-niche?niche=any").status_code == 404
    assert b.get(f"/api/own/channels/{OWN_A}/formats").status_code == 404
    assert a.get(f"/api/own/channels/{OWN_A}/vs-niche?niche=any").json()["topVideos"][0]["views"] == 1000
    assert a.get(f"/api/own/channels/{OWN_A}/formats").json()["available"] is True

    # B's sync naming A's channel syncs nothing of A's; B's disconnect is a 404
    assert b.post(f"/api/own/sync?channel_id={OWN_A}").json()["channels"] == []
    assert b.delete(f"/api/own/channels/{OWN_A}").status_code == 404
    assert revoked == [] and _own_rows(a.uid) == before


def test_the_same_own_channel_connected_by_two_users_stays_apart(ab):
    a, b = ab
    _connect(a.uid, OWN_A, views=1000, revenue=5.0)
    _connect(b.uid, OWN_A, views=7, revenue=None)
    before = _own_rows(a.uid)
    assert a.get("/api/own/channels").json()["channels"][0]["last28d"]["views"] == 1000
    assert b.get("/api/own/channels").json()["channels"][0]["last28d"]["views"] == 7
    assert b.get(f"/api/own/channels/{OWN_A}/vs-niche?niche=any").json()["topVideos"][0]["views"] == 7
    out = b.delete(f"/api/own/channels/{OWN_A}")                   # B's own connection only
    assert out.status_code == 200 and out.json()["deleted"] is True
    assert b.get("/api/own/channels").json()["channels"] == []
    assert _own_rows(b.uid) == {"own_channels": [], "own_video_metrics": [], "own_channel_daily": []}
    assert _own_rows(a.uid) == before


def test_draft_outcomes_show_only_your_own_channel_numbers(ab):
    a, b = ab
    _connect(a.uid, CH, views=1000, revenue=5.0, video_id="httpisov1")
    da = a.post("/api/drafts", json={"title": "A's"}).json()
    db_ = b.post("/api/drafts", json={"title": "B's"}).json()
    a.post(f"/api/drafts/{da['id']}/link", json={"videoId": "httpisov1"})
    b.post(f"/api/drafts/{db_['id']}/link", json={"videoId": "httpisov1"})
    oa = a.get("/api/drafts/outcomes?min_age_days=0").json()["outcomes"]
    ob = b.get("/api/drafts/outcomes?min_age_days=0").json()["outcomes"]
    assert [o["draftId"] for o in oa] == [da["id"]] and oa[0]["ownMetrics"]["views"] == 1000
    assert [o["draftId"] for o in ob] == [db_["id"]] and ob[0]["ownMetrics"] is None


def _iso(dt):
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_content_calendars_are_separate(ab):
    a, b = ab
    from application import content_calendar as CAL
    planned = NOW + timedelta(hours=3)
    da = a.post("/api/drafts", json={"title": "A's release"}).json()
    unplanned_a = a.post("/api/drafts", json={"title": "A's idea"}).json()
    assert a.post(f"/api/drafts/{da['id']}/plan", json={"plannedAt": _iso(planned)}).status_code == 200
    db_ = b.post("/api/drafts", json={"title": "B's idea"}).json()

    cal_a, cal_b = a.get("/api/calendar").json(), b.get("/api/calendar").json()
    assert [i["id"] for i in cal_a["items"]] == [da["id"]]
    assert [i["id"] for i in cal_a["unplanned"]] == [unplanned_a["id"]]
    assert cal_b["items"] == [] and [i["id"] for i in cal_b["unplanned"]] == [db_["id"]]

    # B can neither move A's planned draft nor take it off the calendar, nor plan A's idea
    for body in ({"plannedAt": _iso(NOW + timedelta(days=5))}, {"plannedAt": None}):
        assert b.post(f"/api/drafts/{da['id']}/plan", json=body).status_code == 404
    assert b.post(f"/api/drafts/{unplanned_a['id']}/plan",
                  json={"plannedAt": _iso(planned)}).status_code == 404
    after = a.get("/api/calendar").json()
    assert [(i["id"], i["plannedAt"]) for i in after["items"]] == \
           [(i["id"], i["plannedAt"]) for i in cal_a["items"]]
    assert [i["id"] for i in after["unplanned"]] == [unplanned_a["id"]]

    # the reminder for A's planned draft is A's alone
    assert CAL.remind_due()["raised"] == 1
    assert [e["kind"] for e in a.get("/api/events").json()["events"]] == ["draft_due"]
    assert b.get("/api/events").json() == {"events": [], "unseenCount": 0}


def _topic(uid, text):
    conn = db.get_conn()
    tid = conn.execute("INSERT INTO user_topics (user_id, text, threshold, created_at) "
                       "VALUES (?,?,?,?) RETURNING id", (uid, text, 0.6, db.now_iso())).fetchone()[0]
    conn.commit()
    conn.close()
    return tid


def test_topics_are_separate(ab):
    a, b = ab
    ta = _topic(a.uid, "A's topic")
    tb = _topic(b.uid, "B's topic")
    assert [t["id"] for t in a.get("/api/topics").json()["topics"]] == [ta]
    assert [t["id"] for t in b.get("/api/topics").json()["topics"]] == [tb]
    before = _rows("SELECT * FROM user_topics WHERE id = ?", (ta,))

    assert b.post(f"/api/topics/{ta}/pause", json={"paused": True}).status_code == 404
    # the YouTube search toggle may stop at the plan gate first; either way it is refused
    assert b.post(f"/api/topics/{ta}/search", json={"on": True}).status_code in (403, 404)
    assert b.delete(f"/api/topics/{ta}").status_code == 404
    assert _rows("SELECT * FROM user_topics WHERE id = ?", (ta,)) == before

    # a topic's alert is personal too
    conn = db.get_conn()
    conn.execute("INSERT INTO events (kind, ref_id, payload, created_at, user_id) VALUES (?,?,?,?,?)",
                 ("topic_match", f"{ta}:httpisov1", '{"topicId": %d}' % ta, db.now_iso(), a.uid))
    conn.commit()
    conn.close()
    assert [e["kind"] for e in a.get("/api/events").json()["events"]] == ["topic_match"]
    assert b.get("/api/events").json()["unseenCount"] == 0

    assert b.post(f"/api/topics/{tb}/pause", json={"paused": True}).json()["paused"] is True
    assert b.delete(f"/api/topics/{tb}").status_code == 200
    assert [t["id"] for t in a.get("/api/topics").json()["topics"]] == [ta]
    assert a.get("/api/topics").json()["topics"][0]["paused"] is False


def test_notification_settings_are_separate(ab):
    a, b = ab
    r = a.put("/api/settings/notifications", json={"telegramChatId": "A-chat", "mode": "digest"})
    assert r.status_code == 200 and r.json()["source"] == "settings"
    mine = a.get("/api/settings/notifications").json()
    assert mine["telegramChatId"] == "A-chat" and mine["mode"] == "digest"
    assert b.get("/api/settings/notifications").json() == {
        "source": "none", "telegram": False, "telegramChatId": None, "webhook": False,
        "mode": "instant"}

    assert b.put("/api/settings/notifications", json={"mode": "off"}).json()["mode"] == "off"
    assert b.post("/api/settings/notifications/test").status_code == 409     # not A's target
    assert b.put("/api/settings/notifications", json={"clear": True}).json()["source"] == "none"
    assert a.get("/api/settings/notifications").json() == mine


def test_briefs_are_saved_for_their_author_only(ab, monkeypatch):
    a, b = ab
    from application import search as Q
    monkeypatch.setattr(Q, "similar_videos", lambda *a, **k: {"similar": []})
    monkeypatch.setattr(Q, "check_ideas", lambda *a, **k: {"results": []})
    brief = a.post("/api/briefs", json={"videoId": "httpisov1", "useLlm": False, "save": True}).json()
    did = brief["draftId"]
    assert did and [d["id"] for d in a.get("/api/drafts").json()["drafts"]] == [did]
    assert [q["videoId"] for q in a.get("/api/transcripts/queue").json()["queue"]] == ["httpisov1"]

    assert b.get("/api/drafts").json()["drafts"] == []
    assert b.get("/api/calendar").json()["unplanned"] == []
    assert b.get("/api/transcripts/queue").json()["queue"] == []
    assert b.post(f"/api/drafts/{did}/plan", json={"plannedAt": _iso(NOW)}).status_code == 404
    assert b.post(f"/api/drafts/{did}/link", json={"videoId": "httpisov1"}).status_code == 404

    # a preview of the same shared video writes nothing for B
    preview = b.post("/api/briefs", json={"videoId": "httpisov1", "useLlm": False, "save": False})
    assert preview.status_code == 200 and preview.json()["draftId"] is None
    assert _rows("SELECT id FROM drafts WHERE user_id = ?", (b.uid,)) == []
    d = _rows("SELECT video_id, planned_at, source_video_id FROM drafts WHERE id = ?", (did,))
    assert d == [{"video_id": None, "planned_at": None, "source_video_id": "httpisov1"}]


def _profiles(uid):
    return _rows("SELECT * FROM cost_profiles WHERE user_id = ? ORDER BY id", (uid,))


def test_cost_profiles_are_separate(ab):
    a, b = ab
    pa = a.post("/api/cost-profiles", json={"name": "Studio", "perVideoUsd": 50,
                                            "perMinuteUsd": 2, "monthlyUsd": 30}).json()
    before = _profiles(a.uid)
    assert [p["id"] for p in a.get("/api/cost-profiles").json()["profiles"]] == [pa["id"]]
    assert b.get("/api/cost-profiles").json()["profiles"] == []

    # by id or name: the profit estimate is the one route that reads a single profile
    mine = a.get("/api/profit", params={"video_id": "httpisov1", "profile": pa["id"]})
    assert mine.status_code == 200 and mine.json()["profile"]["id"] == pa["id"]
    for ref in (pa["id"], "Studio"):
        r = b.get("/api/profit", params={"video_id": "httpisov1", "profile": ref})
        assert r.status_code == 400 and r.json()["detail"] == "cost profile not found", ref
    # with no profile named B gets zero costs, not A's first profile
    own = b.get("/api/profit", params={"video_id": "httpisov1"}).json()["profile"]
    assert own == {"id": None, "name": None, "per_video_usd": 0.0, "per_minute_usd": 0.0,
                   "monthly_usd": 0.0}

    # B changes A's profile by id: refused exactly like an id that does not exist
    change = {"id": pa["id"], "name": "Hijacked", "perVideoUsd": 0}
    r = b.post("/api/cost-profiles", json=change)
    missing = b.post("/api/cost-profiles", json={**change, "id": pa["id"] + 10_000})
    assert r.status_code == 400 and r.json() == missing.json() == {"detail": "profile not found"}
    assert b.delete(f"/api/cost-profiles/{pa['id']}").status_code == 404
    assert _profiles(a.uid) == before

    # the same name is B's own profile, not an edit of A's
    pb = b.post("/api/cost-profiles", json={"name": "Studio", "perVideoUsd": 5}).json()
    assert pb["id"] != pa["id"]
    assert [(p["id"], p["per_video_usd"]) for p in b.get("/api/cost-profiles").json()["profiles"]] \
        == [(pb["id"], 5.0)]
    assert b.get("/api/profit", params={"video_id": "httpisov1"}).json()["profile"]["id"] == pb["id"]
    assert b.post("/api/cost-profiles", json={"id": pb["id"], "name": "Studio",
                                              "perVideoUsd": 7}).json()["per_video_usd"] == 7.0
    assert b.delete(f"/api/cost-profiles/{pb['id']}").status_code == 200
    assert b.get("/api/cost-profiles").json()["profiles"] == []
    assert _profiles(a.uid) == before
    assert a.get("/api/cost-profiles").json()["profiles"][0]["per_video_usd"] == 50.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

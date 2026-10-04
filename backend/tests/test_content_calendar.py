"""Tests for application/content_calendar.py (plan 33) and its doors: planning
drafts, the calendar range, reminders as personal events, their Telegram text
and digest line. Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_content_calendar.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import alerts as AL  # noqa: E402
from application import content_calendar as CAL  # noqa: E402
from application import digest as DG  # noqa: E402
from application import metadata_review as MR  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.isoformat()


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    conn = db.get_conn()
    for t in ("drafts", "events", "event_reads"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    # best hours read the niche's videos; their own tests cover them
    monkeypatch.setattr(CAL, "best_slots", lambda niche=None, channel_id=None: (
        [{"weekday": "Mon", "hour": 15, "score": 100}] if niche else []))


def _draft(title="My video", niche="tech", user_id=1):
    return MR.save_draft(title, niche=niche, user_id=user_id)["id"]


def test_a_draft_gets_a_time_and_comes_off_again():
    d = _draft()
    when = NOW + timedelta(days=3)
    r = CAL.plan_draft(d, iso(when))
    assert r["plannedAt"] == iso(when) and r["state"] == "planned"
    assert r["bestSlots"][0] == {"weekday": "Mon", "hour": 15, "score": 100}
    assert r["fitsBestSlot"] == (when.strftime("%a") == "Mon" and when.hour == 15)
    assert MR.list_drafts()[0]["plannedAt"] == iso(when)
    off = CAL.plan_draft(d, None)
    assert off["plannedAt"] is None and off["state"] == "unplanned"


def test_times_are_stored_in_utc_whatever_zone_came_in():
    d = _draft()
    r = CAL.plan_draft(d, "2026-10-10T18:00:00+03:00")
    assert r["plannedAt"] == "2026-10-10T15:00:00+00:00"
    assert CAL.plan_draft(d, "2026-10-10T15:00:00Z")["plannedAt"] == "2026-10-10T15:00:00+00:00"


def test_bad_input_and_someone_else_s_draft_are_refused():
    d = _draft()
    with pytest.raises(ValueError):
        CAL.plan_draft(d, "next friday")
    with pytest.raises(LookupError):
        CAL.plan_draft(d, iso(NOW), user_id=2)
    with pytest.raises(LookupError):
        CAL.plan_draft(999999, iso(NOW))


def test_the_calendar_shows_the_range_and_the_unplanned_drafts():
    a, b, c, gone = _draft("a"), _draft("b"), _draft("c"), _draft("published one")
    CAL.plan_draft(a, iso(NOW + timedelta(days=1)))
    CAL.plan_draft(b, iso(NOW + timedelta(days=90)))            # outside the range
    MR.link_draft(gone, "vid123")
    cal = CAL.content_calendar(iso(NOW - timedelta(days=1)), iso(NOW + timedelta(days=7)))
    ids = {x["id"]: x for x in cal["items"]}
    assert set(ids) == {a, gone}
    assert ids[a]["state"] == "planned" and ids[gone]["state"] == "published"
    assert ids[a]["day"] == (NOW + timedelta(days=1)).date().isoformat()
    assert [x["id"] for x in cal["unplanned"]] == [c]


def test_the_default_range_starts_this_week_and_bad_ranges_are_refused():
    cal = CAL.content_calendar()
    start = datetime.fromisoformat(cal["start"])
    assert start.weekday() == 0 and start <= NOW and (datetime.fromisoformat(cal["end"]) - start).days == 28
    for s, e in ((iso(NOW), iso(NOW - timedelta(days=1))), (iso(NOW), iso(NOW + timedelta(days=90))),
                 ("soon", None)):
        with pytest.raises(ValueError):
            CAL.content_calendar(s, e)


def test_an_overdue_draft_is_marked():
    d = _draft()
    CAL.plan_draft(d, iso(NOW - timedelta(hours=2)))
    cal = CAL.content_calendar(iso(NOW - timedelta(days=1)), iso(NOW + timedelta(days=1)))
    assert cal["items"][0]["state"] == "overdue"


def _events():
    conn = db.get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT kind, ref_id, payload, user_id FROM events ORDER BY id").fetchall()]
    finally:
        conn.close()


def test_reminders_come_the_day_before_and_when_due_once_each_and_are_personal():
    d = _draft(user_id=7)
    planned = NOW + timedelta(hours=10)
    CAL.plan_draft(d, iso(planned), user_id=7)
    assert CAL.remind_due(now=NOW)["raised"] == 1
    assert CAL.remind_due(now=NOW)["raised"] == 0                      # once
    assert CAL.remind_due(now=planned + timedelta(minutes=5))["raised"] == 1
    evs = _events()
    assert [json.loads(e["payload"])["stage"] for e in evs] == ["soon", "due"]
    assert all(e["kind"] == "draft_due" and e["user_id"] == 7 for e in evs)


def test_moving_the_date_brings_new_reminders_and_linking_stops_them():
    d = _draft()
    CAL.plan_draft(d, iso(NOW + timedelta(hours=5)))
    CAL.remind_due(now=NOW)
    CAL.plan_draft(d, iso(NOW + timedelta(hours=8)))
    assert CAL.remind_due(now=NOW)["raised"] == 1
    MR.link_draft(d, "vid9")
    assert CAL.remind_due(now=NOW + timedelta(hours=8, minutes=1))["raised"] == 0


def test_a_reminder_reads_well_in_telegram_and_in_the_digest():
    d = _draft("Launch <video>")
    CAL.plan_draft(d, iso(NOW + timedelta(hours=3)))
    CAL.remind_due(now=NOW)
    ev = _events()[0]
    text = AL._format_message({"kind": "draft_due", "payload": json.loads(ev["payload"])})
    assert "Черновик" in text and "Launch &lt;video&gt;" in text and "ближайшие сутки" in text
    assert "#/calendar" in text
    dg = DG.build_digest(period="24h")
    assert dg["drafts"]["total"] == 1 and not dg["empty"]
    assert "Календарь" in DG.format_digest(dg)


def test_http_and_mcp_doors():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    d = _draft()
    c = TestClient(api.app)
    when = iso(NOW + timedelta(days=2))
    r = c.post(f"/api/drafts/{d}/plan", json={"plannedAt": when})
    assert r.status_code == 200 and r.json()["state"] == "planned"
    assert c.post(f"/api/drafts/{d}/plan", json={"plannedAt": "tomorrow"}).status_code == 400
    assert c.post("/api/drafts/999999/plan", json={"plannedAt": when}).status_code == 404
    r = c.get("/api/calendar")
    assert r.status_code == 200 and [x["id"] for x in r.json()["items"]] == [d]
    assert c.get("/api/calendar", params={"start": "x"}).status_code == 400
    assert srv.plan_draft(d, None)["state"] == "unplanned"
    assert [x["id"] for x in srv.content_calendar()["unplanned"]] == [d]
    assert "error" in srv.plan_draft(999999, when)

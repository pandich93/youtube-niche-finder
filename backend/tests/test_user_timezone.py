"""P2-03: часовой пояс пользователя. Дайджест уходит каждому в DIGEST_HOUR по
его местному времени ровно раз в его сутки (переход на летнее время не даёт ни
двойной, ни пропущенной отправки), неверный пояс отклоняется, пустой -- всё
как раньше (время сервера / NF_DEFAULT_TIMEZONE). Нотификаторы -- заглушки,
без сети. Run with pytest, or directly: python3 tests/test_user_timezone.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import channel_tracking as T  # noqa: E402
from application import content_calendar as CAL  # noqa: E402
from application import digest  # noqa: E402
from application import notify_settings as NS  # noqa: E402
from application import packaging as PKG  # noqa: E402
from application import timezone_prefs as TZP  # noqa: E402
from domain import timezones as TZ  # noqa: E402

LOCAL, OTHER = 1, 4243
UTC = timezone.utc
client = TestClient(api.app, headers={"X-NF-Client": "tests"})


def setup_module(_=None):
    db.init_db()
    conn = db.get_conn()
    conn.execute("INSERT INTO users (id, email, is_admin, created_at) VALUES (?, ?, 0, ?) "
                 "ON CONFLICT (id) DO NOTHING", (OTHER, "tz-test@example.invalid", db.now_iso()))
    conn.commit()
    conn.close()


def teardown_module(_=None):
    conn = db.get_conn()
    conn.execute("UPDATE users SET timezone = NULL WHERE id = ?", (LOCAL,))
    conn.execute("DELETE FROM users WHERE id = ?", (OTHER,))
    conn.execute("DELETE FROM meta WHERE key LIKE 'digest_%'")
    conn.commit()
    conn.close()


class _Stub:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    conn = db.get_conn()
    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM alert_deliveries")
    conn.execute("DELETE FROM meta WHERE key LIKE 'digest_%'")
    conn.execute("UPDATE users SET timezone = NULL WHERE id IN (?, ?)", (LOCAL, OTHER))
    conn.commit()
    conn.close()
    monkeypatch.delenv("NF_DEFAULT_TIMEZONE", raising=False)
    monkeypatch.setattr(T, "recently_added_outlier_channels", lambda **kw: {"channels": []})
    monkeypatch.setattr(PKG, "packaging_feed", lambda **kw: {"changes": []})
    monkeypatch.setattr(digest, "DIGEST_HOUR", 8)
    # пустой день тоже отправляется -- так каждая отправка видна в заглушке
    monkeypatch.setattr(digest, "DIGEST_SKIP_EMPTY", False)
    stubs = {LOCAL: _Stub(), OTHER: _Stub()}
    monkeypatch.setattr(NS, "recipients", lambda: [LOCAL, OTHER])
    monkeypatch.setattr(NS, "mode_for", lambda user_id: "digest")
    monkeypatch.setattr(NS, "notifier_for", lambda user_id: stubs[user_id])
    return stubs


def _sent(out, uid):
    return out[str(uid)].get("sent") is True


# ------------------------------------------------------------ the zone itself

def test_iana_names_are_kept_legacy_ones_canonicalised_bad_ones_refused():
    assert TZ.normalize("Europe/Moscow") == "Europe/Moscow"
    assert TZ.normalize("  America/Argentina/Buenos_Aires ") == "America/Argentina/Buenos_Aires"
    assert TZ.normalize("UTC") == "UTC"
    # Chrome still answers Asia/Calcutta; the Debian image has only Asia/Kolkata
    assert TZ.normalize("Asia/Calcutta") == "Asia/Kolkata"
    assert TZ.normalize("") is None and TZ.normalize(None) is None
    for bad in ("Mars/Olympus", "Europe/Moscow ; DROP", "../../etc/passwd", "/etc/localtime",
                "x" * 80, "+03:00"):
        with pytest.raises(TZ.TimezoneError):
            TZ.normalize(bad)


def test_saving_validates_clears_and_only_if_empty_keeps_a_chosen_zone():
    assert TZP.save("Asia/Tokyo", user_id=OTHER) == "Asia/Tokyo"
    assert TZP.save("Europe/Moscow", user_id=OTHER, only_if_empty=True) == "Asia/Tokyo"
    with pytest.raises(TZP.TimezoneError):
        TZP.save("Nowhere/City", user_id=OTHER)
    assert TZP.get(user_id=OTHER) == "Asia/Tokyo"
    assert TZP.save("", user_id=OTHER) is None
    assert TZP.save("Europe/Moscow", user_id=OTHER, only_if_empty=True) == "Europe/Moscow"


def test_nf_default_timezone_applies_only_when_the_user_has_none(monkeypatch):
    assert TZP.effective(user_id=OTHER) is None             # server time, as before
    monkeypatch.setenv("NF_DEFAULT_TIMEZONE", "Asia/Tokyo")
    assert TZP.effective(user_id=OTHER) == "Asia/Tokyo"
    TZP.save("Europe/Moscow", user_id=OTHER)
    assert TZP.effective(user_id=OTHER) == "Europe/Moscow"
    monkeypatch.setenv("NF_DEFAULT_TIMEZONE", "Not/AZone")  # a typo in .env never breaks the worker
    TZP.save("", user_id=OTHER)
    assert TZP.effective(user_id=OTHER) is None


# ------------------------------------------------------------ the digest

def test_two_users_get_the_digest_at_digest_hour_of_their_own_zone(_clean):
    stubs = _clean
    TZP.save("Europe/Moscow", user_id=LOCAL)                # UTC+3
    TZP.save("Asia/Tokyo", user_id=OTHER)                   # UTC+9
    # 22:30 UTC: Tokyo 07:30 of the 25th, Moscow 01:30 -- nobody yet
    out = digest.send_all_digests(now=datetime(2026, 10, 24, 22, 30, tzinfo=UTC))
    assert out[str(OTHER)]["reason"] == "too early" and out[str(OTHER)]["timezone"] == "Asia/Tokyo"
    assert out[str(LOCAL)]["reason"] == "too early"
    # 23:10 UTC: 08:10 in Tokyo -- Tokyo only
    out = digest.send_all_digests(now=datetime(2026, 10, 24, 23, 10, tzinfo=UTC))
    assert _sent(out, OTHER) and out[str(OTHER)]["date"] == "2026-10-25"
    assert out[str(LOCAL)]["reason"] == "too early"
    # 05:10 UTC: 08:10 in Moscow -- Moscow now, Tokyo (14:10) already had its day
    out = digest.send_all_digests(now=datetime(2026, 10, 25, 5, 10, tzinfo=UTC))
    assert _sent(out, LOCAL) and out[str(LOCAL)]["date"] == "2026-10-25"
    assert out[str(OTHER)]["reason"] == "already sent today"
    assert len(stubs[LOCAL].sent) == 1 and len(stubs[OTHER].sent) == 1


def _run_cycles(start, days, every_min=10):
    """Worker cycles every `every_min` minutes; returns the moments a send happened."""
    sends, now = [], start
    while now < start + timedelta(days=days):
        if digest.send_digest(now=now, user_id=OTHER).get("sent"):
            sends.append(now)
        now += timedelta(minutes=every_min)
    return sends


@pytest.mark.parametrize("hour", [1, 2, 8])
def test_clocks_going_back_never_send_twice(monkeypatch, hour):
    # New York, 1 Nov 2026: 02:00 EDT becomes 01:00 EST, the 01:xx hour repeats
    monkeypatch.setattr(digest, "DIGEST_HOUR", hour)
    TZP.save("America/New_York", user_id=OTHER)
    ny = ZoneInfo("America/New_York")
    # from local midnight of the 31st (00:00 EDT) for four local days
    sends = _run_cycles(datetime(2026, 10, 31, 4, 0, tzinfo=UTC), days=4)
    days = [s.astimezone(ny).date().isoformat() for s in sends]
    assert days == ["2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03"]
    # each one at the first cycle of DIGEST_HOUR, local time
    assert all(s.astimezone(ny).hour == hour and s.astimezone(ny).minute < 10 for s in sends)


@pytest.mark.parametrize("hour", [2, 3])
def test_clocks_going_forward_never_skip_a_day(monkeypatch, hour):
    # New York, 8 Mar 2026: 02:00 EST jumps to 03:00 EDT, the 02:xx hour never happens
    monkeypatch.setattr(digest, "DIGEST_HOUR", hour)
    TZP.save("America/New_York", user_id=OTHER)
    ny = ZoneInfo("America/New_York")
    # from local midnight of the 7th (00:00 EST) for four local days
    sends = _run_cycles(datetime(2026, 3, 7, 5, 0, tzinfo=UTC), days=4)
    days = [s.astimezone(ny).date().isoformat() for s in sends]
    assert days == ["2026-03-07", "2026-03-08", "2026-03-09", "2026-03-10"]
    on_the_day = sends[1].astimezone(ny)
    assert (on_the_day.hour, on_the_day.minute < 10) == (3, True)


def test_a_restart_or_a_westward_move_does_not_resend_the_same_day(_clean):
    stubs = _clean
    TZP.save("Asia/Tokyo", user_id=OTHER)
    sent_at = datetime(2026, 10, 24, 23, 30, tzinfo=UTC)   # 08:30 of the 25th in Tokyo
    assert digest.send_digest(now=sent_at, user_id=OTHER)["sent"] is True
    # the mark lives in the database: a fresh worker process reads the same
    assert digest.send_digest(now=sent_at + timedelta(minutes=10), user_id=OTHER)["reason"] \
        == "already sent today"
    # moving to Los Angeles, where it is still the 24th (16:40): no second digest
    TZP.save("America/Los_Angeles", user_id=OTHER)
    assert digest.send_digest(now=sent_at + timedelta(minutes=10), user_id=OTHER)["reason"] \
        == "already sent today"
    # the next Los Angeles morning (the 25th, 08:10 PDT) is a new day
    assert digest.send_digest(now=datetime(2026, 10, 25, 15, 10, tzinfo=UTC), user_id=OTHER)["sent"]
    assert len(stubs[OTHER].sent) == 2


def test_no_zone_keeps_the_server_time_as_before(_clean):
    # nothing saved, no NF_DEFAULT_TIMEZONE: the hour and day are the server's
    server = datetime(2026, 10, 24, 9, 0).astimezone()      # 09:00 server time
    out = digest.send_digest(now=server, user_id=OTHER)
    assert out["sent"] is True and out["date"] == server.date().isoformat()
    early = datetime(2026, 10, 25, 6, 0).astimezone()
    assert digest.send_digest(now=early, user_id=OTHER)["reason"] == "too early"


def test_a_mark_written_before_the_upgrade_still_counts_for_its_day():
    # before P2-03 the mark was the server-local date of the last send
    conn = db.get_conn()
    db.set_meta(conn, digest._last_sent_key(OTHER), "2026-10-24")
    conn.commit()
    conn.close()
    server = datetime(2026, 10, 24, 20, 0).astimezone()
    assert digest.send_digest(now=server, user_id=OTHER)["reason"] == "already sent today"
    assert digest.send_digest(now=server + timedelta(days=1), user_id=OTHER)["sent"] is True


# ------------------------------------------------------------ calendar reminders

def test_calendar_reminder_shows_the_recipients_local_time():
    p = {"plannedAt": "2026-10-25T12:00:00+00:00", "stage": "soon"}
    assert CAL.event_text(p).endswith("2026-10-25 12:00 UTC")      # no zone: UTC as before
    with TZ.use_timezone("Europe/Moscow"):
        assert CAL.event_text(p).endswith("2026-10-25 15:00 Europe/Moscow")
    assert TZ.current() is None                                     # nothing left behind


# ------------------------------------------------------------ HTTP (single-user)

def test_me_shows_and_put_saves_the_local_users_zone():
    me = client.get("/api/auth/me").json()
    assert me["timezone"] is None and "defaultTimezone" in me
    r = client.put("/api/me/timezone", json={"timezone": "Asia/Calcutta"})
    assert r.status_code == 200 and r.json()["timezone"] == "Asia/Kolkata"
    assert client.get("/api/auth/me").json()["timezone"] == "Asia/Kolkata"
    # the silent "browser zone on first sign-in" never overwrites
    r = client.put("/api/me/timezone", json={"timezone": "Europe/Moscow", "onlyIfEmpty": True})
    assert r.json()["timezone"] == "Asia/Kolkata"
    assert client.put("/api/me/timezone", json={"timezone": "Mars/Base"}).status_code == 400
    assert client.put("/api/me/timezone", json={"timezone": ""}).json()["timezone"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

"""Multi-user plan P1-11: one worker per database, and a worker that keeps
going. A second worker waits as a standby instead of spending the quota and
sending alerts twice; one user's failing channel sync does not stop the
others; alert delivery reads only a recent window of events.
Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_worker_singleton.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import psycopg2  # noqa: E402
import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import alerts  # noqa: E402
from application import notify_settings as NS  # noqa: E402
from application import own_channels as OWN  # noqa: E402
from application import plans as PL  # noqa: E402
from application import worker_cycle as W  # noqa: E402


def setup_module(_=None):
    db.init_db()


# ------------------------------------------------------------ one worker

def _other_session_can_lock(name) -> bool:
    other = psycopg2.connect(db._dsn())
    other.autocommit = True
    cur = other.cursor()
    key = f"{name}:{db._schema()}"
    cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (key,))
    got = cur.fetchone()[0]
    if got:
        cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
    other.close()
    return got


def test_only_one_process_holds_the_worker_lock():
    assert db.hold_lock("test-singleton")
    assert db.hold_lock("test-singleton")                 # this process: already its own
    assert not _other_session_can_lock("test-singleton")  # anyone else: no
    db.release_lock("test-singleton")
    assert _other_session_can_lock("test-singleton")


def test_a_second_worker_waits_as_a_standby_and_takes_over(monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(W.db, "hold_lock", lambda name: next(answers))
    monkeypatch.setattr(W, "STANDBY_CHECK_SEC", 0)
    said = []
    monkeypatch.setattr(W, "log", said.append)
    assert W._wait_for_lock() is True
    assert sum("standby" in s for s in said) == 1         # said once, not every 30 s
    assert any("taking over" in s for s in said)


def test_a_standby_stops_when_told(monkeypatch):
    monkeypatch.setattr(W.db, "hold_lock", lambda name: False)
    monkeypatch.setattr(W, "_stop", True)
    assert W._wait_for_lock() is False


# ------------------------------------------------------------ own channels

def test_one_users_failing_sync_does_not_stop_the_others(monkeypatch):
    conn = db.get_conn()
    conn.execute("DELETE FROM own_channels")
    for uid, ch in ((701, "UCsingletonA"), (702, "UCsingletonB")):
        conn.execute("INSERT INTO own_channels (user_id, channel_id) VALUES (?, ?)", (uid, ch))
    conn.commit()
    conn.close()
    real = PL.plan_for

    def everything(uid):
        ent = real(uid)
        return type(ent)(ent.plan_id, ent.name, ent.limits, {**ent.features, "own_channels": True})

    monkeypatch.setattr(PL, "plan_for", everything)

    def sync(user_id, today=None):
        if user_id == 701:
            raise RuntimeError("token revoked at Google: secret-ish detail")
        return {"synced": 1}

    monkeypatch.setattr(OWN, "sync", sync)
    out = OWN.sync_all()
    assert out["702"] == {"synced": 1}
    assert out["701"] == {"error": "RuntimeError"}        # no message text: it may carry secrets
    conn = db.get_conn()
    conn.execute("DELETE FROM own_channels WHERE user_id IN (701, 702)")
    conn.commit()
    conn.close()


# ------------------------------------------------------------ delivery window

class _Stub:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


def test_delivery_reads_only_the_recent_window(monkeypatch):
    uid = 703
    conn = db.get_conn()
    conn.execute("DELETE FROM events WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM alert_deliveries WHERE user_id = ?", (uid,))
    payload = json.dumps({"videoId": "vwin", "channelId": "UCwin", "title": "Old or new",
                          "outlierScore": 5.0, "views": 1000})
    now = datetime.now(timezone.utc)
    for ref, age in (("vwin-new", timedelta(hours=1)),
                     ("vwin-old", timedelta(days=alerts.NOTIFY_WINDOW_DAYS + 1))):
        conn.execute("INSERT INTO events (kind, ref_id, payload, created_at, user_id) "
                     "VALUES ('outlier', ?, ?, ?, ?)", (ref, payload, (now - age).isoformat(), uid))
    conn.commit()
    conn.close()
    stub = _Stub()
    monkeypatch.setattr(NS, "mode_for", lambda user_id=uid: "instant")
    monkeypatch.setattr(NS, "notifier_for", lambda user_id=uid: stub)
    monkeypatch.setattr(NS, "target_name", lambda user_id=uid: "webhook")
    out = alerts.deliver(user_id=uid)
    assert out["sent"] == 1 and len(stub.sent) == 1        # the week-old backlog is not sent


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

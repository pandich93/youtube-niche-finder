"""Multi-user plan P1-10: count limits out of the box, and no race at the last
free row. The base tariff caps tracked channels (NF_USER_MAX_TRACKED_CHANNELS,
200) and own channels (NF_USER_MAX_OWN_CHANNELS, 5) -- the worker refreshes
every tracked channel from the shared pool; 0 in .env and an explicit null in
a tariff mean no limit; single-user mode has no count limits. ensure_room
counts and the caller inserts in one transaction under a per-user lock, so
two requests at once cannot both take the last row. Throwaway schema; no
network.
Run with pytest, or directly: python3 tests/test_plan_count_limits.py
"""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import library as LIB  # noqa: E402
from application import plans as PL  # noqa: E402
from application import topic_watch as TW  # noqa: E402
from domain import plans as DP  # noqa: E402

PW = "correct horse battery"


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    for name in ("NF_USER_MAX_TRACKED_CHANNELS", "NF_USER_MAX_OWN_CHANNELS"):
        monkeypatch.delenv(name, raising=False)
    conn = db.get_conn()
    for t in ("plan_changes", "user_plans", "tracked_channels", "saved_items", "user_topics",
              "sessions"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM plans WHERE slug != 'free'")
    conn.execute("UPDATE plans SET is_base = 1, limits = '{}'::jsonb, features = '{}'::jsonb "
                 "WHERE slug = 'free'")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()


def _user(limits=None, email="lim@example.com"):
    uid = AUTH.create_user(email, PW)
    if limits is not None:
        PL.choose(uid, PL.create_plan({"slug": "tight", "name": "Tight", "limits": limits})["id"])
    return uid


# ------------------------------------------------------------ defaults

def test_the_base_plan_caps_tracked_and_own_channels():
    lim = PL.plan_for(_user()).limits
    assert lim["tracked_channels"] == 200 and lim["own_channels"] == 5
    assert lim["saved_items"] is None and lim["drafts"] is None


def test_the_caps_come_from_env_and_zero_means_none(monkeypatch):
    monkeypatch.setenv("NF_USER_MAX_TRACKED_CHANNELS", "30")
    monkeypatch.setenv("NF_USER_MAX_OWN_CHANNELS", "0")
    lim = DP.resolve_limits({})
    assert lim["tracked_channels"] == 30 and lim["own_channels"] is None


def test_a_tariff_with_an_explicit_null_stays_unlimited():
    lim = PL.plan_for(_user({"tracked_channels": None})).limits
    assert lim["tracked_channels"] is None and lim["own_channels"] == 5


def test_single_user_mode_has_no_count_limits(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "0")
    lim = PL.plan_for(1).limits
    assert all(lim[k] is None for k, s in DP.LIMITS.items() if s["kind"] == "count")
    assert lim["yt_units"] == DP.resolve_limits({})["yt_units"]


# ------------------------------------------------------------ topics (the 6th caller)

def test_topics_stop_at_the_plan_limit(monkeypatch):
    monkeypatch.setattr(TW, "_embed", lambda text: [0.1] * 8)
    uid = _user({"topics": 1})
    TW.add_topic("первая тема", user_id=uid)
    with pytest.raises(DP.PlanLimitReached):
        TW.add_topic("вторая тема", user_id=uid)
    assert len(TW.list_topics(user_id=uid)) == 1


# ------------------------------------------------------------ the race

def _saved(uid):
    conn = db.get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM saved_items WHERE user_id = ?",
                            (uid,)).fetchone()[0]
    finally:
        conn.close()


def test_a_second_check_waits_for_the_first_insert():
    uid = _user({"saved_items": 1})
    first = db.get_conn()
    PL.ensure_room(uid, "saved_items", conn=first)          # room for the last row, lock held
    outcome = []

    def second():
        conn = db.get_conn()
        try:
            PL.ensure_room(uid, "saved_items", conn=conn)
            outcome.append("room")
        except DP.PlanLimitReached:
            outcome.append("full")
        finally:
            conn.close()

    t = threading.Thread(target=second)
    t.start()
    t.join(0.5)
    assert t.is_alive() and outcome == []                  # waiting, not counting a stale 0
    first.execute("INSERT INTO saved_items (user_id, kind, ref_id, folder, created_at) "
                  "VALUES (?, 'video', 'v1', 'default', ?)", (uid, db.now_iso()))
    first.commit()
    first.close()
    t.join(10)
    assert outcome == ["full"]


def test_parallel_saves_never_go_past_the_limit():
    uid = _user({"saved_items": 3})
    start, results = threading.Barrier(8), []

    def save(i):
        start.wait()
        try:
            LIB.save_item("video", f"v{i}", user_id=uid)
            results.append("ok")
        except DP.PlanLimitReached:
            results.append("full")

    threads = [threading.Thread(target=save, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert results.count("ok") == 3 and results.count("full") == 5
    assert _saved(uid) == 3


def test_the_lock_is_per_user():
    a = _user({"saved_items": 1})
    b = AUTH.create_user("other@example.com", PW)
    PL.choose(b, PL.plan_for(a).plan_id)                    # same tight plan, own lock
    held = db.get_conn()
    try:
        PL.ensure_room(a, "saved_items", conn=held)
        LIB.save_item("video", "vb", user_id=b)            # not blocked by a's lock
    finally:
        held.close()
    assert _saved(b) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

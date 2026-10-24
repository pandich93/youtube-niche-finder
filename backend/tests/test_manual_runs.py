"""Multi-user plan P2-11: a regular user may start part of the server's work by
hand -- refresh and alert scan, over their own watchlist only -- within the
plan limit `manual_runs` (runs per hour per operation; .env default
NF_USER_MANUAL_RUNS_PER_HOUR=0 = admins only, as before). Past the limit: 429
with Retry-After. The counter lives in Postgres under an advisory lock, so
parallel requests cannot both take the last run. Admins are never limited.
Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_manual_runs.py
"""
import os
import sys
import threading
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import collecting as collector  # noqa: E402
from application import manual_runs as MRUN  # noqa: E402
from domain import manual_runs as DM  # noqa: E402
from domain import plans as PD  # noqa: E402

PW = "correct horse battery"
CH_A = "UC" + "manualrunsa".ljust(22, "0")
CH_B = "UC" + "manualrunsb".ljust(22, "0")


def setup_module(_=None):
    db.init_db()


def login(email, admin=False):
    uid = AUTH.create_user(email, PW, is_admin=admin)
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    c.uid = uid
    return c


def _track(uid, channel_id):
    conn = db.get_conn()
    conn.execute("INSERT INTO tracked_channels (user_id, channel_id, active, added_at) "
                 "VALUES (?, ?, 1, ?)", (uid, channel_id, db.now_iso()))
    conn.commit()
    conn.close()


@pytest.fixture
def calls(monkeypatch):
    """The use cases the routes start, stubbed: what each was asked to cover."""
    seen = []
    monkeypatch.setattr(api.collector, "refresh_stats",
                        lambda *a, **k: seen.append(("refresh_stats", k)) or {"refreshed": 0})
    monkeypatch.setattr(api.collector, "refresh_channels",
                        lambda *a, **k: seen.append(("refresh_channels", k)) or {"refreshed": 0})
    monkeypatch.setattr(api.AL, "scan", lambda *a, **k: seen.append(("scan", k)) or {"ok": True})
    monkeypatch.setattr(api, "API_KEY", "key")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.delenv("NF_USER_MANUAL_RUNS_PER_HOUR", raising=False)
    conn = db.get_conn()
    for t in ("sessions", "api_tokens", "manual_runs", "tracked_channels", "user_plans"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM plans WHERE is_base = 0")
    conn.execute("UPDATE plans SET limits = '{}'::jsonb WHERE is_base = 1")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    return seen


@pytest.fixture
def people(monkeypatch, calls):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    return login("boss@example.com", admin=True), login("a@example.com"), login("b@example.com")


@pytest.fixture
def hourly(monkeypatch, people):
    monkeypatch.setenv("NF_USER_MANUAL_RUNS_PER_HOUR", "1")
    return people


ROUTES = {"refresh": "/api/refresh", "scan": "/api/events/scan"}


# ------------------------------------------------------------ the default is unchanged

@pytest.mark.parametrize("op", sorted(ROUTES))
def test_by_default_only_an_admin_runs_them(people, calls, op):
    _boss, a, _b = people
    r = a.post(ROUTES[op], json={})
    assert r.status_code == 403 and "администратор" in r.json()["detail"]
    assert calls == []
    st = a.get("/api/manual-runs").json()
    assert st["perHour"] == 0 and st["operations"][op] == {"allowed": False, "nextAt": None}


# ------------------------------------------------------------ with the limit on

@pytest.mark.parametrize("op", sorted(ROUTES))
def test_first_run_passes_the_second_gets_429_with_retry_after(hourly, op):
    _boss, a, _b = hourly
    assert a.post(ROUTES[op], json={}).status_code == 200
    r = a.post(ROUTES[op], json={}, headers={"X-NF-Locale": "en"})
    assert r.status_code == 429
    retry = int(r.headers["Retry-After"])
    assert 3500 < retry <= 3600
    body = r.json()
    assert body["error"] == "ManualRunLimit" and body["operation"] == op and body["limit"] == 1
    assert body["retryAfter"] == retry and body["nextAt"]
    assert body["detail"].startswith("You can run this by hand at most 1 time(s) an hour")
    st = a.get("/api/manual-runs").json()
    assert st["operations"][op]["allowed"] is False and st["operations"][op]["nextAt"]


def test_the_limit_is_per_operation(hourly):
    _boss, a, _b = hourly
    assert a.post("/api/refresh", json={}).status_code == 200
    assert a.post("/api/events/scan", json={}).status_code == 200      # its own hour
    assert a.post("/api/refresh", json={}).status_code == 429


def test_users_do_not_share_the_limit(hourly):
    _boss, a, b = hourly
    assert a.post("/api/refresh", json={}).status_code == 200
    assert a.post("/api/refresh", json={}).status_code == 429
    assert b.post("/api/refresh", json={}).status_code == 200
    assert b.get("/api/manual-runs").json()["operations"]["scan"]["allowed"] is True


def test_an_admin_is_never_limited(hourly, calls):
    boss, _a, _b = hourly
    for _ in range(3):
        assert boss.post("/api/refresh", json={}).status_code == 200
        assert boss.post("/api/events/scan", json={}).status_code == 200
    conn = db.get_conn()
    assert conn.execute("SELECT COUNT(*) FROM manual_runs").fetchone()[0] == 0
    conn.close()
    assert boss.get("/api/manual-runs").json()["admin"] is True
    # the admin's run is the corpus-wide one, as before
    assert ("scan", {}) in calls
    assert any(n == "refresh_channels" and k.get("only_tracked") for n, k in calls)


def test_the_hour_passes(hourly):
    _boss, a, _b = hourly
    assert a.post("/api/refresh", json={}).status_code == 200
    then = (datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat(timespec="microseconds")
    conn = db.get_conn()
    conn.execute("UPDATE manual_runs SET started_at = ?", (then,))
    conn.commit()
    conn.close()
    assert a.post("/api/refresh", json={}).status_code == 200
    conn = db.get_conn()
    assert conn.execute("SELECT COUNT(*) FROM manual_runs").fetchone()[0] == 1    # old row gone
    conn.close()


def test_a_failed_run_does_not_count(hourly, monkeypatch):
    _boss, a, _b = hourly
    monkeypatch.setattr(api, "API_KEY", "")
    assert a.post("/api/refresh", json={}).status_code == 428
    monkeypatch.setattr(api, "API_KEY", "key")
    assert a.post("/api/refresh", json={}).status_code == 200


def test_the_plan_sets_the_limit(people):
    _boss, a, _b = people
    conn = db.get_conn()
    conn.execute("""UPDATE plans SET limits = '{"manual_runs": 2}'::jsonb WHERE is_base = 1""")
    conn.commit()
    conn.close()
    assert a.post("/api/events/scan", json={}).status_code == 200
    assert a.post("/api/events/scan", json={}).status_code == 200
    assert a.post("/api/events/scan", json={}).status_code == 429
    assert a.get("/api/manual-runs").json()["perHour"] == 2


def test_a_user_run_covers_their_own_watchlist_only(hourly, calls):
    _boss, a, b = hourly
    _track(a.uid, CH_A)
    _track(b.uid, CH_B)
    assert a.post("/api/refresh", json={"scope": "all", "limit": 99999}).status_code == 200
    assert a.post("/api/events/scan", json={}).status_code == 200
    stats = next(k for n, k in calls if n == "refresh_stats")
    assert stats["scope"] == "channels" and stats["channel_ids"] == [CH_A]
    assert stats["limit"] == 1000                                   # not the caller's 99999
    assert ("refresh_channels", {"channel_ids": [CH_A]}) in calls
    assert ("scan", {"channel_ids": [CH_A]}) in calls


# ------------------------------------------------------------ parallel requests

def test_parallel_runs_do_not_both_pass(hourly):
    _boss, a, _b = hourly
    gate = threading.Barrier(4)
    out = []

    def go():
        gate.wait()
        try:
            out.append(MRUN.start("refresh", user_id=a.uid))
        except DM.ManualRunLimited:
            out.append("limited")

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(map(str, out)).count("limited") == 3


def test_a_check_waits_while_another_one_is_running(hourly):
    _boss, a, _b = hourly
    other = db.get_conn()
    db.lock_until_commit(other, "manual_run:scan", a.uid)          # another request mid-check
    got = []

    def go():
        try:
            got.append(MRUN.start("scan", user_id=a.uid))
        except DM.ManualRunLimited:
            got.append("limited")

    t = threading.Thread(target=go)
    t.start()
    t.join(0.5)
    assert t.is_alive() and got == []                               # waits for it
    other.execute("INSERT INTO manual_runs (user_id, operation, started_at) VALUES (?, 'scan', ?)",
                  (a.uid, datetime.now(timezone.utc).isoformat(timespec="microseconds")))
    other.commit()
    other.close()
    t.join(10)
    assert got == ["limited"]                                       # sees the other's run


# ------------------------------------------------------------ rules and single-user mode

def test_single_user_mode_is_unchanged(calls, monkeypatch):
    monkeypatch.delenv("NF_MULTI_USER", raising=False)
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    for _ in range(2):
        assert c.post("/api/refresh", json={}).status_code == 200
    assert c.get("/api/manual-runs").json()["admin"] is True


def test_zero_means_admins_only_and_none_no_limit(people, monkeypatch):
    _boss, a, _b = people
    with pytest.raises(PD.AdminOnly):
        MRUN.start("scan", user_id=a.uid)
    conn = db.get_conn()
    conn.execute("""UPDATE plans SET limits = '{"manual_runs": null}'::jsonb WHERE is_base = 1""")
    conn.commit()
    conn.close()
    assert MRUN.start("scan", user_id=a.uid) is None                # nothing to count
    assert a.get("/api/manual-runs").json()["operations"]["scan"]["allowed"] is True


def test_next_at_rule():
    now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    runs = [now - timedelta(minutes=50), now - timedelta(minutes=10)]
    assert DM.next_at(runs, 3, now) is None
    assert DM.next_at(runs, 2, now) == now + timedelta(minutes=10)
    assert DM.next_at(runs, 1, now) == now + timedelta(minutes=50)
    assert DM.next_at(runs + [now - timedelta(minutes=90)], 2, now) == now + timedelta(minutes=10)
    assert DM.next_at(runs, None, now) is None
    assert DM.retry_after(now + timedelta(seconds=0.2), now) == 1


def test_refresh_stats_channels_scope_reads_only_those_channels(monkeypatch):
    conn = db.get_conn()
    for vid, cid in (("vmr_a1", CH_A), ("vmr_a2", CH_A), ("vmr_b1", CH_B)):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at) "
                     "VALUES (?,?,?,?) ON CONFLICT (video_id) DO NOTHING",
                     (vid, cid, vid, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    asked = []
    monkeypatch.setattr(yt, "videos_batch_get_stats",
                        lambda key, ids: asked.extend(ids) or ([], []))
    out = collector.refresh_stats("k", scope="channels", period="30d", channel_ids=[CH_A])
    assert sorted(asked) == ["vmr_a1", "vmr_a2"] and out["requested"] == 2
    assert collector.refresh_stats("k", scope="channels", channel_ids=[])["refreshed"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

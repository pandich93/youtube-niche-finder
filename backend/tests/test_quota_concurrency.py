"""Multi-user plan P0-05: the YouTube budget is reserved before a request is
sent, in one atomic step per counter -- so parallel requests never take a
user past their share, users never take the shared pool past what is left
for them (the worker keeps a reserve), the search counter loses no update,
and an unsent request costs nothing. requests.get is faked with a small
delay; the counters are the real Postgres meta rows. Throwaway schema.
At most 16 threads: the connection pool holds 20 per process.
Run with pytest, or directly: python3 tests/test_quota_concurrency.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
import requests  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
from application import collecting as collector  # noqa: E402
from infrastructure import quota_owner  # noqa: E402


class _Resp:
    def __init__(self, status=200):
        self.status_code = status
        self.text = "{}"

    def json(self):
        return {"items": []}


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def google(monkeypatch):
    """requests.get stand-in that counts what reached 'Google'."""
    conn = db.get_conn()
    conn.execute("DELETE FROM meta WHERE key LIKE 'yt_units_%' OR key LIKE 'search_calls_%'")
    conn.commit()
    conn.close()
    sent = []
    lock = threading.Lock()

    def get(url, **kw):
        time.sleep(0.02)                     # long enough for threads to overlap
        with lock:
            sent.append(url.rsplit("/", 1)[-1])
        return _Resp()

    monkeypatch.setattr(yt.requests, "get", get)
    monkeypatch.setenv("NF_USER_DAILY_UNITS", "0")          # 0 in .env = no personal limit
    monkeypatch.setenv("NF_USER_DAILY_SEARCH_CALLS", "0")
    return sent


def _in_threads(n, call):
    """Run call(i) in n threads at once; per thread 'sent' or the exception's class name."""
    out = [None] * n
    start = threading.Barrier(n)

    def run(i):
        start.wait()
        try:
            call(i)
            out[i] = "sent"
        except Exception as e:                # noqa: BLE001 -- the outcome is the test
            out[i] = type(e).__name__
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def _as(user_id, path):
    def call(_i):
        token = quota_owner.set_owner(user_id) if user_id is not None else None
        try:
            yt._get(path, "KEY", retries=1)
        finally:
            if token is not None:
                quota_owner.reset(token)
    return call


def _meta(key):
    conn = db.get_conn()
    try:
        return int(db.get_meta(conn, key) or 0)
    finally:
        conn.close()


def test_parallel_requests_never_exceed_a_users_share(google, monkeypatch):
    monkeypatch.setenv("NF_USER_DAILY_SEARCH_CALLS", "5")
    out = _in_threads(16, _as(301, "search"))
    assert out.count("sent") == 5 and out.count("UserQuotaExceeded") == 11, out
    assert len(google) == 5
    assert _meta(yt.user_search_key(301)) == 5 and _meta(yt.user_units_key(301)) == 5


def test_parallel_requests_never_exceed_the_shared_pool(google, monkeypatch):
    monkeypatch.setattr(yt, "DAILY_UNIT_LIMIT", 12)
    monkeypatch.setattr(yt, "WORKER_RESERVE_UNITS", 4)
    out = _in_threads(16, lambda i: _as(400 + i, "videos")(i))      # 16 different users
    assert out.count("sent") == 8 and out.count("SharedPoolExhausted") == 8, out
    assert _meta(yt.units_meta_key()) == 8


def test_users_stop_at_pool_minus_worker_reserve_and_the_worker_goes_on(google, monkeypatch):
    monkeypatch.setattr(yt, "DAILY_UNIT_LIMIT", 10)
    monkeypatch.setattr(yt, "WORKER_RESERVE_UNITS", 4)
    for _ in range(6):
        _as(501, "videos")(0)
    with pytest.raises(yt.SharedPoolExhausted) as e:
        _as(502, "videos")(0)                                       # a fresh user, pool for users spent
    assert isinstance(e.value, yt.QuotaExceeded) and not isinstance(e.value, yt.UserQuotaExceeded)
    for _ in range(4):
        _as(None, "videos")(0)                                      # the worker: its reserve
    with pytest.raises(yt.SharedPoolExhausted):
        _as(None, "videos")(0)
    assert _meta(yt.units_meta_key()) == 10 and len(google) == 10


def test_search_counter_loses_no_updates(google):
    out = _in_threads(16, _as(None, "search"))
    assert out.count("sent") == 16, out
    conn = db.get_conn()
    try:
        assert collector.search_calls_today(conn) == 16
    finally:
        conn.close()


def test_the_search_bucket_is_capped_for_everyone(google, monkeypatch):
    monkeypatch.setattr(yt, "SEARCH_DAILY_CALL_LIMIT", 3)
    out = _in_threads(8, _as(None, "search"))
    assert out.count("sent") == 3 and out.count("SharedPoolExhausted") == 5, out


def test_unsent_request_is_refunded(google, monkeypatch):
    def down(url, **kw):
        raise requests.exceptions.ConnectionError("no route")
    monkeypatch.setattr(yt.requests, "get", down)
    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    monkeypatch.setenv("NF_USER_DAILY_UNITS", "50")
    token = quota_owner.set_owner(601)
    try:
        with pytest.raises(yt.YouTubeUnreachable):
            yt._get("search", "KEY", retries=3)
    finally:
        quota_owner.reset(token)
    for key in (yt.units_meta_key(), yt.search_meta_key(), yt.user_units_key(601),
                yt.user_search_key(601)):
        assert _meta(key) == 0, key


def test_an_answered_request_is_charged_even_when_it_failed(google, monkeypatch):
    # Google charges failed requests too; a retry after a 500 is a second charge
    monkeypatch.setattr(yt.requests, "get", lambda url, **kw: _Resp(500))
    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        yt._get("videos", "KEY", retries=3)
    assert _meta(yt.units_meta_key()) == 3


def test_an_accounting_failure_keeps_the_request_unsent(google, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("database is down")
    monkeypatch.setattr(db, "incr_meta_capped", broken, raising=False)
    with pytest.raises(RuntimeError, match="database is down"):
        yt._get("videos", "KEY", retries=1)
    assert google == []                                              # fail closed


def test_admin_pool_summary_adds_up_user_limits(monkeypatch):
    from application import auth as AUTH
    from application import plans as PL
    conn = db.get_conn()
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = 1")
    conn.commit()
    conn.close()
    for email in ("p1@example.com", "p2@example.com", "p3@example.com"):
        AUTH.create_user(email, "correct horse battery")
    monkeypatch.setattr(yt, "user_limits", lambda uid=None: {"units": 3000, "searchCalls": 20})
    monkeypatch.setattr(yt, "DAILY_UNIT_LIMIT", 10000)
    monkeypatch.setattr(yt, "WORKER_RESERVE_UNITS", 1500)
    s = PL.pool_summary()
    assert s["openToUsers"] == 8500 and s["userLimitsTotal"] == 9000 and s["overbooked"] is True
    assert s["unlimitedUsers"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

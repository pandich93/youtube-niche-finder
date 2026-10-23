"""Multi-user plan P1-09: the LLM budget holds under parallel calls. Each call
reserves what it can cost before it is sent (llm_reservations), spent plus
held is checked under one lock, and the hold is settled to the real cost
after. Signed-in users share the budget minus LLM_WORKER_RESERVE_USD; the
worker may spend all of it. An OpenRouter answer without usage.cost is priced
from its tokens, never counted as free. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_llm_budget_reserve.py
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import httpx  # noqa: E402
import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import llm_gateway as gw  # noqa: E402
from infrastructure import quota_owner  # noqa: E402
from infrastructure.llm import factory  # noqa: E402
from infrastructure.llm import openrouter as OR  # noqa: E402
from infrastructure.llm.base import LLMResult  # noqa: E402


class _Slow:
    """Costs `cost` a call and says so up front; answers after `delay`."""

    def __init__(self, cost, delay=0.3, answer=True):
        self.cost, self.delay, self.answer, self.calls = cost, delay, answer, 0
        self.lock = threading.Lock()

    def max_cost_usd(self, model, system, user):
        return self.cost

    def complete_json(self, system, user, schema, *, model=None, max_tokens=1024):
        with self.lock:
            self.calls += 1
        time.sleep(self.delay)
        if not self.answer:
            return None
        return LLMResult(data={"ok": user}, model="paid/m", prompt_tokens=10,
                         completion_tokens=5, cost_usd=self.cost)


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    conn = db.get_conn()
    for t in ("llm_cache", "llm_usage", "llm_reservations", "user_plans"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM meta WHERE key = 'llm_budget_blocked_until'")
    conn.commit()
    conn.close()
    monkeypatch.setattr(gw, "DAILY_BUDGET_USD", 1.0)
    monkeypatch.setattr(gw, "WORKER_RESERVE_USD", 0.25)
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setenv("NF_USER_DAILY_LLM_USD", "0")          # no personal limit unless set


def _ask(owner, text):
    token = quota_owner.set_owner(owner) if owner is not None else None
    try:
        return gw.run("t", "sys", text, {}, private=True)
    finally:
        if token is not None:
            quota_owner.reset(token)


def _parallel(n, owner):
    start, out = threading.Barrier(n), []

    def one(i):
        start.wait()
        out.append(_ask(owner, f"q{i}"))

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return [r for r in out if r is not None]


def _spent():
    conn = db.get_conn()
    try:
        return gw.spent_today(conn), gw.held_today(conn)
    finally:
        conn.close()


def _spend(user_id, usd):
    conn = db.get_conn()
    conn.execute("INSERT INTO llm_usage (user_id, day, model, calls, prompt_tokens, "
                 "completion_tokens, cost_usd) VALUES (?,?,?,1,0,0,?)",
                 (user_id, gw.today_utc(), "seed", usd))
    conn.commit()
    conn.close()


# ------------------------------------------------------------ parallel calls

def test_parallel_worker_calls_never_go_past_the_budget(monkeypatch):
    stub = _Slow(0.4)
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    answered = _parallel(5, None)
    assert len(answered) == 2 and stub.calls == 2              # 0.8; a third would be 1.2
    spent, held = _spent()
    assert spent == pytest.approx(0.8) and held == 0           # holds settled


def test_parallel_calls_of_one_user_stop_at_their_limit(monkeypatch):
    monkeypatch.setenv("NF_USER_DAILY_LLM_USD", "0.5")
    stub = _Slow(0.2)
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    assert len(_parallel(4, 7001)) == 2                        # 0.4; a third would be 0.6
    assert stub.calls == 2


def test_a_reservation_waits_while_another_one_is_checked():
    other = db.get_conn()
    db.lock_until_commit(other, "llm_budget", 0)               # another call mid-check
    got = []
    t = threading.Thread(target=lambda: got.append(gw._reserve(None, None, 0.1)))
    t.start()
    t.join(0.5)
    assert t.is_alive() and got == []                          # waits, not reading stale sums
    other.rollback()
    other.close()
    t.join(10)
    assert got and got[0] is not None


# ------------------------------------------------------------ worker reserve

def test_users_leave_the_worker_its_reserve(monkeypatch):
    monkeypatch.setattr(factory, "get_provider", lambda: _Slow(0.1, delay=0))
    _spend(0, 0.7)                                              # the worker spent 0.70
    assert _ask(7002, "user") is None                           # 0.8 > 1.0 - 0.25
    assert _ask(None, "worker") == {"ok": "worker"}             # the worker may go on


# ------------------------------------------------------------ holds

def test_a_failed_call_gives_its_hold_back(monkeypatch):
    stub = _Slow(0.4, delay=0, answer=False)
    monkeypatch.setattr(factory, "get_provider", lambda: stub)
    assert _ask(None, "x") is None and stub.calls == 1
    assert _spent() == (0, 0)


def test_a_call_that_raises_gives_its_hold_back(monkeypatch):
    class Boom(_Slow):
        def complete_json(self, *a, **k):
            raise RuntimeError("network")
    monkeypatch.setattr(factory, "get_provider", lambda: Boom(0.4))
    with pytest.raises(RuntimeError):
        _ask(None, "x")
    assert _spent() == (0, 0)


def test_a_hold_left_by_a_crash_stops_counting(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(minutes=gw.RESERVATION_TTL_MINUTES + 1))
    conn = db.get_conn()
    conn.execute("INSERT INTO llm_reservations (user_id, day, usd, created_at) VALUES (0,?,0.9,?)",
                 (gw.today_utc(), old.isoformat()))
    conn.commit()
    conn.close()
    monkeypatch.setattr(factory, "get_provider", lambda: _Slow(0.4, delay=0))
    assert _ask(None, "x") == {"ok": "x"}


# ------------------------------------------------------------ OpenRouter prices

def _answer(model, usage):
    class R:
        status_code = 200

        def json(self):
            return {"model": model, "choices": [{"message": {"content": '{"ok": true}'}}],
                    "usage": usage}
    return R()


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@pytest.fixture
def prices(monkeypatch):
    monkeypatch.setattr(OR, "_PRICES", {"at": 0.0, "table": None})
    fetched = []

    def fetch():
        fetched.append(1)
        return {"paid/m": (0.000002, 0.00001)}
    monkeypatch.setattr(OR, "_fetch_prices", fetch)
    return fetched


def test_an_answer_without_cost_is_priced_from_its_tokens(monkeypatch, prices):
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, headers=None, json=None:
                        _answer("paid/m", {"prompt_tokens": 1000, "completion_tokens": 100}))
    r = OR.OpenRouterProvider("k").complete_json("s", "u", SCHEMA, model="paid/m")
    assert r.cost_usd == pytest.approx(1000 * 0.000002 + 100 * 0.00001)


def test_an_unknown_model_counts_at_the_fallback_price(monkeypatch, prices):
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, headers=None, json=None:
                        _answer("other/x", {"prompt_tokens": 1000, "completion_tokens": 0}))
    r = OR.OpenRouterProvider("k").complete_json("s", "u", SCHEMA, model="other/x")
    assert r.cost_usd == pytest.approx(1000 * OR.FALLBACK_USD_PER_MTOK / 1e6)


def test_free_models_cost_nothing_and_never_fetch_prices(monkeypatch, prices):
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, headers=None, json=None:
                        _answer("a/b:free", {"prompt_tokens": 1000, "completion_tokens": 100}))
    p = OR.OpenRouterProvider("k")
    assert p.complete_json("s", "u", SCHEMA).cost_usd == 0
    assert p.max_cost_usd(None, "s", "u") == 0                  # the free fallback chain
    assert prices == []


def test_the_reservation_covers_a_whole_answer(prices):
    p = OR.OpenRouterProvider("k")
    est = p.max_cost_usd("paid/m", "s" * 200, "u" * 200, max_tokens=1024)
    assert est == pytest.approx((400 // 2 + 16) * 0.000002 + 1024 * 0.00001)
    p.max_cost_usd("paid/m", "s", "u")
    assert prices == [1]                                        # fetched once, then cached


def test_a_failed_price_fetch_falls_back_and_waits_before_retrying(monkeypatch):
    monkeypatch.setattr(OR, "_PRICES", {"at": 0.0, "table": None})
    tries = []

    def down():
        tries.append(1)
        raise httpx.ConnectError("down")
    monkeypatch.setattr(OR, "_fetch_prices", down)
    p = OR.OpenRouterProvider("k")
    assert p.price("paid/m") == (OR.FALLBACK_USD_PER_MTOK / 1e6,) * 2
    p.price("paid/m")
    assert tries == [1]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

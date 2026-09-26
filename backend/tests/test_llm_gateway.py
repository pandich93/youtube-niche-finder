"""Tests for application/llm_gateway.py: cache hits skip the provider, the
daily USD budget blocks further calls once spent, and db_stats() exposes
the llm block. Same throwaway-schema setup as test_smoke.py -- the provider
is monkeypatched everywhere below, so no network and no real LLM_PROVIDER
env is needed. Run with pytest, or directly: python3 tests/test_llm_gateway.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db  # noqa: E402
from application import llm_gateway  # noqa: E402
from application import search as query  # noqa: E402
from infrastructure.llm import factory  # noqa: E402
from infrastructure.llm.base import LLMResult  # noqa: E402

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def setup_module(_=None):
    os.environ.pop("LLM_PROVIDER", None)
    db.init_db()


class _StubProvider:
    def __init__(self, result=None):
        self.result = result
        self.calls = 0

    def complete_json(self, system, user, schema, *, model=None, max_tokens=1024):
        self.calls += 1
        return self.result


def _clear_llm_tables():
    conn = db.get_conn()
    conn.execute("DELETE FROM llm_cache")
    conn.execute("DELETE FROM llm_usage")
    conn.execute("DELETE FROM meta WHERE key='llm_budget_blocked_until'")
    conn.commit()
    conn.close()


def test_run_calls_the_provider_and_records_usage(monkeypatch):
    _clear_llm_tables()
    stub = _StubProvider(LLMResult(data={"ok": True}, model="m/x",
                                    prompt_tokens=10, completion_tokens=5, cost_usd=0.02))
    monkeypatch.setattr(factory, "get_provider", lambda: stub)

    data = llm_gateway.run("unit_test_task", "sys", "user text", SCHEMA)

    assert data == {"ok": True}
    assert stub.calls == 1

    conn = db.get_conn()
    row = conn.execute("SELECT calls, cost_usd FROM llm_usage WHERE model='m/x'").fetchone()
    conn.close()
    assert row["calls"] == 1
    assert float(row["cost_usd"]) == 0.02


def test_cache_hit_never_calls_the_provider_again(monkeypatch):
    _clear_llm_tables()
    stub = _StubProvider(LLMResult(data={"ok": True}, model="m/x",
                                    prompt_tokens=1, completion_tokens=1, cost_usd=0.001))
    monkeypatch.setattr(factory, "get_provider", lambda: stub)

    first = llm_gateway.run("cache_task", "sys", "same input", SCHEMA)
    second = llm_gateway.run("cache_task", "sys", "same input", SCHEMA)

    assert first == second == {"ok": True}
    assert stub.calls == 1


def test_budget_exhausted_blocks_further_calls(monkeypatch):
    _clear_llm_tables()
    monkeypatch.setattr(llm_gateway, "DAILY_BUDGET_USD", 0.01)
    stub = _StubProvider(LLMResult(data={"ok": True}, model="m/y",
                                    prompt_tokens=1, completion_tokens=1, cost_usd=0.02))
    monkeypatch.setattr(factory, "get_provider", lambda: stub)

    first = llm_gateway.run("budget_task_a", "sys", "input a", SCHEMA)
    assert first == {"ok": True}
    assert stub.calls == 1

    second = llm_gateway.run("budget_task_b", "sys", "input b", SCHEMA)
    assert second is None
    assert stub.calls == 1  # blocked before spending another call

    conn = db.get_conn()
    blocked = db.get_meta(conn, "llm_budget_blocked_until")
    conn.close()
    assert blocked == llm_gateway.today_utc()


def test_null_provider_by_default_spends_nothing():
    _clear_llm_tables()
    os.environ.pop("LLM_PROVIDER", None)  # exercise the real default path

    data = llm_gateway.run("default_task", "sys", "user text", SCHEMA)

    assert data is None


def test_db_stats_exposes_llm_block():
    _clear_llm_tables()
    os.environ.pop("LLM_PROVIDER", None)

    s = query.db_stats()

    assert "llm" in s
    assert s["llm"]["provider"] == "none"
    assert s["llm"]["blocked"] is False
    assert s["llm"]["today_cost_usd"] == 0.0


def test_default_model_is_none_unless_pinned_by_env():
    saved = os.environ.pop("OPENROUTER_MODEL", None)
    try:
        assert factory.default_model() is None

        os.environ["OPENROUTER_MODEL"] = "some/pinned-model"
        assert factory.default_model() == "some/pinned-model"
    finally:
        if saved is None:
            os.environ.pop("OPENROUTER_MODEL", None)
        else:
            os.environ["OPENROUTER_MODEL"] = saved


def test_display_model_names_the_free_fallback_chain_when_unpinned():
    saved = os.environ.pop("OPENROUTER_MODEL", None)
    try:
        assert "free" in factory.display_model()

        os.environ["OPENROUTER_MODEL"] = "some/pinned-model"
        assert factory.display_model() == "some/pinned-model"
    finally:
        if saved is None:
            os.environ.pop("OPENROUTER_MODEL", None)
        else:
            os.environ["OPENROUTER_MODEL"] = saved


if __name__ == "__main__":
    setup_module()

    class _Monkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._undo):
                setattr(obj, name, value)

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        mp = _Monkeypatch()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
        finally:
            mp.undo()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)

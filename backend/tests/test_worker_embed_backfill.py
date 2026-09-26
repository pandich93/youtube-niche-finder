"""Tests for the worker_cycle "backfill embeddings" step: cadence, the
WORKER_EMBED off-switch, and that a fastembed/backfill failure doesn't take
the rest of cycle() down with it.

Same throwaway-schema setup as test_smoke.py (tests/schema_scope.py: own
schema per process, dropped at exit) -- no network and no real fastembed;
backfill_embeddings is monkeypatched in every test below. Run with pytest,
or directly: python3 tests/test_worker_embed_backfill.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db  # noqa: E402
from application import alerts as alerts_mod  # noqa: E402
from application import collecting as collector  # noqa: E402
from application import worker_cycle as worker  # noqa: E402


def setup_module(_=None):
    db.init_db()


def _silence_other_steps(monkeypatch):
    """Keep cycle() from touching the network for every step but embed --
    marks rss/hot/alerts/daily as just-ran so _due() is False for them, and
    stubs the collector/alerts calls in case a test still forces one due."""
    for key in ("rss", "hot", "alerts", "daily"):
        worker._mark(key)
    monkeypatch.setattr(collector, "discover_new_videos_via_rss", lambda *a, **kw: {})
    monkeypatch.setattr(collector, "refresh_stats", lambda *a, **kw: {})
    monkeypatch.setattr(collector, "refresh_channels", lambda *a, **kw: {})
    monkeypatch.setattr(collector, "collect_trending", lambda *a, **kw: {})
    monkeypatch.setattr(alerts_mod, "scan", lambda *a, **kw: {})


def _force_embed_due():
    worker._set_meta("worker_last_embed", "2000-01-01T00:00:00+00:00")


def test_embed_step_runs_when_due_and_passes_the_batch_size(monkeypatch):
    _silence_other_steps(monkeypatch)
    _force_embed_due()
    calls = []
    monkeypatch.setattr(
        collector, "backfill_embeddings",
        lambda **kw: calls.append(kw) or
        {"scanned": 0, "embedded": 0, "remaining_without_embedding": 0})

    worker.cycle()

    assert len(calls) == 1
    assert calls[0].get("limit") == worker.EMBED_BATCH


def test_embed_step_does_not_run_before_its_interval(monkeypatch):
    _silence_other_steps(monkeypatch)
    worker._mark("embed")  # just ran -- not due again for EMBED_INTERVAL_MIN
    calls = []
    monkeypatch.setattr(collector, "backfill_embeddings", lambda **kw: calls.append(kw))

    worker.cycle()

    assert calls == []


def test_worker_embed_flag_disables_the_step_entirely(monkeypatch):
    _silence_other_steps(monkeypatch)
    _force_embed_due()
    monkeypatch.setattr(worker, "DO_EMBED", False)
    calls = []
    monkeypatch.setattr(collector, "backfill_embeddings", lambda **kw: calls.append(kw))

    worker.cycle()

    assert calls == []


def test_backfill_failure_does_not_crash_the_cycle(monkeypatch):
    _silence_other_steps(monkeypatch)
    _force_embed_due()

    def _boom(**kw):
        raise RuntimeError("fastembed model unavailable")

    monkeypatch.setattr(collector, "backfill_embeddings", _boom)

    worker.cycle()  # must not raise -- _safe() catches and logs instead


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

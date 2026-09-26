"""Tests for application/worker_cycle.py's scheduling: which steps cycle()
runs and when, the search.list quota back-off, and that one failing step
never takes the rest of the cycle down. Every collector / alerts / enrich /
clusters call is replaced by a recorder, so there is no network, no LLM and
no fastembed. main() is not covered: it is an endless sleep loop around
cycle(). Same throwaway-schema setup as test_worker_embed_backfill.py.
Run with pytest, or directly: python3 tests/test_worker_cycle.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest                                        # noqa: E402

import infrastructure.postgres as db                 # noqa: E402
import infrastructure.youtube.client as yt            # noqa: E402
from application import worker_cycle as worker        # noqa: E402
from application import collecting as collector       # noqa: E402
from application import alerts as alerts_mod           # noqa: E402
from application import enrichment as enrich_mod       # noqa: E402
from application import niche_clusters as clusters_mod  # noqa: E402
from application import maturity_curve as curve_mod   # noqa: E402
from domain import periods as P                        # noqa: E402

SCHEDULE_KEYS = ("rss", "hot", "alerts", "embed", "enrich", "daily", "clusters",
                 "calibrate")


def setup_module(_=None):
    db.init_db()


def _reset_schedule():
    conn = db.get_conn()
    conn.execute("DELETE FROM meta WHERE key LIKE 'worker_%'")
    conn.commit()
    conn.close()


def _make_due(key):
    worker._set_meta(f"worker_last_{key}", "2000-01-01T00:00:00+00:00")


@pytest.fixture
def calls(monkeypatch):
    """Stub every step cycle() can run; returns the list of step names in
    the order they were called."""
    _reset_schedule()
    seen = []

    def rec(name, result=None):
        def fn(*a, **kw):
            seen.append(name)
            return result if result is not None else {}
        return fn

    def refresh_stats(*a, **kw):
        seen.append(f"refresh_stats:{kw.get('period')}")
        return {}

    monkeypatch.setattr(worker, "log", lambda msg: None)
    monkeypatch.setattr(worker, "QUERIES", ["q1", "q2"])
    monkeypatch.setattr(worker, "DO_TRENDING", True)
    monkeypatch.setattr(worker, "DO_EMBED", True)
    monkeypatch.setattr(worker, "_llm_enrichment_enabled", lambda: True)
    monkeypatch.setattr(collector, "discover_new_videos_via_rss", rec("rss"))
    monkeypatch.setattr(collector, "refresh_stats", refresh_stats)
    monkeypatch.setattr(collector, "refresh_channels", rec("refresh_channels"))
    monkeypatch.setattr(collector, "collect_trending", rec("collect_trending"))
    monkeypatch.setattr(collector, "collect_niche",
                        lambda api_key, query, **kw: seen.append(f"collect:{query}") or {})
    monkeypatch.setattr(collector, "backfill_embeddings", rec("embed"))
    monkeypatch.setattr(alerts_mod, "scan", rec("alerts_scan"))
    monkeypatch.setattr(alerts_mod, "deliver", rec("alerts_deliver"))
    monkeypatch.setattr(enrich_mod, "classify_channels", rec("enrich_channels"))
    monkeypatch.setattr(enrich_mod, "tag_new_videos", rec("enrich_videos"))
    monkeypatch.setattr(clusters_mod, "compute_clusters", rec("clusters"))
    monkeypatch.setattr(curve_mod, "apply_calibration", rec("calibrate"))
    return seen


ALL_STEPS = [
    "rss",
    f"refresh_stats:{worker.HOT_PERIOD}",
    "alerts_scan", "alerts_deliver",
    "embed",
    "enrich_channels", "enrich_videos",
    f"refresh_stats:{worker.FULL_PERIOD}", "refresh_channels", "collect_trending",
    "collect:q1", "collect:q2",
    "clusters",
    "calibrate",
]


# ------------------------------------------------------------- scheduling

def test_first_cycle_runs_every_step_in_order_and_marks_them(calls):
    worker.cycle()
    assert calls == ALL_STEPS
    for key in SCHEDULE_KEYS:
        assert worker._get_meta(f"worker_last_{key}"), key


def test_second_cycle_right_after_runs_nothing(calls):
    worker.cycle()
    calls.clear()
    worker.cycle()
    assert calls == []


def test_only_the_step_that_fell_due_runs(calls):
    worker.cycle()
    calls.clear()
    _make_due("hot")
    worker.cycle()
    assert calls == [f"refresh_stats:{worker.HOT_PERIOD}"]


def test_optional_steps_are_skipped_when_switched_off(calls, monkeypatch):
    monkeypatch.setattr(worker, "DO_EMBED", False)
    monkeypatch.setattr(worker, "DO_TRENDING", False)
    monkeypatch.setattr(worker, "QUERIES", [])
    monkeypatch.setattr(worker, "_llm_enrichment_enabled", lambda: False)
    worker.cycle()
    for step in ("embed", "enrich_channels", "enrich_videos", "collect_trending",
                 "collect:q1", "collect:q2"):
        assert step not in calls, step
    # enrich is still marked, so it does not re-check the provider every cycle
    assert worker._get_meta("worker_last_enrich")


# ------------------------------------------------------ failures and quota

def test_a_failing_step_does_not_stop_the_rest(calls, monkeypatch):
    def boom(*a, **kw):
        calls.append("rss")
        raise RuntimeError("feed down")
    monkeypatch.setattr(collector, "discover_new_videos_via_rss", boom)
    worker.cycle()
    assert calls == ALL_STEPS
    assert worker._get_meta("worker_last_rss")  # marked anyway: no hot retry loop


def test_quota_exceeded_blocks_queries_until_the_pacific_day_ends(calls, monkeypatch):
    def quota(api_key, query, **kw):
        calls.append(f"collect:{query}")
        raise yt.QuotaExceeded("search bucket empty")
    monkeypatch.setattr(collector, "collect_niche", quota)
    worker.cycle()
    assert worker._get_meta("worker_quota_blocked_until") == P.pacific_date_key()
    assert worker._search_quota_blocked_today()

    calls.clear()
    _make_due("daily")
    worker.cycle()
    assert not [c for c in calls if c.startswith("collect:")]
    # the rest of the daily block still runs
    assert calls == [f"refresh_stats:{worker.FULL_PERIOD}", "refresh_channels",
                     "collect_trending"]


def test_a_block_from_an_earlier_day_does_not_skip_queries(calls):
    worker._set_meta("worker_quota_blocked_until", "2000-01-01")
    worker.cycle()
    assert "collect:q1" in calls and "collect:q2" in calls


def test_safe_returns_the_result_or_none():
    assert worker._safe("ok", lambda: {"n": 1}) == {"n": 1}
    assert worker._safe("boom", lambda: 1 / 0) is None


# --------------------------------------------------------------------- _due

def test_due_when_never_run_or_timestamp_is_garbage():
    _reset_schedule()
    assert worker._due("x", 60)
    worker._set_meta("worker_last_x", "not-a-date")
    assert worker._due("x", 60)


def test_due_respects_the_interval_with_naive_and_aware_timestamps():
    recent = datetime.now(timezone.utc) - timedelta(minutes=10)
    worker._set_meta("worker_last_x", recent.isoformat())
    assert not worker._due("x", 60)
    assert worker._due("x", 5)
    # a timestamp without tz (written by an older version) is read as UTC
    worker._set_meta("worker_last_x", recent.replace(tzinfo=None).isoformat())
    assert not worker._due("x", 60)
    assert worker._due("x", 5)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

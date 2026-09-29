"""load_window must look up each category title once per call, not once per
video: title_for is a SELECT, and one per row made a 120-day window thousands
of queries. Throwaway schema (tests/schema_scope.py); no network.
Run with pytest, or directly: python3 tests/test_enrich_category_titles.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import discovery as trends  # noqa: E402
from infrastructure.categories import repository as C  # noqa: E402

NOW = datetime.now(timezone.utc)


def setup_module(_=None):
    db.init_db()
    C.seed_fallback()


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("video_niches", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                 ("UCcat", "Cats", 1000))
    # 40 videos, 2 categories x (US, GB, no region) -> 6 distinct lookups at most
    for i in range(40):
        conn.execute(
            "INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
            "duration_seconds, is_short, category_id, region) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"cv{i}", "UCcat", f"t{i}", (NOW - timedelta(days=1 + i)).isoformat(), 100, 600, 0,
             ("10", "27")[i % 2], ("US", "GB", None)[i % 3]))
    conn.commit()
    conn.close()


def test_each_category_is_looked_up_once_per_call(monkeypatch):
    calls = []
    real = C.title_for

    def counting(cid, region="US"):
        calls.append((cid, region))
        return real(cid, region)
    monkeypatch.setattr(C, "title_for", counting)

    rows = trends.load_window(period="all")

    assert len(rows) == 40
    assert len(calls) == len(set(calls)) <= 6


def test_titles_are_the_same_as_a_direct_lookup():
    rows = trends.load_window(period="all")
    for r in rows:
        assert r["category"] == C.title_for(r["category_id"], r["region"] or "US")
    assert {r["category"] for r in rows} >= {"Music", "Education"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

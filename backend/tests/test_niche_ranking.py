"""Tests for application/niche_ranking.py (plan 27) and its HTTP/MCP doors:
the ranking of every niche, its hour of cache, the side-by-side comparison.
Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_niche_ranking.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import niche_ranking as NR  # noqa: E402

NOW = datetime.now(timezone.utc)
BUSY, SECOND, THIN = "busy-niche", "second-niche", "thin-niche"


def iso(days):
    return (NOW - timedelta(days=days)).isoformat()


def setup_module(_=None):
    db.init_db()


def _niche(conn, slug, prefix, n_channels=6):
    conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", (slug, slug, slug.title()))
    for c in range(n_channels):
        conn.execute("INSERT INTO channels (channel_id, title, subscriber_count, published_at) "
                     "VALUES (?,?,?,?)", (f"UC{prefix}{c}", f"{prefix} {c}", 5000, iso(900)))
    # 26 videos in the last 30 days and 34 in the 90 before
    for n, days in enumerate([4 + i for i in range(30)] + [31 + 3 * i for i in range(30)]):
        vid = f"{prefix}{n}"
        conn.execute(
            "INSERT INTO videos (video_id, channel_id, title, published_at, first_seen_at, "
            "view_count, duration_seconds, is_short, category_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (vid, f"UC{prefix}{n % n_channels}", f"video {n}", iso(days), iso(days - 1), 10_000,
             600, 0, "10"))
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (vid, slug))


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    conn = db.get_conn()
    for t in ("video_stats_history", "video_niches", "videos", "channels", "niches"):
        conn.execute(f"DELETE FROM {t}")
    _niche(conn, BUSY, "b")
    _niche(conn, SECOND, "s")
    conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", (THIN, THIN, THIN))
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                 ("UCthin", "Thin", 100))
    for i in range(5):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
                     "duration_seconds, is_short) VALUES (?,?,?,?,?,?,?)",
                     (f"thin{i}", "UCthin", "t", iso(5 + i), 100, 600, 0))
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (f"thin{i}", THIN))
    conn.commit()
    conn.close()
    # the template/policy passes read every channel; their own tests cover them
    monkeypatch.setattr(NR, "_template_share", lambda slug: 0.1)
    monkeypatch.setattr(NR, "_policy_share", lambda slug: 0.0)
    NR.reset_cache()
    yield
    NR.reset_cache()


def _by_niche(ranking):
    return {n["niche"]: n for n in ranking["niches"]}


def test_a_niche_with_a_trend_gets_a_score_and_a_small_one_does_not():
    r = NR.rank_niches()
    by = _by_niche(r)
    assert by[BUSY]["score"] is not None and 0 <= by[BUSY]["score"] <= 100
    assert by[BUSY]["band"] in ("high", "medium", "low")
    assert by[THIN]["score"] is None and by[THIN]["reason"] == "insufficient-data"
    assert r["niches"][-1]["niche"] == THIN             # unscored last
    assert r["weights"] == {"demand": 25, "supply": 20, "newcomers": 20, "rpm": 15,
                            "template": 10, "policy": 10}


def test_every_entry_carries_the_breakdown_and_the_raw_numbers():
    e = _by_niche(NR.rank_niches())[BUSY]
    assert [b["key"] for b in e["breakdown"]] == [
        "demand", "supply", "newcomers", "rpm", "template", "policy"]
    assert e["metrics"]["templateShare"] == 0.1 and e["metrics"]["policyShare"] == 0.0
    assert e["metrics"]["rpmMid"] == 2.1 and e["categoryId"] == "10"       # music: 3.0 x 0.7
    assert e["videos"] == 60 and e["recentVideos"] == 26 and e["label"] == "Busy-Niche"


def test_the_ranking_is_cached_and_refresh_recomputes(monkeypatch):
    calls = []
    real = NR._compute
    monkeypatch.setattr(NR, "_compute", lambda: calls.append(1) or real())
    NR.rank_niches()
    NR.rank_niches()
    assert len(calls) == 1
    NR.rank_niches(refresh=True)
    assert len(calls) == 2
    monkeypatch.setattr(NR, "CACHE_TTL_SEC", -1)           # everything is stale
    NR.rank_niches()
    assert len(calls) == 3


def test_two_niches_side_by_side_with_the_overview_numbers():
    c = NR.compare_niches([BUSY, SECOND])
    assert [n["niche"] for n in c["niches"]] == [BUSY, SECOND] and c["missing"] == []
    assert c["niches"][0]["overview"]["video_count"] == 60
    assert c["niches"][0]["overview"]["channel_count"] == 6
    assert c["niches"][0]["breakdown"] and c["weights"]["demand"] == 25


def test_an_unknown_niche_is_named_missing_and_the_rest_still_compared():
    c = NR.compare_niches([BUSY, "no-such", THIN])
    assert c["missing"] == ["no-such"] and [n["niche"] for n in c["niches"]] == [BUSY, THIN]
    assert c["niches"][1]["score"] is None


def test_the_number_of_niches_to_compare_is_two_or_three():
    for bad in ([], [BUSY], [BUSY, BUSY], [BUSY, SECOND, THIN, "x"], None):
        with pytest.raises(ValueError):
            NR.compare_niches(bad)


def test_http_and_mcp_doors():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    c = TestClient(api.app)
    r = c.get("/api/niches/ranking")
    assert r.status_code == 200 and r.json()["niches"][0]["niche"] in (BUSY, SECOND)
    assert c.get("/api/niches/ranking", params={"refresh": "true"}).status_code == 200
    r = c.get("/api/niches/compare", params={"slugs": f"{BUSY}, {SECOND}"})
    assert r.status_code == 200 and len(r.json()["niches"]) == 2
    assert c.get("/api/niches/compare", params={"slugs": BUSY}).status_code == 400
    assert len(srv.rank_niches()["niches"]) == 3
    assert len(srv.compare_niches([BUSY, SECOND])["niches"]) == 2
    assert "error" in srv.compare_niches([BUSY])

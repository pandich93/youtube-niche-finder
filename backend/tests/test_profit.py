"""Tests for application/profit.py (plan 30) and its HTTP/MCP doors: personal
cost profiles and the profit of a channel, a video and a niche.
Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_profit.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import profit as PF  # noqa: E402

NOW = datetime.now(timezone.utc)
CH, NICHE = "UCprofit", "profit-niche"


def iso(days):
    return (NOW - timedelta(days=days)).isoformat()


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("cost_profiles", "channel_stats_history", "video_niches", "videos", "channels", "niches"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", (NICHE, NICHE, NICHE))
    conn.execute("INSERT INTO channels (channel_id, title, subscriber_count, video_count, view_count) "
                 "VALUES (?,?,?,?,?)", (CH, "Profit channel", 500, 6, 60_000))
    for i in range(6):
        conn.execute(
            "INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
            "duration_seconds, is_short, category_id) VALUES (?,?,?,?,?,?,?,?)",
            (f"pv{i}", CH, f"video {i}", iso(5 + i * 10), 10_000, 600, 0, "27"))
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (f"pv{i}", NICHE))
    conn.commit()
    conn.close()


def test_profiles_are_created_changed_listed_and_removed():
    p = PF.save_profile("All AI", per_video_usd=5, per_minute_usd=0.5, monthly_usd=20)
    assert p["name"] == "All AI" and p["per_minute_usd"] == 0.5
    same = PF.save_profile("All AI", per_video_usd=7)                      # same name: an edit
    assert same["id"] == p["id"] and same["per_video_usd"] == 7.0 and same["monthly_usd"] == 0.0
    renamed = PF.save_profile("Voice + edit", per_video_usd=40, profile_id=p["id"])
    assert renamed["id"] == p["id"] and [x["name"] for x in PF.list_profiles()] == ["Voice + edit"]
    assert PF.delete_profile(p["id"])["removed"] and PF.list_profiles() == []
    assert not PF.delete_profile(p["id"])["removed"]


def test_bad_profiles_are_refused():
    for kw in ({"name": ""}, {"name": "x", "per_video_usd": -1}, {"name": "x", "monthly_usd": "abc"},
               {"name": "x", "per_minute_usd": float("nan")}, {"name": "x", "profile_id": 999}):
        with pytest.raises(ValueError):
            PF.save_profile(**kw)
    PF.save_profile("a")
    b = PF.save_profile("b")
    with pytest.raises(ValueError):
        PF.save_profile("a", profile_id=b["id"])                         # name taken


def test_profiles_are_personal():
    PF.save_profile("mine", per_video_usd=1, user_id=1)
    PF.save_profile("theirs", per_video_usd=2, user_id=2)
    assert [p["name"] for p in PF.list_profiles(user_id=2)] == ["theirs"]
    mine = PF.list_profiles(user_id=1)[0]
    assert not PF.delete_profile(mine["id"], user_id=2)["removed"]
    with pytest.raises(ValueError):
        PF.profit_estimate(video_id="pv0", profile=mine["id"], user_id=2)


def test_a_video_s_profit_uses_its_category_rpm_and_lifetime_views():
    PF.save_profile("p", per_video_usd=10, per_minute_usd=1)               # 10 + 10 min = 20
    r = PF.profit_estimate(video_id="pv0")
    v = r["video"]
    assert r["found"] and r["profile"]["name"] == "p" and v["cost"] == 20.0 and v["views"] == 10_000
    assert v["revenue"]["mid"] == round(10 * r["rpm"]["mid"], 2)
    assert v["breakEvenViews"]["low"] > v["breakEvenViews"]["high"]
    assert "hint" not in r


def test_a_channel_s_month_counts_its_recent_long_uploads():
    PF.save_profile("p", per_video_usd=10, monthly_usd=50)
    r = PF.profit_estimate(channel_id=CH)
    m = r["month"]
    assert r["found"] and r["target"]["title"] == "Profit channel"
    assert m["uploads"] == 3 and m["costPerVideo"] == 10.0 and m["cost"] == 80.0   # 5, 15, 25 days old
    assert r["uploadsBasis"].startswith("long videos")
    assert PF.profit_estimate(channel_id=CH, videos_per_month=10)["month"]["uploads"] == 10


def test_a_small_channel_is_warned_that_ad_revenue_may_be_zero():
    assert PF.profit_estimate(channel_id=CH)["yppWarning"]
    conn = db.get_conn()
    conn.execute("UPDATE channels SET subscriber_count = 5000 WHERE channel_id = ?", (CH,))
    conn.commit()
    conn.close()
    assert PF.profit_estimate(channel_id=CH)["yppWarning"] is None


def test_a_connected_channel_uses_its_real_rpm(monkeypatch):
    monkeypatch.setattr(PF, "_real_rpm", lambda cid, uid: 3.5)
    r = PF.profit_estimate(channel_id=CH)
    assert r["rpm"] == {"low": 3.5, "mid": 3.5, "high": 3.5} and "real" in r["rpmBasis"]
    assert r["yppWarning"] is None


def test_a_niche_s_typical_video_and_a_month_of_them():
    PF.save_profile("p", per_video_usd=10)
    r = PF.profit_estimate(niche=NICHE)
    assert r["found"] and r["target"]["format"] == "long" and r["video"]["views"] == 10_000
    assert r["month"]["uploads"] == PF.DEFAULT_VIDEOS_PER_MONTH
    assert r["month"]["monthlyViews"] == 10_000 * PF.DEFAULT_VIDEOS_PER_MONTH
    assert PF.profit_estimate(niche=NICHE, videos_per_month=2)["month"]["uploads"] == 2


def test_without_a_profile_the_costs_are_zero_and_the_result_says_so():
    r = PF.profit_estimate(video_id="pv0")
    assert r["video"]["cost"] == 0.0 and r["profile"]["id"] is None and "cost profile" in r["hint"]


def test_targets_and_inputs_are_checked():
    for kw in ({}, {"channel_id": CH, "video_id": "pv0"}, {"video_id": "pv0", "videos_per_month": -1},
               {"video_id": "pv0", "profile": "nope"}):
        with pytest.raises(ValueError):
            PF.profit_estimate(**kw)
    assert PF.profit_estimate(video_id="nope")["found"] is False
    assert PF.profit_estimate(channel_id="UCnope")["found"] is False
    assert PF.profit_estimate(niche="nope")["found"] is False


def test_http_and_mcp_doors():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    c = TestClient(api.app)
    r = c.post("/api/cost-profiles", json={"name": "web", "perVideoUsd": 3, "perMinuteUsd": 1})
    assert r.status_code == 200 and r.json()["per_video_usd"] == 3.0
    pid = r.json()["id"]
    assert c.post("/api/cost-profiles", json={"name": ""}).status_code == 400
    assert [p["id"] for p in c.get("/api/cost-profiles").json()["profiles"]] == [pid]
    r = c.get("/api/profit", params={"video_id": "pv0", "profile": "web"})
    assert r.status_code == 200 and r.json()["video"]["cost"] == 13.0
    assert c.get("/api/profit").status_code == 400
    client = {"X-NF-Client": "test"}
    assert c.delete(f"/api/cost-profiles/{pid}").status_code == 403         # the cross-site guard
    assert c.delete(f"/api/cost-profiles/{pid}", headers=client).status_code == 200
    assert c.delete(f"/api/cost-profiles/{pid}", headers=client).status_code == 404
    assert srv.save_cost_profile("mcp", per_video_usd=1)["name"] == "mcp"
    assert [p["name"] for p in srv.list_cost_profiles()["profiles"]] == ["mcp"]
    assert srv.profit_estimate(video_id="pv0")["video"]["cost"] == 1.0
    assert "error" in srv.profit_estimate()
    assert "error" in srv.save_cost_profile("")
    assert srv.delete_cost_profile(srv.list_cost_profiles()["profiles"][0]["id"])["removed"]

"""Smoke tests. Run with pytest, or directly:  python3 tests/test_smoke.py

Uses a throwaway Postgres schema seeded with synthetic data -- no YouTube API
key and no network required, but a reachable Postgres is (see db.py for the
POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD
env vars, or NICHE_DATABASE_URL for a single DSN). Each run gets its own schema
so it never collides with real data or a concurrent test run, and drops it again
when the process exits (see tests/schema_scope.py -- NICHE_KEEP_TEST_SCHEMA=1
keeps it for post-mortem, and a schema passed in via NICHE_DB_SCHEMA is never
dropped).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
from application import channel_tracking as T  # noqa: E402
from application import discovery as trends  # noqa: E402
from application import search as query  # noqa: E402
from domain import keywords as K  # noqa: E402
from domain import metrics as M  # noqa: E402
from domain import periods as P  # noqa: E402
from infrastructure.categories import repository as C  # noqa: E402
from seed_demo import seed  # noqa: E402


def setup_module(_=None):
    db.init_db()
    C.seed_fallback()
    seed()


# ------------------------------------------------------------------ units

def test_duration_parsing():
    assert yt.parse_duration("PT12M34S") == 754
    assert yt.parse_duration("P1DT2H3M4S") == 93784
    assert yt.parse_duration("PT45S") == 45
    assert yt.parse_duration("PT1H") == 3600
    assert yt.parse_duration("") == 0


def test_periods():
    assert P.parse_period("24h").total_seconds() == 86400
    assert P.parse_period("7d").days == 7
    assert P.parse_period("all") is None
    assert P.parse_period("месяц").days == 30
    start, end = P.window("24h", offset=1)
    assert start < end
    try:
        P.parse_period("banana")
    except ValueError:
        pass
    else:
        raise AssertionError("bad period should raise")


def test_outlier_formulas():
    # NexLev parity: views / floor(channelViews / channelVideos)
    assert M.outlier_score(320_000, 241, 3) == 320_000 / 80
    # median baseline ignores a single freak value, a mean would not
    assert M.outlier_vs_median(1000, [100, 100, 100, 100, 100_000]) == 10.0
    assert M.baseline_median([1, 2]) is None  # too little data
    assert M.outlier_band(12) == "mega outlier"
    assert M.outlier_band(1.0) == "normal"


def test_maturity_curve_is_monotonic():
    prev = 0
    for age in range(0, 31):
        cur = M.maturity(age)
        assert cur >= prev - 1e-9, f"curve dips at day {age}"
        prev = cur
    assert M.maturity(40) == 1.0
    # a 2-day-old video is projected upward, a 30-day-old one is not
    assert M.projected_30d_views(1000, 2) > 1000
    assert abs(M.projected_30d_views(1000, 30) - 1000) < 1e-6


def test_velocity_and_revenue():
    from datetime import datetime, timedelta, timezone
    t0 = datetime.now(timezone.utc)
    rows = [(t0 - timedelta(hours=48), 1000), (t0 - timedelta(hours=24), 2000), (t0, 5000)]
    assert M.vph_from_history(rows, 24) == 125.0  # (5000-2000)/24
    assert M.vph_from_history([], 24) is None
    r = M.revenue_range(1_000_000)
    assert r["low_usd"] == 250.0 and r["high_usd"] == 4000.0
    assert M.grade(4.0) and M.grade(None) is None


def test_keyword_scoring():
    rows = [{"video_id": "a", "title": "AI robot builds a house", "tags": "[]",
             "views": 100, "outlier": 5.0},
            {"video_id": "b", "title": "AI robot cooks dinner", "tags": "[]",
             "views": 100, "outlier": 4.0},
            {"video_id": "c", "title": "bread at home", "tags": "[]",
             "views": 100, "outlier": 0.2}]
    stats, total, base = K.aggregate(rows)
    out = K.score(stats, total, base, min_videos=2, top_n=5)
    kws = {k["keyword"] for k in out}
    assert "ai robot" in kws
    # sub-grams with identical coverage are collapsed away
    assert not ({"ai", "robot"} & kws), kws
    # a phrase present only in outliers must have lift > 1
    assert next(k for k in out if k["keyword"] == "ai robot")["outlierLift"] > 1


def test_categories():
    assert C.title_for("20") == "Gaming"
    assert C.is_assignable("21") is False
    assert C.rpm_niche("27") == "education"


# ------------------------------------------------------------ integration

def test_sections_return_data():
    cov = trends.coverage("30d")
    assert cov["videosTotal"] > 100 and cov["velocityMetricsAvailable"]

    v = trends.viral_videos_small_channels(period="30d", max_subscribers=10000,
                                           min_views=1000, limit=5)
    assert v["matched"] > 0 and v["quotaUsed"] == 0
    top = v["results"][0]
    for field in ("viewsPerSubscriber", "outlierScore", "outlierBand",
                  "projected30dViews", "viralScore"):
        assert field in top, field
    assert top["channelSubscribers"] <= 10000

    c = trends.most_popular_categories(period="14d")
    assert c["categories"] and c["categories"][0]["viewShare"] > 0
    assert c["categories"][0]["previousViewShare"] is not None

    k = trends.trending_keywords(period="14d", min_videos=3, sort_by="trend")
    assert k["keywords"] and k["keywords"][0]["trendScore"] >= 0
    assert k["videosAnalysed"] > 0


def test_sorting_is_respected():
    a = trends.viral_videos_small_channels(period="90d", max_subscribers=100000,
                                           min_views=0, min_views_per_subscriber=0,
                                           sort_by="views", limit=10)["results"]
    assert [r["views"] for r in a] == sorted([r["views"] for r in a], reverse=True)


def test_period_by_discovered_differs_from_published():
    """NexLev's "Last 24 hours" is a DISCOVERY window, not a publication one --
    that is why their list shows year-old videos. Both must work."""
    pub = trends.viral_videos_small_channels(period="24h", period_by="published",
                                             min_views=0, min_views_per_subscriber=0,
                                             max_subscribers=10**9, limit=100)
    disc = trends.viral_videos_small_channels(period="24h", period_by="discovered",
                                              min_views=0, min_views_per_subscriber=0,
                                              max_subscribers=10**9, limit=100)
    assert disc["matched"] > pub["matched"], (pub["matched"], disc["matched"])
    assert disc["periodBy"] == "discovered"
    # a discovery window legitimately contains videos published long ago
    assert any(r["ageDays"] > 7 for r in disc["results"])


def test_categories_rank_by_channels():
    by_views = trends.most_popular_categories(period="30d", rank_by="views")
    by_channels = trends.most_popular_categories(period="30d", rank_by="channels")
    assert by_views["rankedBy"] == "views"
    order = [c["channels"] for c in by_channels["categories"]]
    assert order == sorted(order, reverse=True)


def test_channel_level_discovery():
    r = T.recently_added_outlier_channels(period="30d", min_multiplier=1.0)
    assert r["channelsMatched"] > 0
    top = r["channels"][0]
    for field in ("multiplier", "strength", "band", "subscribers", "bestVideo"):
        assert field in top, field
    assert 0 <= top["strength"] <= 4
    mults = [c["multiplier"] for c in r["channels"]]
    assert mults == sorted(mults, reverse=True)

    h = T.high_future_competition(period="90d", min_videos=2)
    assert h["byCategory"] and h["channels"]
    assert all(c["competitionScore"] >= 0 for c in h["channels"])


def test_vph_matches_nexlev_definition():
    """NexLev's VPH badge is plain views / hours since publication: their cards
    show 2.46M views -> 233 VPH and 3.12M -> 297 VPH, both ~438 days old."""
    assert round(2_460_000 / (438 * 24)) == 234   # card showed 233
    assert round(3_120_000 / (438 * 24)) == 297   # card showed 297
    assert round(M.vph_lifetime(2_460_000, 438 * 24)) == 234


def test_channel_analytics_and_compare():
    T.track("UC0000000000000000000b", "test")
    a = T.channel_analytics("UC0000000000000000000b")
    assert a["found"] and a["profile"]["subscribers"] == 9400
    assert a["growthAvailable"] and a["growth"]["30d"]["views"]["delta"] > 0
    assert a["grade"] and a["topOutliers"]
    assert a["revenue"]["socialBladeRange"]["low_usd"] > 0

    cmp_ = T.compare_channels(["UC0000000000000000000a", "UC0000000000000000000d"])
    assert len(cmp_["channels"]) == 2
    # ranking is size-independent: the tiny channel beats the huge one on VSR
    assert cmp_["rankedByViewsPerSubscriber"][0] == "UC0000000000000000000a"

    assert T.channel_analytics("UCdoesnotexist")["found"] is False


def test_velocity_and_patterns():
    v = T.channel_velocity("UC0000000000000000000b", period="30d")
    assert any(x["vph24h"] is not None for x in v["videos"])
    p = T.title_patterns(period="90d", min_videos=3)
    assert p["patterns"]
    b = T.best_time_to_publish(period="90d", min_samples=2)
    assert b["bucketsScored"] > 0 and b["best"][0]["score"] == 100


def test_query_layer():
    o = query.search_outliers(period="90d", min_outlier_score=3, limit=5)
    assert o and all(r["outlierScore"] >= 3 for r in o)
    n = query.niche_overview("demo")
    assert n["found"] and n["channel_size_distribution"]["<1k"] == 1
    assert query.niche_overview("nope")["found"] is False
    s = query.db_stats()
    assert s["videos"] > 100 and s["video_stat_snapshots"] > 0


def test_everything_json_serialisable():
    for obj in (trends.viral_videos_small_channels(period="30d"),
                trends.most_popular_categories(period="30d"),
                trends.trending_keywords(period="30d"),
                T.channel_analytics("UC0000000000000000000b"),
                query.db_stats()):
        json.dumps(obj, default=str)


if __name__ == "__main__":
    setup_module()
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)

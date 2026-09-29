"""Tests for application/sponsors.py (plan 09): the description scan (worker
step and backfill) and the niche / channel sponsor map. Same throwaway-schema
setup as test_packaging.py (tests/schema_scope.py); no network.
Run with pytest, or directly: python3 tests/test_sponsors.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db  # noqa: E402
from application import sponsors as SPN  # noqa: E402
from domain import sponsors as SP  # noqa: E402

NOW = datetime.now(timezone.utc)
CH_A = "UC" + "sponsora".ljust(22, "0")
CH_B = "UC" + "sponsorb".ljust(22, "0")
CH_C = "UC" + "sponsorc".ljust(22, "0")
NICHE = "sponsor-niche"

SPONSORED_A = "This video is sponsored by Brilliant. Go to brilliant.org/x"
SPONSORED_B = "Thanks to Raycon for sponsoring!"
AFFILIATE = "Mic: https://amzn.to/abc"
PLAIN = "Just a video about cats."


def setup_module(_=None):
    db.init_db()


def _reset():
    conn = db.get_conn()
    for t in ("video_sponsors", "sponsor_scan", "video_niches", "videos", "channels", "niches"):
        conn.execute(f"DELETE FROM {t}")
    for cid, title, handle in ((CH_A, "Alpha Labs", "@alpha"), (CH_B, "Beta Channel", "@beta"),
                               (CH_C, "Gamma", None)):
        conn.execute("INSERT INTO channels (channel_id, title, custom_url) VALUES (?,?,?)",
                     (cid, title, handle))
    conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", (NICHE, "q", "Q"))
    conn.commit()
    conn.close()


def _video(vid, channel, description, views=1000, days=1, niche=NICHE):
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO videos (video_id, channel_id, title, description, published_at, view_count) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT (video_id) DO UPDATE SET description = EXCLUDED.description",
        (vid, channel, f"title {vid}", description, (NOW - timedelta(days=days)).isoformat(), views))
    if niche:
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?) "
                     "ON CONFLICT DO NOTHING", (vid, niche))
    conn.commit()
    conn.close()


def _rows(sql, params=()):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _six():
    _reset()
    _video("s1", CH_A, SPONSORED_A)
    _video("s2", CH_B, SPONSORED_B)
    _video("s3", CH_A, AFFILIATE)
    _video("s4", CH_A, PLAIN)
    _video("s5", CH_B, PLAIN)
    _video("s6", CH_B, "")


# ------------------------------------------------------------ schema

def test_tables_are_created_idempotently():
    db.init_db()
    db.init_db()
    assert _rows("SELECT * FROM video_sponsors") is not None
    assert _rows("SELECT * FROM sponsor_scan") is not None


# ------------------------------------------------------------ scan

def test_scan_counts_and_stores_signals():
    _six()
    out = SPN.scan_sponsors()
    assert out == {"scanned": 6, "withSignals": 3, "rows": 3, "remaining": 0}, out
    got = {(r["video_id"], r["brand"], r["kind"]) for r in _rows("SELECT * FROM video_sponsors")}
    assert got == {("s1", "brilliant", "sponsor"), ("s2", "raycon", "sponsor"),
                   ("s3", "amazon", "affiliate")}
    assert len(_rows("SELECT * FROM sponsor_scan")) == 6


def test_second_scan_finds_nothing_to_do():
    _six()
    SPN.scan_sponsors()
    assert SPN.scan_sponsors()["scanned"] == 0


def test_changed_description_rescans_only_that_video_and_drops_old_rows():
    _six()
    SPN.scan_sponsors()
    _video("s1", CH_A, "Now sponsored by NordVPN.")
    out = SPN.scan_sponsors()
    assert out["scanned"] == 1 and out["remaining"] == 0, out
    brands = {r["brand"] for r in _rows("SELECT brand FROM video_sponsors WHERE video_id='s1'")}
    assert brands == {"nordvpn"}
    assert len(_rows("SELECT * FROM video_sponsors")) == 3


def test_raised_rules_version_rescans_everything(monkeypatch):
    _six()
    SPN.scan_sponsors()
    monkeypatch.setattr(SP, "SPONSOR_RULES_VERSION", SP.SPONSOR_RULES_VERSION + 1)
    out = SPN.scan_sponsors()
    assert out["scanned"] == 6 and out["remaining"] == 0, out
    assert {r["rules_version"] for r in _rows("SELECT rules_version FROM sponsor_scan")} == {
        SP.SPONSOR_RULES_VERSION}
    assert len(_rows("SELECT * FROM video_sponsors")) == 3


def test_limit_leaves_the_rest_pending():
    _six()
    out = SPN.scan_sponsors(limit=2, batch=2)
    assert out["scanned"] == 2 and out["remaining"] == 4, out
    out = SPN.scan_sponsors(limit=10, batch=2)
    assert out["scanned"] == 4 and out["remaining"] == 0, out


def test_null_and_empty_descriptions_do_not_fail():
    _reset()
    _video("n1", CH_A, None)
    _video("n2", CH_A, "")
    _video("n3", CH_A, "   ")
    out = SPN.scan_sponsors()
    assert out["scanned"] == 3 and out["withSignals"] == 0 and out["remaining"] == 0, out
    assert SPN.scan_sponsors()["scanned"] == 0


def test_hash_matches_the_sql_md5_so_unicode_descriptions_are_not_rescanned():
    _reset()
    _video("u1", CH_A, "Спонсор видео — Ozon. Привет 👋")
    SPN.scan_sponsors()
    assert SPN.scan_sponsors()["scanned"] == 0
    assert {r["brand"] for r in _rows("SELECT brand FROM video_sponsors")} == {"ozon"}


def test_own_channel_brand_is_not_a_sponsor():
    _reset()
    _video("o1", CH_A, "This video is sponsored by Alpha Labs.")
    _video("o2", CH_B, "This video is sponsored by Alpha Labs.")
    SPN.scan_sponsors()
    got = {(r["video_id"], r["brand"]) for r in _rows("SELECT * FROM video_sponsors")}
    assert got == {("o2", "alphalabs")}


# ------------------------------------------------------------ sponsor_map

def _ten():
    _reset()
    # brand A (Brilliant) on two channels, brand B (Raycon) on one, one affiliate
    _video("m1", CH_A, SPONSORED_A, views=3000, days=1)
    _video("m2", CH_B, SPONSORED_A, views=1000, days=2)
    _video("m3", CH_B, SPONSORED_B, views=2000, days=3)
    _video("m4", CH_A, AFFILIATE, views=500, days=4)
    for i in range(5, 11):
        _video(f"m{i}", CH_A, PLAIN, views=100 * i, days=i)
    SPN.scan_sponsors()


def test_sponsor_map_numbers():
    _ten()
    r = SPN.sponsor_map(NICHE)
    assert r["found"] is True and r["niche"] == NICHE and r["period"] == "all"
    assert r["videos"] == 10 and r["videosWithSponsor"] == 3
    assert r["sponsorShare"] == 0.3
    assert r["videosWithAffiliate"] == 1 and r["affiliateShare"] == 0.1
    assert r["scanCoverage"] == 1
    assert r["avgViewsWithSponsor"] == 2000 and r["medianViewsWithSponsor"] == 2000
    # without a sponsor: m4 (500) + m5..m10 (500..1000) -> 7 videos
    without = [500, 500, 600, 700, 800, 900, 1000]
    assert r["avgViewsWithout"] == round(sum(without) / 7, 1)
    assert r["medianViewsWithout"] == 700
    assert "lower bound" in r["note"]
    top = r["topBrands"]
    assert [b["brand"] for b in top] == ["brilliant", "raycon"]
    assert top[0]["videos"] == 2 and top[0]["channels"] == 2 and top[0]["kind"] == "sponsor"
    assert top[0]["lastSeen"] > top[1]["lastSeen"] or top[0]["brand"] == "brilliant"
    ex = top[0]["examples"][0]
    assert set(ex) == {"videoId", "title", "channelId", "evidence"} and ex["videoId"] == "m1"
    assert top[1]["videos"] == 1 and top[1]["channels"] == 1


def test_affiliate_is_listed_apart_from_sponsors():
    _ten()
    r = SPN.sponsor_map(NICHE)
    assert "amazon" not in {b["brand"] for b in r["topBrands"]}
    assert [(b["brand"], b["kind"]) for b in r["affiliateBrands"]] == [("amazon", "affiliate")]


def test_promo_code_counts_as_sponsored_and_merges_into_the_brand():
    _reset()
    _video("p1", CH_A, "Sponsored by NordVPN.\nUse code TOM at nordvpn.com/tom")
    _video("p2", CH_B, "Use code TOM at nordvpn.com/tom")
    SPN.scan_sponsors()
    r = SPN.sponsor_map(NICHE)
    assert r["videosWithSponsor"] == 2
    assert len(r["topBrands"]) == 1 and r["topBrands"][0]["videos"] == 2
    assert r["topBrands"][0]["kind"] == "sponsor"


def test_unknown_niche_is_not_found_with_a_hint():
    _reset()
    r = SPN.sponsor_map("no-such-niche")
    assert r["found"] is False and r["hint"] and "lower bound" in r["note"]


def test_niche_without_scanned_videos_has_zero_shares_and_coverage():
    _reset()
    _video("q1", CH_A, SPONSORED_A)
    _video("q2", CH_A, PLAIN)
    r = SPN.sponsor_map(NICHE)
    assert r["found"] is True and r["videos"] == 2
    assert r["sponsorShare"] == 0 and r["scanCoverage"] == 0
    assert r["avgViewsWithSponsor"] == 0 and r["avgViewsWithout"] == 0
    assert r["topBrands"] == [] and r["affiliateBrands"] == []


def test_partly_scanned_niche_reports_coverage_and_uses_scanned_videos_only():
    _reset()
    _video("h1", CH_A, SPONSORED_A)
    SPN.scan_sponsors()
    _video("h2", CH_A, SPONSORED_A)      # not scanned yet
    r = SPN.sponsor_map(NICHE)
    assert r["videos"] == 2 and r["scanCoverage"] == 0.5
    assert r["videosWithSponsor"] == 1 and r["sponsorShare"] == 1


def test_period_cuts_old_videos():
    _reset()
    _video("d1", CH_A, SPONSORED_A, days=2)
    _video("d2", CH_A, SPONSORED_B, days=200)
    SPN.scan_sponsors()
    assert SPN.sponsor_map(NICHE, period="30d")["videos"] == 1
    r = SPN.sponsor_map(NICHE, period="30d")
    assert [b["brand"] for b in r["topBrands"]] == ["brilliant"]
    assert SPN.sponsor_map(NICHE, period="all")["videos"] == 2
    assert SPN.sponsor_map(NICHE, period="24h")["videos"] == 0


def test_top_n_limits_brands():
    _reset()
    for i, name in enumerate(("Aaa", "Bbb", "Ccc", "Ddd")):
        _video(f"t{i}", CH_A, f"Sponsored by {name}Corp.")
    SPN.scan_sponsors()
    assert len(SPN.sponsor_map(NICHE, top_n=2)["topBrands"]) == 2
    assert len(SPN.sponsor_map(NICHE, top_n=10)["topBrands"]) == 4


def test_empty_niche_has_no_division_by_zero():
    _reset()
    conn = db.get_conn()
    conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES ('ghost', ?)", (NICHE,))
    conn.commit()
    conn.close()
    r = SPN.sponsor_map(NICHE)
    assert r["videos"] == 0 and r["sponsorShare"] == 0 and r["scanCoverage"] == 0


# ------------------------------------------------------------ channel_sponsors

def test_channel_sponsors_covers_only_that_channel():
    _ten()
    r = SPN.channel_sponsors(CH_A)
    assert r["found"] is True and r["channelId"] == CH_A
    assert r["videos"] == 8 and r["videosWithSponsor"] == 1     # m1 only
    assert [b["brand"] for b in r["topBrands"]] == ["brilliant"]
    assert r["topBrands"][0]["channels"] == 1
    assert r["videosWithAffiliate"] == 1
    assert "lower bound" in r["note"]


def test_channel_without_videos_is_not_found():
    _reset()
    r = SPN.channel_sponsors(CH_C)
    assert r["found"] is False and r["hint"]


def test_channel_sponsors_works_for_videos_outside_any_niche():
    _reset()
    _video("x1", CH_C, SPONSORED_B, niche=None)
    SPN.scan_sponsors()
    r = SPN.channel_sponsors(CH_C, period="30d")
    assert r["videosWithSponsor"] == 1 and r["topBrands"][0]["brand"] == "raycon"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

"""Tests for application/language_gaps.py (plan 26) and its HTTP/MCP doors:
outliers in one language, their nearest videos in another, a verdict.
Throwaway schema; no network, zero quota.
Run with pytest, or directly: python3 tests/test_language_gaps.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import language_gaps as G  # noqa: E402

NOW = datetime.now(timezone.utc)
rng = np.random.default_rng(11)


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def _topic():
    return _unit(rng.normal(size=db.EMBEDDING_DIM))


def _near(topic):
    return _unit(topic + 0.15 * rng.normal(size=db.EMBEDDING_DIM) / np.sqrt(db.EMBEDDING_DIM))


OPEN, THIN, COVERED = _topic(), _topic(), _topic()


def setup_module(_=None):
    db.init_db()


def _channel(conn, cid):
    db.upsert_channel(conn, {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None, "description": "",
        "default_language": None, "subscriber_count": 10_000, "video_count": 10,
        "view_count": 1, "thumbnail": None, "published_at": None, "topic_categories": None,
        "keywords": None, "uploads_playlist": None, "hidden_subs": 0})


def _video(conn, vid, cid, views, days_ago, vec, lang, title=None):
    db.upsert_video(conn, {
        "video_id": vid, "channel_id": cid, "title": title or vid, "description": "",
        "published_at": (NOW - timedelta(days=days_ago)).isoformat(), "duration_seconds": 600,
        "view_count": views, "like_count": 0, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": lang, "embedding": vec.tobytes(),
        "updated_at": NOW.isoformat(), "category_id": None, "region": None, "is_short": 0,
        "topic_categories": None, "live_content": None})


def _channel_with(conn, cid, lang, topic, views, n_hits=1, n_base=5):
    """n_base baseline uploads at 1,000 views on unrelated topics, then n_hits on `topic`."""
    _channel(conn, cid)
    for i in range(n_base):
        _video(conn, f"{cid}-b{i}", cid, 1_000, 200 - i * 10, _topic(), lang)
    for j in range(n_hits):
        _video(conn, f"{cid}-hit{j}", cid, views, 60 - j, _near(topic), lang, title=f"{cid} hit {j}")


@pytest.fixture(autouse=True)
def _world():
    conn = db.get_conn()
    for t in ("video_niches", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    # english outliers, one per topic; the open topic has a second english channel that repeated it
    _channel_with(conn, "UCen-open", "en", OPEN, 9_000)
    _channel_with(conn, "UCen-open2", "en-US", OPEN, 4_000)
    _channel_with(conn, "UCen-thin", "en", THIN, 8_000)
    _channel_with(conn, "UCen-cov", "en", COVERED, 7_000)
    # russian: a flop on the thin topic, a hit on the covered one, nothing on the open one
    _channel_with(conn, "UCru-thin", "ru-RU", THIN, 900)
    _channel_with(conn, "UCru-cov", "ru", COVERED, 6_000)
    conn.commit()
    conn.close()


def _by_channel(result):
    return {c["channelId"]: c for c in result["gaps"]}


def test_three_topics_get_three_verdicts_and_sort_open_first():
    r = G.language_gaps("en", "ru")
    got = _by_channel(r)
    assert got["UCen-open"]["verdict"] == "open"
    assert got["UCen-thin"]["verdict"] == "thin"
    assert got["UCen-cov"]["verdict"] == "covered"
    order = [c["verdict"] for c in r["gaps"]]
    assert order == sorted(order, key=["open", "thin", "covered"].index)
    assert r["counts"] == {"open": 2, "thin": 1, "covered": 1}


def test_a_covered_card_names_the_target_language_hit():
    card = _by_channel(G.language_gaps("en", "ru"))["UCen-cov"]
    top = card["matches"][0]
    assert top["channelId"] == "UCru-cov" and top["outlierScore"] >= 2
    assert top["similarity"] >= G.L.MIN_SIMILARITY
    assert top["title"] and top["channelTitle"] == "UCru-cov"


def test_an_open_card_keeps_the_nearest_target_video_for_context():
    card = _by_channel(G.language_gaps("en", "ru"))["UCen-open"]
    assert card["matches"] == [] and card["nearest"]["similarity"] < G.L.MIN_SIMILARITY


def test_demand_counts_other_source_language_channels_that_repeated_it():
    got = _by_channel(G.language_gaps("en", "ru"))
    assert got["UCen-open"]["demand"]["otherChannelsHit"] == 1   # UCen-open2, tagged en-US
    assert got["UCen-thin"]["demand"]["otherChannelsHit"] == 0
    assert got["UCen-open"]["demand"]["score"] > 2


def test_regional_codes_are_the_same_language_both_ways():
    r = G.language_gaps("EN-us", "ru_RU")
    assert r["source"] == "en" and r["target"] == "ru"
    assert {"UCen-open", "UCen-open2"} <= set(_by_channel(r))   # en and en-US are both source


def test_the_source_pairs_flip_and_the_direction_matters():
    r = G.language_gaps("ru", "en", min_outlier=3)
    assert set(_by_channel(r)) == {"UCru-cov"}
    assert r["gaps"][0]["verdict"] == "covered"   # english has the same topic


def test_min_outlier_and_similarity_move_the_verdicts():
    assert G.language_gaps("en", "ru", min_outlier=7.5)["sourceOutliers"] < \
        G.language_gaps("en", "ru", min_outlier=3)["sourceOutliers"]
    r = G.language_gaps("en", "ru", min_similarity=0.999)
    assert r["counts"]["covered"] == 0 and r["counts"]["thin"] == 0


def test_one_channels_string_of_hits_is_capped(monkeypatch):
    monkeypatch.setattr(G, "MAX_PER_CHANNEL", 2)
    conn = db.get_conn()
    _channel_with(conn, "UCen-flood", "en", OPEN, 8_000, n_hits=4, n_base=12)
    conn.commit()
    conn.close()
    r = G.language_gaps("en", "ru", limit=100)
    assert sum(1 for c in r["gaps"] if c["channelId"] == "UCen-flood") == 2
    assert r["outliersFound"] > r["sourceOutliers"]


def test_a_thin_target_corpus_says_so():
    r = G.language_gaps("en", "ru")
    assert r["targetCorpus"]["thin"] is True and r["targetCorpus"]["videos"] == 12
    assert r["targetCorpus"]["channels"] == 2 and "русском языке" in r["targetCorpus"]["hint"]


def test_languages_without_a_language_are_not_counted_and_variants_merge():
    conn = db.get_conn()
    _channel(conn, "UCnone")
    for i, lang in enumerate(("und", "zxx", "", None)):
        _video(conn, f"UCnone-{i}", "UCnone", 100, 30, _topic(), lang)
    conn.commit()
    conn.close()
    langs = {x["code"]: x for x in G.languages()}
    assert set(langs) == {"en", "ru"}
    assert langs["en"]["videos"] == 24 and langs["en"]["channels"] == 4   # en and en-US merged
    assert [x["code"] for x in G.languages()] == ["en", "ru"]              # biggest first


def test_a_video_without_a_language_is_never_a_source():
    conn = db.get_conn()
    _channel_with(conn, "UCmystery", "und", OPEN, 9_000)
    conn.commit()
    conn.close()
    assert "UCmystery" not in _by_channel(G.language_gaps("en", "ru"))


def test_two_equal_or_unknown_languages_are_refused():
    for bad in (("en", "en"), ("en", "en-US"), ("en", "und"), ("", "ru"), ("xx1", "ru")):
        with pytest.raises(ValueError):
            G.language_gaps(*bad)


def test_the_niche_narrows_the_source_outliers_not_the_target():
    conn = db.get_conn()
    conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?, ?)",
                 ("UCen-cov-hit0", "only-this"))
    conn.commit()
    conn.close()
    r = G.language_gaps("en", "ru", niche="only-this")
    assert set(_by_channel(r)) == {"UCen-cov"}
    assert r["gaps"][0]["matches"], "the target language is searched beyond the niche"


def test_without_pgvector_the_same_verdicts_come_out(monkeypatch):
    with_index = {k: v["verdict"] for k, v in _by_channel(G.language_gaps("en", "ru")).items()}
    monkeypatch.setattr(db, "pgvector_available", lambda: False)
    without = {k: v["verdict"] for k, v in _by_channel(G.language_gaps("en", "ru")).items()}
    assert without == with_index and len(without) == 4


def test_http_and_mcp_doors():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    c = TestClient(api.app)
    r = c.get("/api/language-gaps", params={"source": "en", "target": "ru"})
    assert r.status_code == 200 and r.json()["counts"]["open"] == 2
    assert c.get("/api/language-gaps", params={"source": "en", "target": "en"}).status_code == 400
    assert [x["code"] for x in c.get("/api/language-gaps/languages").json()["languages"]] == ["en", "ru"]
    assert srv.language_gaps("en", "ru")["counts"]["covered"] == 1
    assert "error" in srv.language_gaps("en", "en")


def test_one_video_gets_the_same_verdict_as_in_the_list():
    for ch, verdict in (("UCen-open", "open"), ("UCen-thin", "thin"), ("UCen-cov", "covered")):
        r = G.video_language_gap(f"{ch}-hit0", "ru")
        assert r["verdict"] == verdict and r["source"] == "en" and r["target"] == "ru", (ch, r)
    assert G.video_language_gap("UCen-cov-hit0", "ru")["matches"][0]["channelId"] == "UCru-cov"


def test_one_video_without_a_target_picks_the_biggest_other_language():
    r = G.video_language_gap("UCen-cov-hit0")
    assert r["target"] == "ru" and r["targetCorpus"]["videos"] == 12


def test_a_baseline_video_that_is_no_outlier_still_gets_a_verdict():
    r = G.video_language_gap("UCen-cov-b0", "ru")
    assert r["verdict"] in ("open", "thin", "covered") and r["outlierScore"] <= 2


def test_one_video_edge_cases_say_why():
    conn = db.get_conn()
    _channel(conn, "UCx")
    _video(conn, "UCx-und", "UCx", 100, 30, _topic(), "und")
    _video(conn, "UCx-en", "UCx", 100, 30, _topic(), "en")
    conn.execute("UPDATE videos SET embedding = NULL, embedding_v = NULL WHERE video_id = 'UCx-en'")
    conn.commit()
    conn.close()
    assert G.video_language_gap("UCx-und", "ru")["reason"] == "no-language"
    assert G.video_language_gap("UCx-en", "ru")["reason"] == "no-embedding"
    with pytest.raises(ValueError):
        G.video_language_gap("nope", "ru")
    with pytest.raises(ValueError):
        G.video_language_gap("UCen-cov-hit0", "en")
    with pytest.raises(ValueError):
        G.video_language_gap("UCen-cov-hit0", "und")


def test_one_video_http_door():
    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    c = TestClient(api.app)
    r = c.get("/api/videos/UCen-cov-hit0/language-gap", params={"target": "ru"})
    assert r.status_code == 200 and r.json()["verdict"] == "covered"
    assert c.get("/api/videos/nope/language-gap").status_code == 400

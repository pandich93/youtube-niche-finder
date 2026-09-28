"""Tests for application/template_risk.py (plan 01): the per-channel and
per-niche template-risk read over the local database. Same throwaway-schema
setup as test_packaging.py (tests/schema_scope.py); embeddings are synthetic
vectors, no model, no network, no quota.
Run with pytest, or directly: python3 tests/test_template_risk.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Выставляет NICHE_DB_SCHEMA (своя одноразовая схема на процесс) и вешает её
# удаление на atexit -- импорт нужен именно ради этого побочного эффекта.
import schema_scope  # noqa: F401,E402

import numpy as np  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import template_risk as TRK  # noqa: E402

NOW = datetime.now(timezone.utc)
RNG = np.random.default_rng(7)
DIM = 384
CONVEYOR, HUMAN, TINY, MIXED, EMPTY = (
    "UC" + n.ljust(22, "0") for n in ("trconveyor", "trhuman", "trtiny", "trmixed", "trempty"))


def setup_module(_=None):
    db.init_db()


def _blob(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def _same_vec():
    base = RNG.normal(size=DIM)
    return lambda: base + RNG.normal(scale=0.05, size=DIM)


def _video(vid, cid, title, days_ago, duration, vec):
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO videos (video_id, channel_id, title, published_at, duration_seconds, "
        "is_short, embedding) VALUES (?,?,?,?,?,?,?)",
        (vid, cid, title, (NOW - timedelta(days=days_ago)).isoformat(), duration,
         1 if duration <= 60 else 0, _blob(vec)))
    conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?) "
                 "ON CONFLICT DO NOTHING", (vid, "tr-niche"))
    conn.commit()
    conn.close()


def _seed():
    conn = db.get_conn()
    for t in ("video_niches", "videos", "channels", "niches"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO niches (slug, query, label) VALUES ('tr-niche','q','TR')")
    for cid, title, subs in ((CONVEYOR, "Conveyor Beats", 5000), (HUMAN, "Human Vlogs", 90000),
                             (TINY, "Tiny New", 10), (MIXED, "Mixed Shorts", 300),
                             (EMPTY, "No Videos", 1)):
        conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                     (cid, title, subs))
    conn.commit()
    conn.close()

    near = _same_vec()
    for i in range(14):                                   # conveyor: same look, length, rhythm
        _video(f"cv{i}", CONVEYOR, f"Track {i} | Afro House 2026", i + 1, 240, near())
    for i in range(14):                                   # human: everything varies
        _video(f"hm{i}", HUMAN, ["How I quit", "The war", "Why bridges fall", "A tour",
                                 "Cooking", "Lost media", "My setup"][i % 7] + f" part {i * 7}",
               [1, 3, 4, 9, 12, 20, 21, 30, 33, 41, 50, 52, 60, 75][i],
               [240, 1300, 610, 2200, 95, 900, 3100, 480, 700, 1500, 300, 1800, 260, 990][i],
               RNG.normal(size=DIM))
    for i in range(4):                                    # too few videos for a score
        _video(f"ti{i}", TINY, f"Tiny {i}", i + 1, 300, RNG.normal(size=DIM))
    short_near = _same_vec()
    for i in range(12):                                   # long-form varied ...
        _video(f"mx{i}", MIXED, f"Essay number {i * 13}", i * 5 + 1, 600 + i * 260,
               RNG.normal(size=DIM))
    for i in range(15):                                   # ... plus identical Shorts
        _video(f"ms{i}", MIXED, f"Clip {i} | #shorts", i * 2 + 1, 30, short_near())


def test_conveyor_channel_is_high_risk_with_reasons():
    _seed()
    r = TRK.template_risk(CONVEYOR)
    assert r["found"] is True and r["level"] == "high" and r["score"] >= 70
    assert r["videosAnalysed"] == 14 and r["format"] == "long-form"
    assert {x["signal"] for x in r["reasons"]} >= {"similarity", "templateShare"}
    assert r["signals"]["similarity"] > 0.9
    assert "not YouTube's verdict" in r["note"]


def test_varied_channel_is_low_risk():
    _seed()
    r = TRK.template_risk(HUMAN)
    assert r["level"] == "low" and r["reasons"] == []


def test_few_videos_gives_insufficient_data_not_a_score():
    _seed()
    r = TRK.template_risk(TINY)
    assert r["level"] == "insufficient-data" and r["score"] is None
    assert r["videosAnalysed"] == 4


def test_shorts_and_long_form_are_not_mixed():
    # Identical Shorts must not drag a varied long-form channel into high risk.
    _seed()
    r = TRK.template_risk(MIXED)
    assert r["format"] == "long-form" and r["videosAnalysed"] == 12
    assert r["level"] == "low"


def test_unknown_channel_and_channel_without_videos():
    _seed()
    assert TRK.template_risk("UCdoesnotexist000000000")["found"] is False
    r = TRK.template_risk(EMPTY)
    assert r["found"] is True and r["level"] == "insufficient-data" and r["videosAnalysed"] == 0


def test_last_n_limits_how_far_back_it_looks():
    _seed()
    assert TRK.template_risk(CONVEYOR, last_n=10)["videosAnalysed"] == 10


def test_niche_summary_counts_levels_and_lists_the_most_templated():
    _seed()
    n = TRK.niche_template_risk("tr-niche")
    assert n["niche"] == "tr-niche" and n["found"] is True
    assert n["channelsAnalysed"] == 3                  # conveyor, human, mixed
    assert n["channelsInsufficient"] == 1              # tiny
    assert n["levels"] == {"low": 2, "medium": 0, "high": 1}
    assert n["highRiskSharePercent"] == 33.3
    top = n["mostTemplated"][0]
    assert top["channelId"] == CONVEYOR and top["level"] == "high"
    assert top["subscribers"] == 5000
    assert [c["score"] for c in n["mostTemplated"]] == sorted(
        [c["score"] for c in n["mostTemplated"]], reverse=True)


def test_niche_without_videos_or_unknown_is_a_hint_not_an_error():
    _seed()
    n = TRK.niche_template_risk("no-such-niche")
    assert n["found"] is False and "hint" in n


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

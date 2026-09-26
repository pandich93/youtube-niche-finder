"""Tests for application/metadata_review.py -- the SEO-assistant orchestration
(plan item 8.8). sqlite double for infrastructure.postgres (real discovery.py
SQL runs against it unmodified -- the "?" placeholders are sqlite-native), a
fake embeddings provider (no fastembed/torch download in CI), no YouTube key.

Run: python3 tests/test_metadata_review.py (or pytest tests/test_metadata_review.py)
"""
import json
import os
import sys
import sqlite3
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from module_doubles import ModuleDoubles  # noqa: E402

NOW = datetime.now(timezone.utc)
iso = lambda dt: dt.isoformat()

SCHEMA = """
CREATE TABLE channels (
  channel_id TEXT PRIMARY KEY, title TEXT, custom_url TEXT, country TEXT,
  subscriber_count INTEGER, video_count INTEGER, view_count INTEGER
);
CREATE TABLE videos (
  video_id TEXT PRIMARY KEY, channel_id TEXT, title TEXT, tags TEXT,
  published_at TEXT, first_seen_at TEXT, view_count INTEGER, like_count INTEGER,
  comment_count INTEGER, duration_seconds INTEGER, thumbnail TEXT,
  category_id TEXT, region TEXT, default_language TEXT, is_short INTEGER,
  contains_synthetic_media INTEGER, embedding BLOB, updated_at TEXT
);
CREATE TABLE video_niches (video_id TEXT, niche_slug TEXT, PRIMARY KEY(video_id, niche_slug));
CREATE TABLE video_stats_history (video_id TEXT, captured_at TEXT, view_count INTEGER);
CREATE TABLE drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, title TEXT, description TEXT,
  tags TEXT, niche TEXT, channel_id TEXT, is_short INTEGER, review TEXT,
  created_at TEXT, published_at TEXT
);
"""

RAW = None
NICHE = "ai-explainers"
CH = "UCtestchannel00000001"


class _Conn:
    def execute(self, sql, params=()):
        return RAW.execute(sql, params)

    def commit(self):
        RAW.commit()

    def close(self):
        pass


def reset():
    global RAW
    RAW = sqlite3.connect(":memory:")
    RAW.row_factory = sqlite3.Row
    RAW.executescript(SCHEMA)


reset()

# Real `infrastructure` / `infrastructure.embeddings` packages import fine on
# their own (empty __init__.py, no heavy deps at import time) -- only their
# leaf submodules get replaced, so discovery.py's *real* SQL and
# infrastructure.youtube.client (needs only `requests`, already a dependency)
# still run unmodified against this sqlite double.
fake_db = types.ModuleType("infrastructure.postgres")
fake_db.get_conn = lambda: _Conn()
fake_db.now_iso = lambda: iso(datetime.now(timezone.utc))
fake_db.pgvector_available = lambda: False  # stage 06: this sqlite double has no vector column

fake_emb = types.ModuleType("infrastructure.embeddings.fastembed_provider")
fake_emb.embed = lambda text: [1.0, 0.0, 0.0]
fake_emb.from_blob = lambda blob: [1.0, 0.0, 0.0]
fake_emb.cosine = lambda a, b: 1.0
fake_emb_pkg = types.ModuleType("infrastructure.embeddings")
fake_emb_pkg.fastembed_provider = fake_emb

DOUBLES = ModuleDoubles({
    "infrastructure.postgres": fake_db,
    "infrastructure.embeddings": fake_emb_pkg,
    "infrastructure.embeddings.fastembed_provider": fake_emb,
})
MR, = DOUBLES.load("application.metadata_review")


def seed_channel(ch=CH, subs=50_000, videos=80, views=40_000_000):
    RAW.execute(
        "INSERT OR REPLACE INTO channels (channel_id, title, custom_url, subscriber_count,"
        " video_count, view_count) VALUES (?,?,?,?,?,?)",
        (ch, "AI Explainers", "@aiexplainers", subs, videos, views))


def seed_corpus(n_outliers=25, n_normal=25, niche=NICHE, ch=CH):
    """Outliers get a digit in the title and 40x the views of normal videos --
    both structural_lift and title-length signals need real separation to
    read as reliable, not just non-crashing.

    Each video lives on its OWN channel (all with identical channel-level
    totals: 40M views / 80 videos, matching seed_channel's defaults) rather
    than sharing one channel's upload history. discovery.py's real outlier
    score prefers a per-channel rolling-median baseline (`outlierScoreAgeAdjusted`
    / `outlierScore`) over the flat channel-average fallback (`outlierScoreNexlev`)
    -- with 50 videos crammed onto one channel that rolling baseline gets
    contaminated by whichever outliers/normals happened to upload most
    recently, which is a real property of that metric, not something this
    fixture should fight. One channel per video keeps every video's baseline
    at "no prior upload" (None), so the deterministic channel-average score
    decides -- exactly the same number the real per-channel-baseline score
    converges to once a channel has enough of its own history to no longer
    need the fallback. `ch` (the CH constant) becomes outlier0000's channel,
    so tests that filter or seed by CH still land on a real, niche-tagged
    video.
    """
    for i in range(n_outliers):
        vid = f"outlier{i:04d}"
        vch = ch if i == 0 else f"UCoutlierch{i:04d}00000"
        title = f"{i+1} способов применить нейросети в 2026"
        if i != 0:
            RAW.execute(
                "INSERT OR REPLACE INTO channels (channel_id, title, custom_url,"
                " subscriber_count, video_count, view_count) VALUES (?,?,?,?,?,?)",
                (vch, f"Outlier Channel {i}", f"@outlierch{i}", 50_000, 80, 40_000_000))
        RAW.execute(
            "INSERT INTO videos (video_id, channel_id, title, tags, published_at,"
            " view_count, duration_seconds, is_short) VALUES (?,?,?,?,?,?,?,0)",
            (vid, vch, title, "[]", iso(NOW - timedelta(days=10 + i)), 2_000_000, 600))
        RAW.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (vid, niche))
    for i in range(n_normal):
        vid = f"normal{i:04d}"
        vch = f"UCnormalch{i:04d}00000"
        title = "Обычное видео про нейросети без изюминки"
        RAW.execute(
            "INSERT OR REPLACE INTO channels (channel_id, title, custom_url,"
            " subscriber_count, video_count, view_count) VALUES (?,?,?,?,?,?)",
            (vch, f"Normal Channel {i}", f"@normalch{i}", 50_000, 80, 40_000_000))
        RAW.execute(
            "INSERT INTO videos (video_id, channel_id, title, tags, published_at,"
            " view_count, duration_seconds, is_short) VALUES (?,?,?,?,?,?,?,0)",
            (vid, vch, title, "[]", iso(NOW - timedelta(days=10 + i)), 50_000, 600))
        RAW.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (vid, niche))
    RAW.commit()


def test_no_niche_or_channel_is_all_unreliable_by_design():
    reset()
    res = MR.review_metadata("Заголовок без контекста")
    assert res["summary"]["total"] == 0
    assert "hint" in res


def test_review_computes_signals_against_niche_corpus():
    reset(); seed_channel(); seed_corpus()
    res = MR.review_metadata("5 секретов нейросетей, о которых молчат",
                             description="00:00 вступление " + "текст " * 40,
                             tags=["нейросети", "ai"], niche=NICHE)
    assert res["sample"]["videosAnalysed"] == 50
    assert res["sample"]["outliersInSample"] == 25
    # digit-in-title structural signal must have resolved (25 with, 25 without -- both >= min sample)
    digit = next(f for f in res["structuralPatterns"] if f["feature"] == "has_digit")
    assert digit["verdict"] == "ok"
    assert digit["lift"] > 1.5, digit
    assert res["summary"]["total"] == len(res["signals"])


def test_title_length_signal_uses_outlier_baseline_only():
    reset(); seed_channel(); seed_corpus()
    res = MR.review_metadata("5 секретов нейросетей, о которых молчат", niche=NICHE)
    title_sig = next(s for s in res["signals"] if s["id"] == "title_length")
    # baseline sample must equal the OUTLIER subset (25), not the whole corpus (50)
    assert title_sig["baseline"]["sample"] == 25


def test_near_duplicates_flagged_within_niche():
    reset(); seed_channel(); seed_corpus()
    # give every video in the niche a (fake, identical) embedding so cosine=1.0 fires
    RAW.execute("UPDATE videos SET embedding = X'00' WHERE video_id IN "
                "(SELECT video_id FROM video_niches WHERE niche_slug=?)", (NICHE,))
    RAW.commit()
    res = MR.review_metadata("Новый ролик про нейросети", niche=NICHE)
    assert res["nearDuplicates"]["checked"] == 50
    assert len(res["nearDuplicates"]["near"]) > 0


def test_draft_save_list_and_link_roundtrip():
    reset(); seed_channel(); seed_corpus()
    review = MR.review_metadata("Черновик заголовка", niche=NICHE)
    saved = MR.save_draft("Черновик заголовка", description="", tags=["ai"],
                          niche=NICHE, channel_id=CH, review=review)
    assert saved["id"]
    drafts = MR.list_drafts(channel_id=CH)
    assert len(drafts) == 1 and drafts[0]["title"] == "Черновик заголовка"
    assert drafts[0]["videoId"] is None

    MR.link_draft(saved["id"], "outlier0000")  # pretend it got published as this video
    drafts = MR.list_drafts(channel_id=CH)
    assert drafts[0]["videoId"] == "outlier0000"


def test_draft_outcomes_only_surfaces_old_enough_links():
    reset(); seed_channel(); seed_corpus()
    review = MR.review_metadata("Черновик", niche=NICHE)
    saved = MR.save_draft("Черновик", niche=NICHE, channel_id=CH, review=review)
    # outlier0000 was seeded 10 days ago -- old enough for the default 7-day check
    MR.link_draft(saved["id"], "outlier0000")
    outcomes = MR.draft_outcomes(min_age_days=7.0)
    assert len(outcomes) == 1
    assert outcomes[0]["videoId"] == "outlier0000"
    assert outcomes[0]["views"] == 2_000_000

    # a link to a just-published video (0 days old) must NOT show up yet
    RAW.execute("UPDATE videos SET published_at=? WHERE video_id='outlier0001'", (iso(NOW),))
    saved2 = MR.save_draft("Другой черновик", niche=NICHE, channel_id=CH)
    MR.link_draft(saved2["id"], "outlier0001")
    outcomes2 = MR.draft_outcomes(min_age_days=7.0)
    assert len(outcomes2) == 1  # still only the old one


def _run_all():
    ns = dict(globals())
    tests = [(name, fn) for name, fn in ns.items() if name.startswith("test_")]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # pragma: no cover
            print(f"ERROR {name}: {e!r}")
    print(f"\n{passed}/{len(tests)} прошло")
    return passed == len(tests)


if __name__ == "__main__":
    with DOUBLES.active():
        ok = _run_all()
    sys.exit(0 if ok else 1)

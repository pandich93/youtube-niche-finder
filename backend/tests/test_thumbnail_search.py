"""Tests for application/thumbnail_search.py (plan 13): embedding thumbnails,
finding visually similar ones, searching them by text and grouping a niche's
thumbnails into styles. Throwaway schema; the image CDN and the CLIP models
are monkeypatched -- no network, no model download.
Run with pytest, or directly: python3 tests/test_thumbnail_search.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.thumbnails as TH  # noqa: E402
from application import thumbnail_search as TS  # noqa: E402
from infrastructure.embeddings import image_provider as IP  # noqa: E402

NOW = datetime.now(timezone.utc)
NICHE = "thumbs"
CH_A, CH_B = "UC" + "thumbsa".ljust(22, "0"), "UC" + "thumbsb".ljust(22, "0")
RED = [f"red{i}" for i in range(8)]
BLUE = [f"blu{i}" for i in range(8)]


def _vec(axis, wobble=0.0):
    v = np.zeros(IP.DIM, dtype=np.float32)
    v[axis] = 1.0
    v[2] = wobble
    return v / np.linalg.norm(v)


def fake_embed_images(images):
    out = []
    for raw in images:
        if not raw or raw == b"broken":
            out.append(None)
            continue
        vid = raw.decode().split(":")[1]
        out.append(_vec(0 if vid.startswith("red") else 1, wobble=int(vid[-1]) / 50))
    return out


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    conn = db.get_conn()
    for t in ("thumbnail_archive", "video_changes", "video_niches", "videos", "channels", "niches"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", (NICHE, "q", "Q"))
    for cid in (CH_A, CH_B):
        conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                     (cid, cid[-4:], 5000))
    for i, vid in enumerate(RED + BLUE):
        conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count, "
                     "duration_seconds, is_short) VALUES (?,?,?,?,?,?,?)",
                     (vid, CH_A if i % 2 else CH_B, vid, (NOW - timedelta(days=5 + i)).isoformat(),
                      1000 * (i + 1), 600, 0))
        conn.execute("INSERT INTO video_niches (video_id, niche_slug) VALUES (?,?)", (vid, NICHE))
    conn.commit()
    conn.close()
    monkeypatch.setattr(TH, "fetch_thumbnail", lambda vid: f"img:{vid}".encode())
    monkeypatch.setattr(IP, "embed_images", fake_embed_images)
    monkeypatch.setattr(IP, "embed_text", lambda text: _vec(0 if "red" in text else 1))


def _embedded():
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM videos WHERE thumb_embedding IS NOT NULL").fetchone()[0]
    conn.close()
    return n


# ------------------------------------------------------------ embedding

def test_embed_thumbnails_embeds_what_is_missing_and_then_nothing():
    out = TS.embed_thumbnails(limit=100, pause_seconds=0)
    assert out["embedded"] == 16 and out["failed"] == 0
    assert _embedded() == 16
    again = TS.embed_thumbnails(limit=100, pause_seconds=0)
    assert again["embedded"] == 0 and again["candidates"] == 0


def test_embed_thumbnails_can_take_one_niche_only():
    conn = db.get_conn()
    conn.execute("INSERT INTO niches (slug, query, label) VALUES (?,?,?)", ("other", "o", "O"))
    conn.execute("UPDATE video_niches SET niche_slug = 'other' WHERE video_id LIKE 'blu%'")
    conn.commit()
    conn.close()
    out = TS.embed_thumbnails(limit=100, niche=NICHE, pause_seconds=0)
    assert out["embedded"] == 8 and _embedded() == 8


def test_broken_or_missing_images_are_counted_not_raised(monkeypatch):
    monkeypatch.setattr(TH, "fetch_thumbnail",
                        lambda vid: None if vid == "red0" else b"broken" if vid == "red1"
                        else f"img:{vid}".encode())
    out = TS.embed_thumbnails(limit=100, pause_seconds=0)
    assert out["embedded"] == 14 and out["failed"] == 2


def test_a_thumbnail_swap_after_embedding_re_embeds_it():
    TS.embed_thumbnails(limit=100, pause_seconds=0)
    conn = db.get_conn()
    conn.execute("INSERT INTO video_changes (video_id, changed_at, field, old_value, new_value) "
                 "VALUES (?,?,?,?,?)", ("red3", (NOW + timedelta(minutes=5)).isoformat(),
                                        "thumbnail_image", "a", "b"))
    conn.commit()
    conn.close()
    out = TS.embed_thumbnails(limit=100, pause_seconds=0)
    assert out["embedded"] == 1


def test_an_archived_image_is_used_instead_of_downloading(monkeypatch):
    conn = db.get_conn()
    conn.execute("INSERT INTO thumbnail_archive (video_id, captured_at, dhash, image) "
                 "VALUES (?,?,?,?)", ("red0", db.now_iso(), "00", b"img:red0"))
    conn.commit()
    conn.close()
    fetched = []
    monkeypatch.setattr(TH, "fetch_thumbnail", lambda vid: fetched.append(vid) or f"img:{vid}".encode())
    TS.embed_thumbnails(limit=100, pause_seconds=0)
    assert "red0" not in fetched and len(fetched) == 15


# ------------------------------------------------------------ search

@pytest.mark.parametrize("pgvector", [True, False])
def test_similar_thumbnails_ranks_the_same_style_first(monkeypatch, pgvector):
    TS.embed_thumbnails(limit=100, pause_seconds=0)
    if not pgvector:
        monkeypatch.setattr(db, "pgvector_available", lambda: False)
    out = TS.similar_thumbnails("red0", limit=7)
    ids = [s["videoId"] for s in out["similar"]]
    assert "red0" not in ids
    assert set(ids) == set(RED[1:])
    assert out["similar"][0]["similarity"] >= out["similar"][-1]["similarity"]
    assert out["similar"][0]["thumbnail"] is None or "ytimg" in out["similar"][0]["thumbnail"]


def test_similar_thumbnails_can_skip_the_same_channel():
    TS.embed_thumbnails(limit=100, pause_seconds=0)
    out = TS.similar_thumbnails("red0", limit=20, exclude_same_channel=True)
    assert all(s["channelId"] != CH_B for s in out["similar"])


def test_a_video_without_a_thumbnail_vector_gets_a_hint():
    out = TS.similar_thumbnails("red0")
    assert out["similar"] == [] and "embed" in out["hint"]


def test_search_thumbnails_by_text():
    TS.embed_thumbnails(limit=100, pause_seconds=0)
    out = TS.search_thumbnails("red arrow", niche=NICHE, limit=8)
    assert {r["videoId"] for r in out["results"]} == set(RED)


def test_search_needs_a_query():
    with pytest.raises(ValueError):
        TS.search_thumbnails("  ")


# ------------------------------------------------------------ styles

def test_thumbnail_styles_find_the_two_looks():
    TS.embed_thumbnails(limit=100, pause_seconds=0)
    out = TS.thumbnail_styles(NICHE)
    assert out["found"] is True and out["embedded"] == 16
    groups = [sorted(e["videoId"][:3] for e in s["examples"]) for s in out["styles"]]
    assert len(out["styles"]) >= 2
    assert all(len(set(g)) == 1 for g in groups)          # a style never mixes red and blue
    assert sum(s["videos"] for s in out["styles"]) == 16
    assert all("medianOutlierScore" in s for s in out["styles"])


def test_thumbnail_styles_with_few_vectors_says_so():
    out = TS.thumbnail_styles(NICHE)
    assert out["found"] is False and "embed" in out["hint"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

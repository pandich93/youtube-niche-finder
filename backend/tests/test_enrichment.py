"""Tests for application/enrichment.py (stage 03 background AI labeling) and
the worker's LLM_PROVIDER=none skip. Same throwaway-schema-against-real-
Postgres setup as test_tags.py; the LLM provider itself is monkeypatched via
infrastructure.llm.factory.get_provider, same pattern as test_llm_gateway.py
-- no network, no real LLM_PROVIDER env needed.
Run with pytest, or directly: python3 tests/test_enrichment.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db               # noqa: E402
from application import enrichment as EN            # noqa: E402
from application import tags as T                   # noqa: E402
from application import worker_cycle                # noqa: E402
from infrastructure.llm import factory               # noqa: E402
from infrastructure.llm.base import LLMResult        # noqa: E402
from infrastructure.llm.null import NullProvider     # noqa: E402


def setup_module(_=None):
    db.init_db()


class _StubProvider:
    """Returns `results` in order, one per call; None once exhausted (mimics
    the gateway's own "budget exhausted" contract)."""
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def complete_json(self, system, user, schema, *, model=None, max_tokens=1024):
        self.calls += 1
        if not self.results:
            return None
        return self.results.pop(0)


def _channel(cid, first_seen_at="2026-01-01T00:00:00Z", description="a channel"):
    return {
        "channel_id": cid, "title": cid, "custom_url": None, "country": None,
        "description": description, "default_language": None, "subscriber_count": 1000,
        "video_count": 1, "view_count": 1000, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0, "first_seen_at": first_seen_at,
    }


def _video(vid, cid, title="video", published_at="2026-01-01T00:00:00Z"):
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": published_at, "duration_seconds": 300,
        "view_count": 100, "like_count": 1, "comment_count": 0, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": published_at, "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def _clear_llm_tables():
    conn = db.get_conn()
    conn.execute("DELETE FROM llm_cache")
    conn.execute("DELETE FROM llm_usage")
    conn.execute("DELETE FROM meta WHERE key='llm_budget_blocked_until'")
    conn.commit()
    conn.close()


_CHANNEL_LABEL = {
    "is_faceless": True, "faceless_confidence": 0.9, "content_format": "voiceover_stock",
    "ai_voice_likely": True, "topic": "history", "language": "en",
    "made_for_kids_likely": False, "reason": "stock footage + narration",
}


# --------------------------------------------------------- channel candidates

def test_channel_candidates_prioritizes_tracked_over_untracked():
    conn = db.get_conn()
    db.upsert_channel(conn, _channel("UCenr0000000000000000001", "2026-01-01T00:00:00Z"))
    db.upsert_channel(conn, _channel("UCenr0000000000000000002", "2026-06-01T00:00:00Z"))
    db.track_channel(conn, "UCenr0000000000000000001")
    conn.commit()
    conn.close()

    conn = db.get_conn()
    rows = EN._channel_candidates(conn, limit=50)
    conn.close()

    ids = [r["channel_id"] for r in rows]
    assert ids.index("UCenr0000000000000000001") < ids.index("UCenr0000000000000000002")


def test_channel_candidates_excludes_recently_labeled_channels():
    conn = db.get_conn()
    db.upsert_channel(conn, _channel("UCenr0000000000000000003"))
    db.record_channel_llm_labels(conn, "UCenr0000000000000000003", _CHANNEL_LABEL, "m/x")
    conn.commit()
    conn.close()

    conn = db.get_conn()
    rows = EN._channel_candidates(conn, limit=1000)
    conn.close()

    assert "UCenr0000000000000000003" not in [r["channel_id"] for r in rows]


def test_build_channel_input_reports_titles_and_shorts_share():
    row = {"title": "My Channel", "channel_id": "UCx", "description": "d" * 700}
    videos = [
        {"title": "t1", "tags": "a,b", "duration_seconds": 60, "is_short": 1},
        {"title": "t2", "tags": "a", "duration_seconds": 600, "is_short": 0},
    ]
    text = EN._build_channel_input(row, videos)
    assert "t1" in text and "t2" in text
    assert "Shorts share: 0.5" in text
    assert len(text.split("Description: ")[1].split("\n")[0]) <= 600


# --------------------------------------------------------- classify_channels

def test_classify_channels_writes_labels_and_records_model(monkeypatch):
    # limit=1 with other tracked-but-unlabeled channels already sitting in
    # the shared test schema (is_tracked ranks first, see the candidates
    # test above) means *some* channel gets classified, not necessarily
    # this one -- so this looks the write up by the stub's model name
    # instead of by channel_id.
    _clear_llm_tables()
    conn = db.get_conn()
    db.upsert_channel(conn, _channel("UCenr0000000000000000010"))
    conn.commit()
    conn.close()

    stub = _StubProvider([LLMResult(data=_CHANNEL_LABEL, model="m/classify-unique",
                                    prompt_tokens=1, completion_tokens=1, cost_usd=0.001)])
    monkeypatch.setattr(factory, "get_provider", lambda: stub)

    result = EN.classify_channels(limit=1)

    assert result["classified"] >= 1
    assert result["budgetExhausted"] is False

    # llm_model records factory.default_model() (the gateway's own choice),
    # not LLMResult.model from a free-fallback answer -- see the module
    # docstring's "known gap" note -- so the write is looked up by the
    # unique `reason` text baked into _CHANNEL_LABEL instead.
    conn = db.get_conn()
    row = conn.execute("SELECT llm_labels, llm_labeled_at, llm_model FROM channels "
                       "WHERE llm_labels->>'reason' = ?",
                       (_CHANNEL_LABEL["reason"],)).fetchone()
    conn.close()
    assert row["llm_labels"]["is_faceless"] is True
    assert row["llm_labeled_at"] is not None
    assert row["llm_model"] is not None


def test_classify_channels_stops_when_gateway_returns_none(monkeypatch):
    _clear_llm_tables()
    conn = db.get_conn()
    db.upsert_channel(conn, _channel("UCenr0000000000000000011"))
    db.upsert_channel(conn, _channel("UCenr0000000000000000012"))
    conn.commit()
    conn.close()

    stub = _StubProvider([])  # returns None on the very first call
    monkeypatch.setattr(factory, "get_provider", lambda: stub)

    result = EN.classify_channels(limit=50)

    assert result["budgetExhausted"] is True


def test_classify_channels_with_null_provider_writes_nothing(monkeypatch):
    _clear_llm_tables()
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    conn = db.get_conn()
    db.upsert_channel(conn, _channel("UCenr0000000000000000013"))
    conn.commit()
    conn.close()

    result = EN.classify_channels(limit=10)

    assert result["classified"] == 0
    assert result["budgetExhausted"] is True


# --------------------------------------------------------- tag_new_videos

def _niche_channel_id(niche_slug):
    return f"UC-{niche_slug}-chan"


def _seed_niche_with_manual_tags(niche_slug, n_manual, tag_group="theme"):
    conn = db.get_conn()
    channel_id = _niche_channel_id(niche_slug)
    db.upsert_channel(conn, _channel(channel_id))
    db.upsert_niche(conn, niche_slug, niche_slug, niche_slug)
    for i in range(n_manual):
        vid = f"v{niche_slug}m{i}"
        db.upsert_video(conn, _video(vid, channel_id, title=f"manual {i}"))
        db.link_video_niche(conn, vid, niche_slug)
        db.upsert_video_tag(conn, vid, tag_group, "fear" if i % 2 else "howto", "manual")
    conn.commit()
    conn.close()


def test_tag_new_videos_skips_niches_below_the_manual_tag_threshold():
    # No stub is installed and the Null provider spends nothing, so a niche
    # that does clear the threshold elsewhere in the suite still can't write
    # a tag here -- this assertion only needs skippedNiches to be honest.
    _clear_llm_tables()
    _seed_niche_with_manual_tags("n-enr-small", n_manual=3)

    result = EN.tag_new_videos(limit=50)

    skipped = {(s["niche"], s["tagGroup"]) for s in result["skippedNiches"]}
    assert ("n-enr-small", "theme") in skipped
    tags = T.list_video_tags(niche="n-enr-small")
    assert all(t["source"] != "llm" for t in tags)


def test_tag_new_videos_tags_untagged_videos_in_a_trained_niche(monkeypatch):
    _clear_llm_tables()
    _seed_niche_with_manual_tags("n-enr-big", n_manual=EN.MIN_MANUAL_TAGS_FOR_NICHE)
    conn = db.get_conn()
    db.upsert_video(conn, _video("v-enr-untagged", _niche_channel_id("n-enr-big"),
                                 title="untagged video"))
    db.link_video_niche(conn, "v-enr-untagged", "n-enr-big")
    conn.commit()
    conn.close()

    # Repeats the same answer for every call -- other (niche, tag_group)
    # pairs that happen to also be "ready" elsewhere in the shared test
    # schema must not starve this one of a response.
    tagged_result = LLMResult(data={"tags": [{"tag": "howto", "proposed": False}]},
                              model="m/tag", prompt_tokens=1, completion_tokens=1,
                              cost_usd=0.001)
    stub = _StubProvider([tagged_result] * 200)
    monkeypatch.setattr(factory, "get_provider", lambda: stub)

    EN.tag_new_videos(limit=200)

    tags = {t["tag"]: t["source"] for t in T.list_video_tags(video_id="v-enr-untagged")}
    assert tags == {"howto": "llm"}


def test_tag_new_videos_never_overwrites_a_manual_or_claude_mcp_tag(monkeypatch):
    """Regression for the stage 16 protection: even if the LLM path tried to
    write over a protected tag, the repository layer blocks it -- same
    guarantee tag_videos() gets, exercised here through enrichment's own
    call path (db.upsert_video_tag with source='llm')."""
    _clear_llm_tables()
    conn = db.get_conn()
    db.upsert_channel(conn, _channel("UCenr0000000000000000020"))
    db.upsert_video(conn, _video("v-enr-protected", "UCenr0000000000000000020"))
    db.upsert_niche(conn, "n-enr-protect", "n-enr-protect", "n-enr-protect")
    db.link_video_niche(conn, "v-enr-protected", "n-enr-protect")
    db.upsert_video_tag(conn, "v-enr-protected", "theme", "fear", "manual")
    conn.commit()
    conn.close()

    conn = db.get_conn()
    written = db.upsert_video_tag(conn, "v-enr-protected", "theme", "fear", "llm")
    conn.commit()
    conn.close()
    assert written is False

    tags = {t["tag"]: t["source"] for t in T.list_video_tags(video_id="v-enr-protected")}
    assert tags == {"fear": "manual"}


def test_tag_new_videos_marks_off_taxonomy_tag_as_proposed_and_excludes_it_from_stats(monkeypatch):
    _clear_llm_tables()
    _seed_niche_with_manual_tags("n-enr-proposed", n_manual=EN.MIN_MANUAL_TAGS_FOR_NICHE)
    conn = db.get_conn()
    db.upsert_video(conn, _video("v-enr-newtag", _niche_channel_id("n-enr-proposed"),
                                 title="brand new angle"))
    db.link_video_niche(conn, "v-enr-newtag", "n-enr-proposed")
    conn.commit()
    conn.close()

    proposed_result = LLMResult(
        data={"tags": [{"tag": "brand-new-angle", "proposed": True}]},
        model="m/tag", prompt_tokens=1, completion_tokens=1, cost_usd=0.001)
    stub = _StubProvider([proposed_result] * 200)
    monkeypatch.setattr(factory, "get_provider", lambda: stub)

    result = EN.tag_new_videos(limit=200)
    assert result["proposedTags"] >= 1

    proposed = T.list_proposed_tags("n-enr-proposed")
    assert any(p["tag"] == "brand-new-angle" for p in proposed)

    stats = T.tag_stats("n-enr-proposed", "theme")
    assert "brand-new-angle" not in [t["tag"] for t in stats["tags"]]


def test_resolve_proposed_tag_accept_makes_it_count_in_tag_stats():
    _clear_llm_tables()
    _seed_niche_with_manual_tags("n-enr-accept", n_manual=EN.MIN_MANUAL_TAGS_FOR_NICHE)
    conn = db.get_conn()
    db.upsert_video(conn, _video("v-enr-accept", _niche_channel_id("n-enr-accept")))
    db.link_video_niche(conn, "v-enr-accept", "n-enr-accept")
    db.upsert_video_tag(conn, "v-enr-accept", "theme", "new-angle", "llm", proposed=True)
    conn.commit()
    conn.close()

    out = T.resolve_proposed_tag("v-enr-accept", "theme", "new-angle", accept=True)
    assert out["found"] is True

    assert T.list_proposed_tags("n-enr-accept") == []
    tags = {t["tag"]: t["source"] for t in T.list_video_tags(video_id="v-enr-accept")}
    assert tags["new-angle"] == "llm"

    stats = T.tag_stats("n-enr-accept", "theme", exclude_recent_days=0)
    assert "new-angle" in [t["tag"] for t in stats["tags"]]


def test_resolve_proposed_tag_reject_deletes_it():
    _clear_llm_tables()
    conn = db.get_conn()
    db.upsert_channel(conn, _channel("UCenr0000000000000000030"))
    db.upsert_video(conn, _video("v-enr-reject", "UCenr0000000000000000030"))
    db.upsert_niche(conn, "n-enr-reject", "n-enr-reject", "n-enr-reject")
    db.link_video_niche(conn, "v-enr-reject", "n-enr-reject")
    db.upsert_video_tag(conn, "v-enr-reject", "theme", "bad-angle", "llm", proposed=True)
    conn.commit()
    conn.close()

    out = T.resolve_proposed_tag("v-enr-reject", "theme", "bad-angle", accept=False)
    assert out["found"] is True

    tags = [t["tag"] for t in T.list_video_tags(video_id="v-enr-reject")]
    assert "bad-angle" not in tags


# --------------------------------------------------------- worker skip

def test_worker_enrichment_disabled_with_null_provider(monkeypatch):
    monkeypatch.setattr(factory, "get_provider", lambda: NullProvider())
    assert worker_cycle._llm_enrichment_enabled() is False


def test_worker_enrichment_enabled_with_a_real_provider(monkeypatch):
    monkeypatch.setattr(factory, "get_provider", lambda: _StubProvider([]))
    assert worker_cycle._llm_enrichment_enabled() is True


if __name__ == "__main__":
    setup_module()

    class _Monkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._undo):
                setattr(obj, name, value)

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        mp = _Monkeypatch()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
        finally:
            mp.undo()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)

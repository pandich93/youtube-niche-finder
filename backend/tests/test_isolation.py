"""Per-user isolation (plan 15, sub-stage 5.4): user B never sees or changes
user A's tracked channels, saved items, drafts, transcript requests or alert
read marks -- in the application layer here, through HTTP in
test_http_isolation.py. Shared public data (channels, videos, events,
transcripts) stays shared, and a channel tracked by two users is refreshed
once. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_isolation.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import alerts as AL  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import channel_tracking as T  # noqa: E402
from application import digest as DG  # noqa: E402
from application import library as L  # noqa: E402
from application import metadata_review as MR  # noqa: E402
from application import transcripts as TR  # noqa: E402

NOW = datetime.now(timezone.utc)
CH1, CH2 = "UC" + "isolone".ljust(22, "0"), "UC" + "isoltwo".ljust(22, "0")
PW = "correct horse battery"


def setup_module(_=None):
    db.init_db()


@pytest.fixture
def users():
    conn = db.get_conn()
    for t in ("tracked_channels", "saved_items", "drafts", "transcript_requests", "event_reads",
              "events", "sessions", "videos", "channels"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    for cid in (CH1, CH2):
        conn.execute("INSERT INTO channels (channel_id, title, subscriber_count) VALUES (?,?,?)",
                     (cid, cid[-6:], 1000))
    conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count) "
                 "VALUES (?,?,?,?,?)", ("isov1", CH1, "v1", (NOW - timedelta(days=2)).isoformat(), 9000))
    conn.execute("INSERT INTO videos (video_id, channel_id, title, published_at, view_count) "
                 "VALUES (?,?,?,?,?)", ("isov2", CH2, "v2", (NOW - timedelta(days=2)).isoformat(), 9000))
    conn.commit()
    conn.close()
    return AUTH.create_user("a@example.com", PW), AUTH.create_user("b@example.com", PW)


# ------------------------------------------------------------ tracking

def test_tracking_is_per_user_and_the_same_channel_can_be_tracked_twice(users):
    a, b = users
    T.track(CH1, "mine", user_id=a)
    T.track(CH1, None, user_id=b)
    T.track(CH2, None, user_id=b)
    assert [c["channel_id"] for c in T.list_tracked(user_id=a)] == [CH1]
    assert {c["channel_id"] for c in T.list_tracked(user_id=b)} == {CH1, CH2}
    T.untrack(CH1, user_id=b)
    assert [c["channel_id"] for c in T.list_tracked(user_id=a)] == [CH1]      # A untouched
    assert T.list_tracked(user_id=a)[0]["note"] == "mine"


def test_the_worker_sees_each_tracked_channel_once(users):
    a, b = users
    T.track(CH1, user_id=a)
    T.track(CH1, user_id=b)
    T.track(CH2, user_id=b)
    assert sorted(T.tracked_channel_ids()) == [CH1, CH2]


def test_single_user_callers_keep_working_as_user_1(users):
    T.track(CH2)
    assert [c["channel_id"] for c in T.list_tracked()] == [CH2]
    assert T.list_tracked(user_id=users[0]) == []


# ------------------------------------------------------------ swipe file

def test_saved_items_are_per_user(users):
    a, b = users
    item = L.save_item("video", "isov1", folder="ideas", user_id=a)
    assert L.list_items(user_id=b) == [] and L.list_folders(user_id=b) == []
    assert L.is_saved("video", "isov1", user_id=b) is False
    assert L.delete_item(item["id"], user_id=b)["deleted"] is None            # not B's item
    assert [i["id"] for i in L.list_items(user_id=a)] == [item["id"]]
    assert L.delete_item(item["id"], user_id=a)["deleted"] == item["id"]


# ------------------------------------------------------------ drafts

def test_drafts_are_per_user(users):
    a, b = users
    d = MR.save_draft("my title", niche="n", user_id=a)
    assert MR.list_drafts(user_id=b) == []
    with pytest.raises(LookupError):
        MR.link_draft(d["id"], "isov1", user_id=b)
    assert MR.list_drafts(user_id=a)[0]["videoId"] is None
    MR.link_draft(d["id"], "isov1", user_id=a)
    assert MR.draft_outcomes(min_age_days=0, user_id=b) == []
    assert [o["draftId"] for o in MR.draft_outcomes(min_age_days=0, user_id=a)] == [d["id"]]


# ------------------------------------------------------------ transcripts

def test_transcript_queues_are_per_user_but_the_transcript_is_shared(users, monkeypatch):
    a, b = users
    TR.request_transcript("isov1", reason="mine", user_id=a)
    TR.request_transcript("isov1", reason="theirs", user_id=b)
    TR.request_transcript("isov2", user_id=b)
    assert [q["videoId"] for q in TR.list_transcript_queue(user_id=a)] == ["isov1"]
    assert TR.list_transcript_queue(user_id=a)[0]["reason"] == "mine"
    assert {q["videoId"] for q in TR.list_transcript_queue(user_id=b)} == {"isov1", "isov2"}

    class _Emb:
        @staticmethod
        def embed(texts):
            import numpy as np
            return [np.ones(4, dtype="float32") for _ in texts]

        @staticmethod
        def to_blob(v):
            return v.tobytes()
    monkeypatch.setattr(TR, "_embeddings", lambda: _Emb)
    out = TR.save_transcript("isov1", "hello there, this is the pasted transcript", user_id=a)
    assert out["status"] == "ready"
    statuses = {(q["videoId"], q["status"]) for q in TR.list_transcript_queue(user_id=b)}
    assert ("isov1", "ready") in statuses                 # B's request is answered too


# ------------------------------------------------------------ alerts

def _event(kind, ref, channel):
    conn = db.get_conn()
    row = conn.execute("INSERT INTO events (kind, ref_id, payload, created_at, channel_id) "
                       "VALUES (?,?,?,?,?) RETURNING id",
                       (kind, ref, '{"channelId": "%s"}' % channel, db.now_iso(), channel)).fetchone()
    conn.commit()
    conn.close()
    return row["id"]


def test_each_user_sees_events_of_their_own_channels_with_their_own_read_marks(users):
    a, b = users
    T.track(CH1, user_id=a)
    T.track(CH1, user_id=b)
    T.track(CH2, user_id=b)
    e1 = _event("outlier", "isov1", CH1)
    e2 = _event("acceleration", "isov2:2026-09-29T00:00:00", CH2)   # composite ref id
    assert {e["id"] for e in AL.list_events(user_id=a)} == {e1}
    assert {e["id"] for e in AL.list_events(user_id=b)} == {e1, e2}
    AL.mark_seen(all_unseen=True, user_id=a)
    assert AL.unseen_count(user_id=a) == 0
    assert AL.unseen_count(user_id=b) == 2                # A's marks are A's
    AL.mark_seen(ids=[e2], user_id=a)                     # not A's event: no effect for B
    assert {e["id"] for e in AL.list_events(unseen_only=True, user_id=b)} == {e1, e2}
    assert AL.list_events(user_id=a)[0]["seenAt"] is not None


def test_the_digest_covers_the_users_own_channels(users):
    a, b = users
    T.track(CH1, user_id=a)
    T.track(CH2, user_id=b)
    _event("outlier", "isov2", CH2)
    d_a = DG.build_digest(period="48h", user_id=a)
    d_b = DG.build_digest(period="48h", user_id=b)
    assert d_a["eventIds"] == [] and len(d_b["eventIds"]) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_older_events_get_their_channel_from_the_payload():
    conn = db.get_conn()
    conn.execute("INSERT INTO events (kind, ref_id, payload, created_at) VALUES (?,?,?,?)",
                 ("title_change", "isov1:x", '{"channelId": "%s"}' % CH1, db.now_iso()))
    db.migrate(conn)
    conn.commit()
    row = conn.execute("SELECT channel_id FROM events WHERE ref_id = 'isov1:x'").fetchone()
    conn.execute("DELETE FROM events")
    conn.commit()
    conn.close()
    assert row["channel_id"] == CH1


def test_a_malformed_old_payload_does_not_stop_the_migration():
    conn = db.get_conn()
    conn.execute("INSERT INTO events (kind, ref_id, payload, created_at) VALUES (?,?,?,?)",
                 ("outlier", "broken", "not json {", db.now_iso()))
    db.migrate(conn)
    conn.commit()
    row = conn.execute("SELECT channel_id FROM events WHERE ref_id = 'broken'").fetchone()
    conn.execute("DELETE FROM events")
    conn.commit()
    conn.close()
    assert row["channel_id"] is None

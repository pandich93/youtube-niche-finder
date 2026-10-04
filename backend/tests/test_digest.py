"""Tests for application/digest.py (plan 07): the once-a-day Telegram/webhook
summary -- what goes in, the 4096-char Telegram limit, "not before
DIGEST_HOUR, never twice a day", and NOTIFY_MODE=digest silencing the
per-event alerts. Same throwaway-schema setup as test_notify.py; the
notifier and the two non-event sources are monkeypatched -- no network.
Run with pytest, or directly: python3 tests/test_digest.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import (  # noqa: E402
    alerts,
    digest,
)
from application import channel_tracking as T  # noqa: E402
from application import packaging as PKG  # noqa: E402
from infrastructure.notify import factory as notify_factory  # noqa: E402
from infrastructure.notify.null import NullNotifier  # noqa: E402

TZ = timezone(timedelta(hours=5))
MORNING = datetime(2026, 9, 29, 9, 0, tzinfo=TZ)
TOO_EARLY = datetime(2026, 9, 29, 6, 0, tzinfo=TZ)


def setup_module(_=None):
    db.init_db()


class _StubNotifier:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return self.ok


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    conn = db.get_conn()
    for t in ("events", "alert_deliveries"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM meta WHERE key LIKE 'digest_%'")
    conn.commit()
    conn.close()
    monkeypatch.setattr(T, "recently_added_outlier_channels",
                        lambda **kw: {"channels": []})
    monkeypatch.setattr(PKG, "packaging_feed", lambda **kw: {"changes": []})
    monkeypatch.setattr(digest, "DIGEST_HOUR", 8)
    monkeypatch.setattr(digest, "DIGEST_SKIP_EMPTY", True)


def _event(kind, ref_id, payload, hours_ago=1):
    # The digest covers the local user's watchlist (plan 15): the event's
    # channel is tracked here, as the alert scan only emits for tracked ones.
    channel = payload.get("channelId") or "UCdigesttest"
    conn = db.get_conn()
    conn.execute("INSERT INTO tracked_channels (user_id, channel_id, added_at, active) "
                 "VALUES (1, ?, ?, 1) ON CONFLICT DO NOTHING", (channel, db.now_iso()))
    conn.execute("INSERT INTO events (kind, ref_id, payload, created_at, channel_id) "
                 "VALUES (?,?,?,?,?)",
                 (kind, ref_id, __import__("json").dumps(payload),
                  (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(), channel))
    conn.commit()
    conn.close()


def _stub(monkeypatch, ok=True):
    n = _StubNotifier(ok)
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: n)
    return n


# ------------------------------------------------------------ build / format

def test_build_groups_the_last_day_by_kind_and_ranks_by_strength(monkeypatch):
    for i, score in enumerate((3.5, 9.0, 4.2)):
        _event("outlier", f"v{i}", {"videoId": f"v{i}", "title": f"video {i}",
                                    "outlierScore": score})
    _event("outlier", "vold", {"videoId": "vold", "title": "old", "outlierScore": 50},
           hours_ago=30)
    _event("channel_gone", "UCx:t", {"channelId": "UCx", "title": "Gone"})
    monkeypatch.setattr(T, "recently_added_outlier_channels", lambda **kw: {
        "channelsMatched": 7, "channels": [
            {"channelId": "UCr", "channelTitle": "Rising", "multiplier": 6.1,
             "subscribers": 36_800_000}]})
    d = digest.build_digest(top_n=2)
    assert [e["payload"]["outlierScore"] for e in d["outliers"]["items"]] == [9.0, 4.2]
    assert d["outliers"]["total"] == 3                     # old one is outside 24h
    assert d["gone"]["total"] == 1
    assert d["risingChannels"]["items"][0]["channelTitle"] == "Rising"
    assert d["risingChannels"]["total"] == 7               # all matched, not just shown
    assert d["empty"] is False
    assert "36.8M подп." in digest.format_digest(d)


def test_topic_matches_and_milestones_reach_the_digest():
    # plans 17 and 19: digest-mode users get these alerts only through it
    _event("milestone", "UCm:1000", {"channelId": "UCm", "title": "Growing", "milestone": 1000,
                                     "subscribers": 1004})
    conn = db.get_conn()
    conn.execute("INSERT INTO events (kind, ref_id, payload, created_at, channel_id, user_id) "
                 "VALUES ('topic_match', '1:vt', ?, ?, 'UCany', 1)",
                 (__import__("json").dumps({"topic": "ai tools", "title": "New AI agent",
                                            "videoId": "vt", "similarity": 0.8}), db.now_iso()))
    conn.commit()
    conn.close()
    d = digest.build_digest()
    assert d["topics"]["total"] == 1 and d["milestones"]["total"] == 1 and d["empty"] is False
    text = digest.format_digest(d)
    assert "Ваши темы" in text and "«ai tools»: New AI agent" in text and "Рубежи" in text


def test_repackaging_section_is_per_user_only_in_multi_user_mode(monkeypatch):
    seen = []
    monkeypatch.setattr(PKG, "packaging_feed",
                        lambda **kw: seen.append(kw.get("user_id")) or {"changes": []})
    monkeypatch.delenv("NF_MULTI_USER", raising=False)
    digest.build_digest(user_id=1)
    monkeypatch.setenv("NF_MULTI_USER", "1")
    digest.build_digest(user_id=2)
    assert seen == [None, 2]


def test_empty_day_is_flagged():
    d = digest.build_digest()
    assert d["empty"] is True


def test_format_stays_under_telegram_limit_and_escapes_html(monkeypatch):
    for i in range(200):
        _event("outlier", f"v{i}", {"videoId": f"v{i}", "outlierScore": 3 + i / 100,
                                    "title": "<b>" + "очень длинный заголовок " * 20})
    text = digest.format_digest(digest.build_digest(top_n=50))
    assert len(text) <= digest.TELEGRAM_LIMIT
    assert "&lt;b&gt;" in text and "<b>очень" not in text
    assert "ещё" in text                                    # the rest is summarised


# ------------------------------------------------------------ sending

def test_no_notifier_configured_is_a_quiet_skip(monkeypatch):
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: NullNotifier())
    out = digest.send_digest(now=MORNING)
    assert out["sent"] is False and out["reason"] == "no notifier"


def test_not_before_digest_hour_and_never_twice_a_day(monkeypatch):
    n = _stub(monkeypatch)
    _event("outlier", "v1", {"videoId": "v1", "title": "t", "outlierScore": 5})
    assert digest.send_digest(now=TOO_EARLY)["reason"] == "too early"
    assert digest.send_digest(now=MORNING)["sent"] is True
    again = digest.send_digest(now=MORNING + timedelta(hours=3))
    assert again["sent"] is False and again["reason"] == "already sent today"
    assert len(n.sent) == 1
    # the next local day sends again
    assert digest.send_digest(now=MORNING + timedelta(days=1))["sent"] is True


def test_force_sends_outside_the_schedule(monkeypatch):
    n = _stub(monkeypatch)
    _event("outlier", "v1", {"videoId": "v1", "title": "t", "outlierScore": 5})
    digest.send_digest(now=MORNING)
    assert digest.send_digest(now=TOO_EARLY, force=True)["sent"] is True
    assert len(n.sent) == 2


def test_empty_day_is_not_sent_but_is_marked_done(monkeypatch):
    n = _stub(monkeypatch)
    out = digest.send_digest(now=MORNING)
    assert out["sent"] is False and out["reason"] == "empty"
    assert n.sent == []
    assert digest.send_digest(now=MORNING)["reason"] == "already sent today"


def test_failed_send_is_retried_next_cycle(monkeypatch):
    _stub(monkeypatch, ok=False)
    _event("outlier", "v1", {"videoId": "v1", "title": "t", "outlierScore": 5})
    assert digest.send_digest(now=MORNING)["reason"] == "send failed"
    _stub(monkeypatch, ok=True)
    assert digest.send_digest(now=MORNING)["sent"] is True


def test_digest_mode_silences_instant_alerts_and_marks_covered_events(monkeypatch):
    n = _stub(monkeypatch)
    monkeypatch.setattr(alerts, "NOTIFY_MODE", "digest")
    _event("outlier", "v1", {"videoId": "v1", "title": "t", "outlierScore": 5})
    assert alerts.deliver()["skipped"] is True
    assert n.sent == []
    digest.send_digest(now=MORNING)
    # switching back to instant must not flood with what the digest covered
    monkeypatch.setattr(alerts, "NOTIFY_MODE", "instant")
    assert alerts.deliver()["sent"] == 0
    assert len(n.sent) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

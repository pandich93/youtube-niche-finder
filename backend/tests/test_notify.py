"""Tests for infrastructure/notify/* and application/alerts.py:deliver()
(stage 07). No network -- the notifier itself is monkeypatched via
infrastructure.notify.factory.get_notifier, same pattern as LLM provider
tests. Run with pytest, or directly: python3 tests/test_notify.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import infrastructure.postgres as db                # noqa: E402
from application import alerts                        # noqa: E402
from infrastructure.notify import factory as notify_factory  # noqa: E402
from infrastructure.notify.null import NullNotifier    # noqa: E402


def setup_module(_=None):
    db.init_db()


class _StubNotifier:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return self.ok


def _clear_alert_tables():
    conn = db.get_conn()
    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM alert_deliveries")
    conn.commit()
    conn.close()


def _emit(kind, ref_id, payload):
    conn = db.get_conn()
    alerts._emit(conn, kind, ref_id, payload)
    conn.commit()
    conn.close()


def test_deliver_is_a_noop_by_default_null_notifier(monkeypatch):
    _clear_alert_tables()
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: NullNotifier())
    _emit("outlier", "v1", {"videoId": "v1", "title": "t", "outlierScore": 5, "views": 1000})

    out = alerts.deliver()

    assert out["skipped"] is True
    conn = db.get_conn()
    count = conn.execute("SELECT COUNT(*) AS n FROM alert_deliveries").fetchone()["n"]
    conn.close()
    assert count == 0


def test_deliver_sends_each_pending_event_once(monkeypatch):
    _clear_alert_tables()
    stub = _StubNotifier()
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: stub)
    _emit("outlier", "v1", {"videoId": "v1", "channelId": "c1", "title": "t1",
                            "outlierScore": 5, "views": 1000})

    out = alerts.deliver()

    assert out["sent"] == 1
    assert len(stub.sent) == 1
    assert "t1" in stub.sent[0]


def test_deliver_never_resends_an_already_delivered_event(monkeypatch):
    _clear_alert_tables()
    stub = _StubNotifier()
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: stub)
    _emit("outlier", "v2", {"videoId": "v2", "title": "t2", "outlierScore": 5, "views": 1000})

    alerts.deliver()
    out = alerts.deliver()  # nothing new pending

    assert out["sent"] == 0
    assert len(stub.sent) == 1  # not resent


def test_deliver_sends_first_max_per_cycle_individually_and_rest_as_one_summary(monkeypatch):
    _clear_alert_tables()
    stub = _StubNotifier()
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: stub)
    for i in range(15):
        _emit("outlier", f"vbulk{i}", {"videoId": f"vbulk{i}", "title": f"t{i}",
                                       "outlierScore": 3, "views": 100})

    out = alerts.deliver(max_per_cycle=10)

    assert out["sent"] == 10
    assert out["summarized"] == 5
    assert len(stub.sent) == 11  # 10 individual + 1 summary message
    assert "5" in stub.sent[-1]  # summary mentions the remaining count

    conn = db.get_conn()
    count = conn.execute("SELECT COUNT(*) AS n FROM alert_deliveries").fetchone()["n"]
    conn.close()
    assert count == 15  # all marked delivered, including the summarized ones


def test_deliver_does_not_mark_a_failed_send_as_delivered(monkeypatch):
    _clear_alert_tables()
    stub = _StubNotifier(ok=False)
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: stub)
    _emit("outlier", "vfail", {"videoId": "vfail", "title": "t", "outlierScore": 5, "views": 1})

    out = alerts.deliver()

    assert out["sent"] == 0
    assert out["failed"] == 1
    conn = db.get_conn()
    count = conn.execute("SELECT COUNT(*) AS n FROM alert_deliveries").fetchone()["n"]
    conn.close()
    assert count == 0  # left pending for the next cycle to retry


def test_telegram_notifier_swallows_a_network_error_instead_of_raising(monkeypatch):
    import infrastructure.notify.telegram as tg

    def _boom(*a, **kw):
        raise ConnectionError("network exploded")
    monkeypatch.setattr(tg.httpx, "post", _boom)

    ok = tg.TelegramNotifier("fake-token", "fake-chat").send("hi")

    assert ok is False  # never raised past send()


def test_webhook_notifier_swallows_a_network_error_instead_of_raising(monkeypatch):
    import infrastructure.notify.webhook as wh

    def _boom(*a, **kw):
        raise ConnectionError("network exploded")
    monkeypatch.setattr(wh.httpx, "post", _boom)

    ok = wh.WebhookNotifier("http://example.invalid/hook").send("hi")

    assert ok is False


def test_message_format_includes_youtube_and_dashboard_links():
    ev = {"kind": "outlier", "payload": {"videoId": "vlink", "channelId": "clink",
                                         "title": "<script>hack</script>",
                                         "outlierScore": 4.2, "views": 5000}}
    text = alerts._format_message(ev)
    assert "vlink" in text
    assert "clink" in text
    assert "&lt;script&gt;" in text  # HTML-escaped, Telegram parse_mode=HTML
    assert "<script>" not in text


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

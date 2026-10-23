"""Multi-user plan P1-14: logs, metrics and operator alerts for a hosted copy.
JSON log lines mask secrets and carry no email or address; /metrics is off
without NF_METRICS_TOKEN, refuses a wrong token, labels requests by route
template and reports the quota, the LLM spend and the worker's steps from
the database; operator alerts go out at most once per kind per window;
Sentry events lose headers, cookies and query strings.
Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_observability.py
"""
import json
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.youtube.client as yt  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import worker_cycle as W  # noqa: E402
from infrastructure import observability as OBS  # noqa: E402

FAKE_KEY = "AIza" + "x" * 35
client = TestClient(api.app, headers={"X-NF-Client": "tests"})


def setup_module(_=None):
    db.init_db()


def _clear_meta(*prefixes):
    conn = db.get_conn()
    for p in prefixes:
        conn.execute("DELETE FROM meta WHERE key LIKE ?", (p + "%",))
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setenv("NF_MULTI_USER", "0")
    _clear_meta("operator_alert_", "worker_step_", "worker_last_cycle")


class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


@pytest.fixture
def notifier(monkeypatch):
    from infrastructure.notify import factory
    n = _Notifier()
    monkeypatch.setattr(factory, "get_notifier", lambda: n)
    return n


# ------------------------------------------------------------------ logs

def _record(msg, exc=None):
    rec = logging.LogRecord("niche-finder.test", logging.ERROR, __file__, 1, msg, None, exc)
    return json.loads(OBS.JsonFormatter("web").format(rec))


def test_json_lines_mask_keys_in_the_message_and_the_traceback():
    try:
        raise RuntimeError(f"GET https://x/?key={FAKE_KEY}")
    except RuntimeError:
        line = _record(f"failed with {FAKE_KEY}", sys.exc_info())
    assert line["service"] == "web" and line["level"] == "error" and line["logger"] == "niche-finder.test"
    assert FAKE_KEY not in json.dumps(line) and "<redacted>" in line["msg"] and "<redacted>" in line["exc"]


def test_access_lines_name_the_route_not_the_path(monkeypatch, caplog):
    monkeypatch.setenv("NF_LOG_FORMAT", "json")
    with caplog.at_level(logging.INFO, logger="niche-finder.access"):
        client.get("/api/videos/secretvideo123/similar")
    rec = next(r for r in caplog.records if r.name == "niche-finder.access")
    assert rec.nf["route"] == "/api/videos/{video_id}/similar" and rec.nf["method"] == "GET"
    assert "secretvideo123" not in json.dumps(rec.nf) and "ip" not in rec.nf


def test_text_mode_writes_no_access_lines(monkeypatch, caplog):
    monkeypatch.setenv("NF_LOG_FORMAT", "text")
    with caplog.at_level(logging.INFO, logger="niche-finder.access"):
        client.get("/api/health")
    assert not [r for r in caplog.records if r.name == "niche-finder.access"]


# --------------------------------------------------------------- metrics

def test_render_is_prometheus_text_and_skips_unknown_values():
    OBS.inc("nf_operator_alerts_total", kind='we"ird\\')
    text = OBS.render([("nf_demo", "a demo", [({"a": "1"}, 2), ({"a": "2"}, None)])])
    assert "# TYPE nf_operator_alerts_total counter" in text
    assert 'kind="we\\"ird\\\\"' in text
    assert "# TYPE nf_demo gauge" in text and 'nf_demo{a="1"} 2' in text and 'a="2"' not in text


def test_metrics_is_off_without_a_token(monkeypatch):
    monkeypatch.delenv("NF_METRICS_TOKEN", raising=False)
    assert client.get("/metrics").status_code == 404


def test_metrics_refuses_a_wrong_token(monkeypatch):
    monkeypatch.setenv("NF_METRICS_TOKEN", "right-token-123")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_metrics_reports_requests_quota_llm_and_the_worker(monkeypatch):
    monkeypatch.setenv("NF_METRICS_TOKEN", "right-token-123")
    conn = db.get_conn()
    db.set_meta(conn, yt.units_meta_key(), "1234")
    db.set_meta(conn, "worker_last_cycle", "2026-10-23T10:00:00+00:00")
    conn.commit()
    conn.close()
    W._record_step("rss watch", "ok", 1.5)
    W._record_step("refresh stats", "failed", 0.25)
    client.get("/api/videos/abc/similar")

    r = client.get("/metrics", headers={"Authorization": "Bearer right-token-123"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    text = r.text
    assert 'route="/api/videos/{video_id}/similar"' in text and "abc" not in text
    assert "nf_youtube_units_used_today 1234" in text
    assert "nf_youtube_units_daily_limit " in text and "nf_llm_cost_usd_today " in text
    assert "nf_worker_last_cycle_timestamp_seconds 1792749600.0" in text
    assert 'nf_worker_step_last_ok{step="rss_watch"} 1' in text
    assert 'nf_worker_step_last_ok{step="refresh_stats"} 0' in text
    assert 'nf_worker_step_last_duration_seconds{step="rss_watch"} 1.5' in text
    assert 'nf_worker_step_failures{step="refresh_stats"} 1' in text
    assert 'nf_users{state="active"}' in text


# ------------------------------------------------------- operator alerts

def test_an_alert_goes_out_once_per_window(monkeypatch, notifier):
    monkeypatch.setenv("YOUTUBE_API_KEY", FAKE_KEY)
    assert OBS.operator_alert("demo", f"pool spent <now> {FAKE_KEY}")
    assert not OBS.operator_alert("demo", "again")
    assert len(notifier.sent) == 1
    assert FAKE_KEY not in notifier.sent[0] and "&lt;now&gt;" in notifier.sent[0]
    monkeypatch.setenv("NF_OPERATOR_ALERT_HOURS", "0")
    assert OBS.operator_alert("demo", "window 0: every time")
    assert len(notifier.sent) == 2


def test_alerts_can_be_turned_off(monkeypatch, notifier):
    monkeypatch.setenv("NF_OPERATOR_ALERTS", "0")
    assert not OBS.operator_alert("demo", "x") and not notifier.sent


def test_no_notifier_means_no_message_and_no_claim(monkeypatch):
    for name in ("NOTIFY_TELEGRAM_BOT_TOKEN", "NOTIFY_TELEGRAM_CHAT_ID", "NOTIFY_WEBHOOK_URL"):
        monkeypatch.delenv(name, raising=False)
    assert not OBS.operator_alert("demo-null", "x")
    conn = db.get_conn()
    assert db.get_meta(conn, "operator_alert_demo_null") is None
    conn.close()


def test_a_spent_shared_pool_alerts_the_operator(monkeypatch):
    raised = []
    monkeypatch.setattr(OBS, "operator_alert", lambda kind, text: raised.append(kind))
    conn = db.get_conn()
    db.set_meta(conn, yt.units_meta_key(), str(yt.DAILY_UNIT_LIMIT))
    conn.commit()
    conn.close()
    try:
        with pytest.raises(yt.SharedPoolExhausted):
            yt._reserve("videos")
    finally:
        _clear_meta("yt_units_")
    assert raised == ["youtube-pool"]


def test_a_failing_worker_step_is_recorded_and_alerts(monkeypatch):
    raised = []
    monkeypatch.setattr(OBS, "operator_alert", lambda kind, text: raised.append((kind, text)))

    def boom():
        raise ValueError("bad row")

    assert W._safe("own channels sync", boom) is None
    assert W._safe("rss watch", lambda: {"checked": 3}) == {"checked": 3}
    assert raised == [("worker-step:own_channels_sync",
                       "worker step 'own channels sync' failed: ValueError: bad row")]
    conn = db.get_conn()
    step = json.loads(db.get_meta(conn, "worker_step_own_channels_sync"))
    failures = db.get_meta(conn, "worker_step_failures_own_channels_sync")
    ok = json.loads(db.get_meta(conn, "worker_step_rss_watch"))
    conn.close()
    assert step["outcome"] == "failed" and failures == "1" and ok["outcome"] == "ok"


def test_heartbeat_marks_the_end_of_a_cycle():
    W._heartbeat()
    conn = db.get_conn()
    assert db.get_meta(conn, "worker_last_cycle")
    conn.close()


# ---------------------------------------------------------------- Sentry

def test_sentry_events_lose_headers_cookies_queries_and_addresses(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", FAKE_KEY)
    event = {"request": {"url": "https://x/api/v", "headers": {"Authorization": "Bearer nf_x"},
                         "cookies": {"nf_session": "s"}, "query_string": "q=me@example.com"},
             "user": {"id": "7", "ip_address": "1.2.3.4", "email": "me@example.com"},
             "exception": {"values": [{"value": f"boom {FAKE_KEY}"}]}}
    out = OBS._before_send(event, {})
    assert out["request"] == {"url": "https://x/api/v"} and out["user"] == {"id": "7"}
    assert FAKE_KEY not in json.dumps(out)


def test_sentry_stays_off_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert OBS.init_sentry("web") is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

"""Observability for a hosted copy (multi-user plan P1-14): structured logs,
Prometheus metrics, operator alerts and optional Sentry.

A local copy sees no difference unless it asks for one:
  NF_LOG_FORMAT=json     one JSON object per log line (default: text, as before)
  NF_METRICS_TOKEN       opens GET /metrics on the dashboard for that bearer token
  SENTRY_DSN             sends unhandled errors to Sentry
  NOTIFY_TELEGRAM_* / NOTIFY_WEBHOOK_URL in .env also receive operator alerts
                         (NF_OPERATOR_ALERTS=0 turns them off)

Everything that leaves the process -- log lines, alerts, Sentry events --
passes through redact(): no API keys, tokens or OAuth secrets. Log lines
carry no email and no IP address; access lines name the route template
(/api/videos/{video_id}), never the query string.
"""
import html
import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timedelta, timezone

from infrastructure.redact import redact

# ------------------------------------------------------------------ logs


def json_logs() -> bool:
    return os.environ.get("NF_LOG_FORMAT", "text").strip().lower() == "json"


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record):
        out = {"ts": datetime.fromtimestamp(record.created, timezone.utc)
                     .isoformat(timespec="milliseconds"),
               "level": record.levelname.lower(), "service": self.service,
               "logger": record.name, "msg": redact(record.getMessage())}
        out.update(getattr(record, "nf", None) or {})
        if record.exc_info:
            out["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False, default=str)


_service = None


def configure_logging(service: str):
    """Once per process. Text mode leaves logging exactly as it was; JSON mode
    sends every logger, uvicorn's included, through one JSON handler on stderr
    -- never stdout, which is the MCP protocol itself over stdio -- and
    silences uvicorn's access line (it carries the client address and the
    query string) in favour of access() below."""
    global _service
    if _service:
        return
    _service = service
    if not json_logs():
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO")
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers[:] = []
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True


def access(method: str, route: str, status: int, seconds: float, user_id=None):
    """One line per API request in JSON mode (text mode keeps uvicorn's own)."""
    if not json_logs():
        return
    logging.getLogger("niche-finder.access").info(
        "%s %s %s", method, route, status,
        extra={"nf": {"method": method, "route": route, "status": status,
                      "ms": round(seconds * 1000, 1), "user": user_id}})


# --------------------------------------------------------------- metrics
# In-process counters and summaries for this process (the dashboard's
# requests). What other processes do -- the worker's steps, the quota, the LLM
# spend -- is read from the database at scrape time (application/ops_metrics.py).

_lock = threading.Lock()
_counters = {}   # (name, labels) -> value
_summaries = {}  # (name, labels) -> [count, sum]
_HELP = {
    "nf_http_requests_total": ("counter", "HTTP API requests by route template, method and status"),
    "nf_http_request_duration_seconds": ("summary", "HTTP API request duration by route template"),
    "nf_operator_alerts_total": ("counter", "Operator alerts raised in this process, by kind"),
}


def _key(name, labels):
    return name, tuple(sorted((k, str(v)) for k, v in labels.items()))


def inc(name: str, n: float = 1, **labels):
    with _lock:
        k = _key(name, labels)
        _counters[k] = _counters.get(k, 0) + n


def observe(name: str, seconds: float, **labels):
    with _lock:
        k = _key(name, labels)
        s = _summaries.setdefault(k, [0, 0.0])
        s[0] += 1
        s[1] += seconds


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(pairs) -> str:
    if not pairs:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in pairs) + "}"


def _num(v) -> str:
    return repr(float(v)) if isinstance(v, float) else str(v)


def render(gauges=()) -> str:
    """Prometheus text format: this process's counters and summaries, then
    `gauges` -- [(name, help, [(labels dict, value)])] read at scrape time."""
    lines = []
    with _lock:
        counters, summaries = dict(_counters), {k: list(v) for k, v in _summaries.items()}
    for name in sorted({n for n, _ in counters} | {n for n, _ in summaries}):
        kind, text = _HELP.get(name, ("untyped", name))
        lines += [f"# HELP {name} {text}", f"# TYPE {name} {kind}"]
        for (n, labels), value in sorted(counters.items()):
            if n == name:
                lines.append(f"{name}{_labels(labels)} {_num(value)}")
        for (n, labels), (count, total) in sorted(summaries.items()):
            if n == name:
                lines.append(f"{name}_count{_labels(labels)} {count}")
                lines.append(f"{name}_sum{_labels(labels)} {_num(round(total, 6))}")
    for name, text, samples in gauges:
        lines += [f"# HELP {name} {text}", f"# TYPE {name} gauge"]
        for labels, value in samples:
            if value is not None:
                lines.append(f"{name}{_labels(sorted((labels or {}).items()))} {_num(value)}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------- operator alerts

def alerts_enabled() -> bool:
    return os.environ.get("NF_OPERATOR_ALERTS", "1").strip().lower() not in ("0", "false", "no", "off")


def _alert_every() -> timedelta:
    try:
        hours = float(os.environ.get("NF_OPERATOR_ALERT_HOURS", "6") or 6)
    except ValueError:
        hours = 6.0
    return timedelta(hours=max(hours, 0.0))


def _claim(kind: str) -> bool:
    """True if no alert of this kind went out within NF_OPERATOR_ALERT_HOURS;
    records this one. Two processes racing may both send -- a duplicate
    message, never a lost one."""
    import infrastructure.postgres as db
    key = "operator_alert_" + re.sub(r"[^a-z0-9]+", "_", kind.lower()).strip("_")
    now = datetime.now(timezone.utc)
    conn = db.get_conn()
    try:
        last = db.get_meta(conn, key)
        if last:
            try:
                if now - datetime.fromisoformat(last) < _alert_every():
                    return False
            except ValueError:
                pass
        db.set_meta(conn, key, now.isoformat())
        conn.commit()
        return True
    finally:
        conn.close()


def operator_alert(kind: str, text: str) -> bool:
    """Tell whoever runs the server that something needs them: the shared
    YouTube pool is spent, a worker step failed. Always logged as a warning;
    sent to the .env notifier at most once per kind per NF_OPERATOR_ALERT_HOURS.
    Never raises -- an alert must not break the request or the worker step
    that noticed the problem. Returns whether a message was sent."""
    from domain.i18n import default_locale, tr, use_locale
    text = redact(text)
    inc("nf_operator_alerts_total", kind=kind.split(":", 1)[0])
    logging.getLogger("niche-finder.operator").warning("operator alert %s: %s", kind, text)
    if not alerts_enabled():
        return False
    try:
        from infrastructure.notify import factory
        from infrastructure.notify.null import NullNotifier
        notifier = factory.get_notifier()
        if isinstance(notifier, NullNotifier) or not _claim(kind):
            return False
        with use_locale(default_locale()):
            message = tr("⚠️ <b>niche-finder: нужен оператор</b>\n{text}", text=html.escape(text))
        return bool(notifier.send(message))
    except Exception:
        logging.getLogger("niche-finder.operator").warning(
            "operator alert %s could not be sent", kind, exc_info=True)
        return False


# ---------------------------------------------------------------- Sentry

def _scrub(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _before_send(event, hint):
    """No cookies, headers (Authorization!), query strings or addresses; the
    user only by id; every remaining string through redact()."""
    request = event.get("request")
    if isinstance(request, dict):
        for field in ("cookies", "headers", "query_string", "data", "env"):
            request.pop(field, None)
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user["id"]} if "id" in user else {}
    return _scrub(event)


def init_sentry(service: str) -> bool:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger(__name__).warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return False
    sentry_sdk.init(dsn=dsn, environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
                    server_name=service, send_default_pii=False, traces_sample_rate=0.0,
                    before_send=_before_send)
    return True


def setup(service: str):
    """Entry points call this once at start: logs, then Sentry."""
    configure_logging(service)
    init_sentry(service)

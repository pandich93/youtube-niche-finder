"""Background collector -- the thing that makes '24 hours' mean anything.

The YouTube API only ever tells you a video's view count *right now*. Velocity,
acceleration, subscriber growth, period-over-period category shifts and
title-change detection all require someone to write down the numbers on a
schedule. That someone is this process.

Default daily budget (well inside the free tier):
  hot refresh  every 3h   videos published in the last 7d   ~1 unit / 50 videos
  full refresh once a day videos published in the last 30d  ~1 unit / 50 videos
  channels     once a day tracked channels                  ~1 unit / 50 channels
  trending     once a day mostPopular per region            ~3 units / region
  queries      once a day WORKER_QUERIES, if set            1 search call each

Configure with env vars (see .env.example). Set WORKER_QUERIES to keep a set of
topics continuously fresh, e.g. "ai automation,faceless history,нейросети".
"""
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone

import infrastructure.postgres as db
from application import collecting as collector
from application import alerts as alerts_mod
from domain import periods as P
import infrastructure.youtube.client as yt

load_env = None
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

API_KEY = os.environ.get("YOUTUBE_API_KEY")

RSS_INTERVAL_MIN = int(os.environ.get("WORKER_RSS_INTERVAL_MIN", "30"))
ALERTS_INTERVAL_MIN = int(os.environ.get("WORKER_ALERTS_INTERVAL_MIN", "60"))
HOT_INTERVAL_MIN = int(os.environ.get("WORKER_HOT_INTERVAL_MIN", "180"))
DAILY_INTERVAL_MIN = int(os.environ.get("WORKER_DAILY_INTERVAL_MIN", "1440"))
HOT_PERIOD = os.environ.get("WORKER_HOT_PERIOD", "7d")
FULL_PERIOD = os.environ.get("WORKER_FULL_PERIOD", "30d")
HOT_LIMIT = int(os.environ.get("WORKER_HOT_LIMIT", "1000"))
FULL_LIMIT = int(os.environ.get("WORKER_FULL_LIMIT", "3000"))
REGIONS = [r.strip() for r in os.environ.get("WORKER_REGIONS", "US").split(",") if r.strip()]
DO_TRENDING = os.environ.get("WORKER_TRENDING", "1") not in ("0", "false", "no")
QUERIES = [q.strip() for q in os.environ.get("WORKER_QUERIES", "").split(",") if q.strip()]
QUERY_PERIOD = os.environ.get("WORKER_QUERY_PERIOD", "24h")
QUERY_PAGES = int(os.environ.get("WORKER_QUERY_PAGES", "1"))
EMBED_INTERVAL_MIN = int(os.environ.get("WORKER_EMBED_INTERVAL_MIN", "60"))
EMBED_BATCH = int(os.environ.get("WORKER_EMBED_BATCH", "500"))
DO_EMBED = os.environ.get("WORKER_EMBED", "1") not in ("0", "false", "no")

_stop = False


def _handle_stop(signum, frame):
    global _stop
    _stop = True
    log(f"signal {signum} received, finishing current cycle and exiting")


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}",
          flush=True)


def _get_meta(key):
    conn = db.get_conn()
    value = db.get_meta(conn, key)
    conn.close()
    return value


def _set_meta(key, value):
    conn = db.get_conn()
    db.set_meta(conn, key, value)
    conn.commit()
    conn.close()


def _search_quota_blocked_today() -> bool:
    """True if a search.list call already hit QuotaExceeded today (Pacific Time) --
    no point retrying WORKER_QUERIES again before the bucket resets."""
    return _get_meta("worker_quota_blocked_until") == P.pacific_date_key()


def _due(key, interval_min):
    last = _get_meta(f"worker_last_{key}")
    if not last:
        return True
    try:
        prev = datetime.fromisoformat(last)
    except ValueError:
        return True
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - prev).total_seconds() >= interval_min * 60


def _mark(key):
    _set_meta(f"worker_last_{key}", datetime.now(timezone.utc).isoformat())


def _safe(name, fn):
    try:
        result = fn()
        log(f"{name}: {result}")
        return result
    except yt.QuotaExceeded as e:
        log(f"{name}: QUOTA EXCEEDED -- backing off until tomorrow ({e})")
        _set_meta("worker_quota_blocked_until", P.pacific_date_key())
        return None
    except Exception:
        log(f"{name}: FAILED\n{traceback.format_exc()}")
        return None


def cycle():
    if _due("rss", RSS_INTERVAL_MIN):
        _safe("rss watch", lambda: collector.discover_new_videos_via_rss(API_KEY))
        _mark("rss")

    if _due("hot", HOT_INTERVAL_MIN):
        _safe("hot refresh", lambda: collector.refresh_stats(
            API_KEY, scope="recent", period=HOT_PERIOD, limit=HOT_LIMIT))
        _mark("hot")

    if _due("alerts", ALERTS_INTERVAL_MIN):
        _safe("alerts scan", lambda: alerts_mod.scan())
        _mark("alerts")

    if DO_EMBED and _due("embed", EMBED_INTERVAL_MIN):
        _safe("embed backfill", lambda: collector.backfill_embeddings(limit=EMBED_BATCH))
        _mark("embed")

    if _due("daily", DAILY_INTERVAL_MIN):
        _safe("full refresh", lambda: collector.refresh_stats(
            API_KEY, scope="recent", period=FULL_PERIOD, limit=FULL_LIMIT))
        _safe("channel refresh", lambda: collector.refresh_channels(
            API_KEY, only_tracked=True))
        if DO_TRENDING:
            _safe("trending charts", lambda: collector.collect_trending(
                API_KEY, regions=tuple(REGIONS), category_ids=(None,), pages=2))
        if QUERIES and _search_quota_blocked_today():
            log("collect queries: skipped, search.list quota already exhausted "
                "today (resets at midnight Pacific Time)")
        else:
            for query in QUERIES:
                _safe(f"collect '{query}'", lambda query=query: collector.collect_niche(
                    API_KEY, query, period=QUERY_PERIOD, pages=QUERY_PAGES, embed=True))
        _mark("daily")


def main():
    if not API_KEY:
        log("YOUTUBE_API_KEY is not set -- the worker has nothing to collect.\n"
            "  Docker:     put the key in .env next to docker-compose.yml, then "
            "`docker compose up -d worker`\n"
            "  Without it: the read-only tools still work on whatever is already "
            "in the database; only collection needs a key.")
        sys.exit(1)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    db.init_db()
    embed_status = (f"embed backfill every {EMBED_INTERVAL_MIN}min (batch {EMBED_BATCH})"
                     if DO_EMBED else "embed backfill=off")
    log(f"worker started | db={db.display_dsn()} | rss watch every {RSS_INTERVAL_MIN}min | "
        f"alerts scan every {ALERTS_INTERVAL_MIN}min | "
        f"hot every {HOT_INTERVAL_MIN}min "
        f"({HOT_PERIOD}) | daily every {DAILY_INTERVAL_MIN}min ({FULL_PERIOD}) "
        f"| regions={REGIONS} | trending={DO_TRENDING} | queries={len(QUERIES)} "
        f"| {embed_status}")
    while not _stop:
        cycle()
        for _ in range(60):
            if _stop:
                break
            time.sleep(5)
    log("worker stopped")


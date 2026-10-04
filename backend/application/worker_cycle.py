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
  thumbnails   every 6h   tracked channels' last 30d        0 units (i.ytimg.com)
  sponsors     every 1h   new/changed video descriptions    0 units (local DB only)
  thumb_embed  every 1h   thumbnail CLIP vectors, opt-in     0 units (i.ytimg.com)
  own_sync     once a day your connected channels            Analytics API quota only
  topic_search every 6h   opted-in topics, each once a day   1 search call per topic (max 5/day)
  freshness    once a day rows not refreshed for 25 days      ~1 unit / 50 rows, capped
                          (titles, descriptions, counters; nothing is ever deleted)

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
import infrastructure.youtube.client as yt
from application import alerts as alerts_mod
from application import collecting as collector
from application import content_calendar as calendar_mod
from application import digest as digest_mod
from application import enrichment as enrich_mod
from application import freshness as freshness_mod
from application import maturity_curve as curve_mod
from application import niche_clusters as clusters_mod
from application import own_channels as own_mod
from application import packaging as packaging_mod
from application import sponsors as sponsors_mod
from application import thumbnail_search as thumbsearch_mod
from application import topic_watch as topic_mod
from domain import periods as P
from infrastructure.llm import factory as llm_factory
from infrastructure.llm.null import NullProvider

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
ENRICH_INTERVAL_MIN = int(os.environ.get("WORKER_ENRICH_INTERVAL_MIN", "120"))
ENRICH_CHANNEL_BATCH = int(os.environ.get("WORKER_ENRICH_CHANNEL_BATCH", "50"))
ENRICH_VIDEO_BATCH = int(os.environ.get("WORKER_ENRICH_VIDEO_BATCH", "100"))
CLUSTER_INTERVAL_MIN = int(os.environ.get("WORKER_CLUSTER_INTERVAL_MIN", "1440"))
CALIBRATE_INTERVAL_MIN = int(os.environ.get("WORKER_CALIBRATE_INTERVAL_MIN", "1440"))
# Thumbnail fingerprints (plan 05): downloads thumbnails of tracked channels'
# recent videos from i.ytimg.com -- no API quota, but a request to Google per
# video, so it is scoped to tracked channels and can be switched off.
DO_THUMBS = os.environ.get("WORKER_THUMBS", "1") not in ("0", "false", "no")
THUMBS_INTERVAL_MIN = int(os.environ.get("WORKER_THUMBS_INTERVAL_MIN", "360"))
THUMBS_LIMIT = int(os.environ.get("WORKER_THUMBS_LIMIT", "500"))
# Sponsor scan (plan 09): reads video descriptions already in the database for
# "sponsored by / promo code / affiliate" signals. No network, no quota; only
# videos that are new, changed or covered by older rules are read.
DO_SPONSORS = os.environ.get("WORKER_SPONSORS", "1") not in ("0", "false", "no")
SPONSORS_INTERVAL_MIN = int(os.environ.get("WORKER_SPONSORS_INTERVAL_MIN", "60"))
SPONSORS_LIMIT = int(os.environ.get("WORKER_SPONSORS_LIMIT", "5000"))
# Thumbnail vectors (plan 13): CLIP embeddings of thumbnails for "similar
# thumbnails" and thumbnail styles. OFF by default: the model is ~0.34 GB on
# disk and the step downloads thumbnails from i.ytimg.com (no API quota).
DO_THUMB_EMBED = os.environ.get("WORKER_THUMB_EMBED", "0") not in ("0", "false", "no")
THUMB_EMBED_INTERVAL_MIN = int(os.environ.get("WORKER_THUMB_EMBED_INTERVAL_MIN", "60"))
THUMB_EMBED_LIMIT = int(os.environ.get("WORKER_THUMB_EMBED_LIMIT", "200"))
# Own channels (plan 14): YouTube Analytics numbers of channels you connected
# through OAuth. Runs only when OAuth is configured and a channel is connected;
# it uses the Analytics API quota, not the Data API key's.
DO_OWN_SYNC = os.environ.get("WORKER_OWN_SYNC", "1") not in ("0", "false", "no")
OWN_SYNC_INTERVAL_MIN = int(os.environ.get("WORKER_OWN_SYNC_INTERVAL_MIN", "1440"))
# Daily digest (plan 07): only when NOTIFY_MODE is digest/both. Checked this
# often; application/digest.py itself decides whether today's is due.
DIGEST_CHECK_INTERVAL_MIN = int(os.environ.get("WORKER_DIGEST_CHECK_INTERVAL_MIN", "10"))
# Plan 16: re-read stored rows not refreshed for REFRESH_STALE_DAYS (YouTube's
# 30-day rule); caps and age live in application/freshness.py
DO_FRESHNESS = os.environ.get("WORKER_FRESHNESS", "1") not in ("0", "false", "no")
# plan 19 follow-up: topics that opted into a daily YouTube search
DO_TOPIC_SEARCH = os.environ.get("WORKER_TOPIC_SEARCH", "1") not in ("0", "false", "no")
TOPIC_SEARCH_INTERVAL_MIN = int(os.environ.get("WORKER_TOPIC_SEARCH_INTERVAL_MIN", "360"))
FRESHNESS_INTERVAL_MIN = int(os.environ.get("WORKER_FRESHNESS_INTERVAL_MIN", "1440"))

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


def _llm_enrichment_enabled() -> bool:
    return not isinstance(llm_factory.get_provider(), NullProvider)


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
        _safe("topic matches", lambda: topic_mod.match_new())
        _safe("draft reminders", lambda: calendar_mod.remind_due())
        _safe("alerts deliver", lambda: alerts_mod.deliver_all())
        _mark("alerts")

    if _due("digest", DIGEST_CHECK_INTERVAL_MIN) and digest_mod.digest_wanted():
        # Not _safe(): "too early" / "already sent today" every 10 minutes
        # would drown the log -- only a real outcome is worth a line.
        try:
            for uid, res in digest_mod.send_all_digests().items():
                if res.get("sent") or res.get("reason") in ("send failed", "empty"):
                    log(f"daily digest (user {uid}): {res}")
        except Exception:
            log(f"daily digest: FAILED\n{traceback.format_exc()}")
        _mark("digest")

    if DO_EMBED and _due("embed", EMBED_INTERVAL_MIN):
        _safe("embed backfill", lambda: collector.backfill_embeddings(limit=EMBED_BATCH))
        _mark("embed")

    if _due("enrich", ENRICH_INTERVAL_MIN):
        if not _llm_enrichment_enabled():
            log("enrich: skipped, LLM_PROVIDER=none")
        else:
            _safe("enrich channels", lambda: enrich_mod.classify_channels(
                limit=ENRICH_CHANNEL_BATCH))
            _safe("enrich videos", lambda: enrich_mod.tag_new_videos(
                limit=ENRICH_VIDEO_BATCH))
        _mark("enrich")

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

    if _due("clusters", CLUSTER_INTERVAL_MIN):
        _safe("niche clusters", lambda: clusters_mod.compute_clusters())
        _mark("clusters")

    if _due("calibrate", CALIBRATE_INTERVAL_MIN):
        _safe("maturity curve", lambda: curve_mod.apply_calibration())
        _mark("calibrate")

    if DO_THUMBS and _due("thumbs", THUMBS_INTERVAL_MIN):
        _safe("thumbnail fingerprints", lambda: packaging_mod.fingerprint_thumbnails(
            period=FULL_PERIOD, limit=THUMBS_LIMIT))
        _mark("thumbs")

    if DO_SPONSORS and _due("sponsors", SPONSORS_INTERVAL_MIN):
        _safe("sponsor scan", lambda: sponsors_mod.scan_sponsors(limit=SPONSORS_LIMIT))
        _mark("sponsors")

    if DO_THUMB_EMBED and _due("thumb_embed", THUMB_EMBED_INTERVAL_MIN):
        _safe("thumbnail vectors", lambda: thumbsearch_mod.embed_thumbnails(limit=THUMB_EMBED_LIMIT))
        _mark("thumb_embed")

    if DO_TOPIC_SEARCH and _due("topic_search", TOPIC_SEARCH_INTERVAL_MIN):
        # each topic is searched once a day at most (topic_watch.SEARCH_EVERY_HOURS);
        # running every few hours just picks up topics as they fall due
        if _search_quota_blocked_today():
            log("topic search: skipped, search.list quota already exhausted today")
        else:
            _safe("topic search", lambda: topic_mod.search_topics(API_KEY))
        _mark("topic_search")

    if DO_FRESHNESS and _due("freshness", FRESHNESS_INTERVAL_MIN):
        _safe("refresh stale rows", lambda: freshness_mod.refresh_stale(API_KEY))
        _mark("freshness")

    if DO_OWN_SYNC and _due("own_sync", OWN_SYNC_INTERVAL_MIN):
        st = _safe("own channels status", own_mod.status) or {}
        # plan 25: a sync needs the client and the key, not the redirect -- a
        # wrong web-mode redirect blocks connecting only, never the numbers
        if st and not st.get("missing", ["?"]):
            _safe("own channels sync", own_mod.sync_all)   # every user's channels
        _mark("own_sync")


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
    enrich_status = (f"enrich every {ENRICH_INTERVAL_MIN}min "
                     f"(channels {ENRICH_CHANNEL_BATCH}/videos {ENRICH_VIDEO_BATCH})"
                     if _llm_enrichment_enabled() else "enrich=off (LLM_PROVIDER=none)")
    log(f"worker started | db={db.display_dsn()} | rss watch every {RSS_INTERVAL_MIN}min | "
        f"alerts scan every {ALERTS_INTERVAL_MIN}min | "
        f"hot every {HOT_INTERVAL_MIN}min "
        f"({HOT_PERIOD}) | daily every {DAILY_INTERVAL_MIN}min ({FULL_PERIOD}) "
        f"| regions={REGIONS} | trending={DO_TRENDING} | queries={len(QUERIES)} "
        f"| {embed_status} | {enrich_status}")
    while not _stop:
        cycle()
        for _ in range(60):
            if _stop:
                break
            time.sleep(5)
    log("worker stopped")


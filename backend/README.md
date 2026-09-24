# niche-finder — backend

[![CI](https://img.shields.io/github/actions/workflow/status/pandich93/youtube-niche-finder/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/pandich93/youtube-niche-finder/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fpandich93%2Fyoutube-niche-finder%2Fmain%2Fassets%2Fcoverage.json&query=%24.totals.percent_covered_display&suffix=%25&label=coverage&style=flat-square)](../assets/coverage.json)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square&logo=python&logoColor=white)](Dockerfile)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](interfaces/http/api.py)
[![PostgreSQL 16](https://img.shields.io/badge/postgres-16-336791?style=flat-square&logo=postgresql&logoColor=white)](../docker-compose.yml)
[![MCP](https://img.shields.io/badge/MCP-61%20tools-8A2BE2?style=flat-square)](interfaces/mcp/server.py)

A self-hosted alternative to NexLev / vidIQ / ViewStats: find niches, viral
videos from small channels, trending categories and keywords **over
arbitrary periods** (24h, 48h, 7/30/90 days), plus full channel tracking and
analytics. All on the free YouTube Data API v3 and local PostgreSQL.

Classification like "faceless / AI / on topic" isn't done by the server —
it's done by the model calling these tools: the server returns raw titles,
descriptions, and thumbnails, and the decision gets made in the
conversation. No separate paid LLM key is needed. An optional LLM
(OpenRouter or a local Ollama, off by default -- `LLM_PROVIDER=none`) adds
background labeling, comment insights, "why viral" explanations, title
generation and cluster naming; see [Configuration](#configuration).

Market research and competitor formulas (`docs/research-tools.md`) are
internal notes, not included in this repository.

---

## Quota essentials (changed June 1, 2026)

| Method | Cost |
|---|---|
| `search.list` | 1 unit, but **only 100 calls a day**, a separate bucket |
| everything else | 1 unit out of the shared pool of **10,000 units a day** |

Search is scarce, reading is nearly free. So:

- `collect_niche` — the only one that spends search. Use it for new topics.
- `collect_channel` — goes through the uploads playlist: **1 unit per 50
  videos**, no 500-result cap, doesn't touch search. The main way to build
  up the corpus.
- `refresh_stats` — via `videos.batchGetStats`, ~1 unit per 50 videos.

Every collector returns a `quota` field with the actual spend.

Also: since July 21, 2025 `chart=mostPopular` only returns Music, Movies, and
Gaming charts — YouTube no longer has a general Trending tab. So
`most_popular_categories` and `trending_keywords` are computed from your own
corpus, not from the chart.

---

## Running with Docker (recommended)

```bash
cd youtube-niche-finder
cp .env.example .env          # fill in YOUTUBE_API_KEY — it builds without
                              # one, but there'll be nothing to collect with
docker compose build
docker compose up -d web worker   # also brings up postgres (depends_on)
open http://localhost:8080        # or: make open
docker compose logs -f worker
```

Upgrading from the old SQLite version and want to keep your collected data?
`python3 backend/migrate_sqlite_to_postgres.py path/to/old/niches.db` (once,
after `docker compose up -d postgres`; safe to run again).

### Upgrading to pgvector (stage 06)

The `postgres` image changed from `postgres:16-alpine` to
`pgvector/pgvector:pg16` -- same PostgreSQL 16, same data directory format
(Debian base instead of Alpine, doesn't matter for the volume), plus the
`vector` extension pre-installed. `init_db()` detects the extension every
time it runs and only uses it when present -- similar_videos,
similar_channels and the metadata-review "already covered this" check fall
back to the old pure-Python cosine comparison on any Postgres that doesn't
have it, so this upgrade is optional, not required.

**Your data survives the swap** -- it's the same volume (`postgres-data`),
same major Postgres version, same tables; the new image only adds an
extension and one nullable column (`videos.embedding_v`), migrated from the
existing `embedding` BLOB column in batches the first time `init_db()` runs
against it. The BLOB column is kept (not dropped) specifically so this is
reversible.

To upgrade:
```bash
docker compose pull postgres     # or: docker compose build, if building locally
docker compose up -d postgres    # recreates the container on the new image,
                                  # same volume -- init_db() migrates on next connect
docker compose restart web worker
```

**Back up first if this database matters to you** -- it's a one-way image
swap for that container (not for the data, which is untouched either way):
`docker run --rm -v niche-finder-postgres-data:/data -v "$PWD":/backup alpine \
tar czf /backup/postgres-data-backup.tar.gz -C /data .`

To roll back: change the image line in `docker-compose.yml` back to
`postgres:16-alpine` and `docker compose up -d postgres` again -- the
`embedding_v` column and its index simply go unused (they only exist on the
pgvector image's own storage, so a genuine downgrade drops them along with
the extension; nothing you have with plain `postgres:16-alpine` behavior is
lost, since every read path already has that Python fallback).

The dashboard is `frontend/`, see [frontend/README.md](../frontend/README.md).
It shows the same sections as the MCP tools, and only listens on localhost.

The key is only needed at runtime, not at build time: `docker compose build`
works fine with an empty `.env`. If there's no key, the worker says so and
exits, while the read tools keep working off whatever's already collected.

The worker isn't optional, it's a requirement: the YouTube API only ever
returns "how many views right now". View-gain speed, acceleration,
subscriber growth, period comparisons, and thumbnail/title-change detection
only exist because something is regularly recording the numbers. That's the
worker's job.

Claude Desktop connection — in
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "niche-finder": {
      "command": "/usr/local/bin/docker",
      "args": ["run", "--rm", "-i",
               "--network", "niche-finder_default",
               "--env-file", "/path/to/youtube-niche-finder/.env",
               "-e", "POSTGRES_HOST=postgres",
               "-v", "niche-finder-models:/models",
               "niche-finder:latest", "python", "server.py"]
    }
  }
}
```

Use the absolute path to `docker` (`which docker`), not just `"docker"` —
Claude Desktop's MCP launcher doesn't always inherit your shell's `PATH`.

There's also a `scripts/mcp-docker.sh` launcher that fills in `--env-file`
and the volumes for you, so you can point `command` at it directly instead
of writing out the full `docker run` line. **On macOS it can fail with
`Operation not permitted` / `Server disconnected`**, even though the same
script runs fine from a terminal — Claude Desktop's MCP process appears to
be sandboxed and unable to exec an arbitrary script it didn't create,
regardless of the script's file permissions. If you hit that, use the raw
`docker` command above instead (it invokes the already-trusted `docker`
binary directly, so the sandbox restriction doesn't apply).

### If the build fails at Docker Hub

```
failed to fetch anonymous token: ... lookup auth.docker.io: i/o timeout
```

This is a network issue, not a code issue: Docker can't reach the image
registry. The usual culprit is an active VPN (corporate clients like
AnyConnect regularly tunnel or drop traffic to the registry) — turn it off
and retry. If it's not the VPN, restart Docker Desktop: an `i/o timeout`
specifically on `auth.docker.io` is almost always fixed by that. To check
whether it's Docker:

```bash
curl -sI https://auth.docker.io/token | head -1   # directly from the Mac
docker pull hello-world                            # through Docker
```

If the first one works and the second doesn't, the problem is Docker
Desktop's DNS.

**Rebuilding is almost never needed.** Every service runs code straight from
`backend/` and `frontend/` (the folders are mounted into the container
read-only), so the image only exists for Python and its dependencies.
`docker compose build` is only required when `requirements.txt` changes;
otherwise `docker compose restart` is enough. And if the image was built
from an older `requirements.txt`, the web service fetches the missing
`fastapi`/`uvicorn` from PyPI at startup on its own — pypi.org and
registry.docker.io are different hosts, and the former is usually reachable
even when the latter isn't.

### First thing to run — `doctor`

```bash
docker compose run --rm mcp python cli.py doctor
# or simply: make doctor
```

It checks, in order: the key (its shape, and whether the API actually
responds), reachability of `googleapis.com`, database state, and 24-hour
window coverage — then prints, in plain words, exactly what to fix: YouTube
Data API v3 not enabled, an IP/referrer restriction on the key, exhausted
quota, an empty database, no history. One check costs 1 quota unit.

### CLI: everything, without Claude Desktop

```bash
make cli ARGS="collect-channel @somechannel"      # build up the corpus, cheap
make cli ARGS="collect 'ai automation' --period 24h"
make cli ARGS="refresh"                           # refresh counters → history
make cli ARGS="embed-videos"                      # backfill embeddings, 0 quota
make cli ARGS="viral --period 24h"
make cli ARGS="viral --period 24h --period-by discovered"
make cli ARGS="categories --period 7d --rank-by channels"
make cli ARGS="keywords --period 24h"
make cli ARGS="channels --period 24h"             # outlier channels
make cli ARGS="seed"                              # synthetic data, just to look around
make cli ARGS="stats"                             # what's in the database
make cli ARGS="export-niche brain --format csv"   # niche videos to TSV (default) / CSV
make cli ARGS="notify-test"                       # test message to Telegram / webhook
make cli ARGS="fix-tracked"                       # dry run: watchlist entries stored as
                                                  # @handle/URL instead of a channel id;
                                                  # add --apply to fix them
make cli ARGS="doctor --llm"                      # doctor + ping the LLM provider (spends budget)
```

Useful commands (`make help` shows all of them):

```bash
make up          # bring up the worker
make logs        # watch what it's collecting
make test        # smoke tests inside the image, no key and no network needed
make seed        # load synthetic data and try the tools
make stats       # what's currently in the database
make http        # run MCP over HTTP on :8765 instead of stdio
```

The `mcp` service (i.e. `make cli` and `make doctor`) mounts `./backend`
into the container read-only, so code edits show up immediately, no rebuild
needed. The worker, `mcp-http`, and `scripts/mcp-docker.sh` run code baked
into the image — those need `docker compose build`.

The database lives in the `niche-finder-postgres-data` volume (the
`postgres` service); the embeddings model cache is in `niche-finder-models`.
Rebuilding the image doesn't touch either.

`docker compose build --build-arg PREFETCH_MODEL=1` bakes the embeddings
model (~220 MB) straight into the image, if you don't want to wait for it to
download on the first semantic search.

---

## Running without Docker

```bash
cd youtube-niche-finder/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in YOUTUBE_API_KEY
python3 tests/test_smoke.py   # should be 17/17 passed -- needs a reachable
                              # Postgres (docker compose up -d postgres, or
                              # your own; see infrastructure/postgres/connection.py)
```

Each test process works in its own `nichetest_<random>` schema and drops it on
exit (`tests/schema_scope.py`), so repeated runs leave nothing behind. Set
`NICHE_KEEP_TEST_SCHEMA=1` to keep it and inspect the data after a failure; a
schema you pass in yourself via `NICHE_DB_SCHEMA` is never dropped. Note that
`pytest tests/test_smoke.py tests/test_mcp_tools.py` puts both modules in one
process and therefore one schema, where test_smoke's demo data breaks
test_mcp_tools' cosine-similarity ranking expectation -- run them the way CI
does, one process per file (see .github/workflows/ci.yml).

"Reachable Postgres" means a reachable *host* address, and that is not the
default one: the compose database is published on `127.0.0.1:5433`
(`POSTGRES_HOST_PORT`), while `_dsn()` falls back to `localhost:5432` --
hence `psycopg2.OperationalError: Connection refused` straight out of
`init_db()`. Put the host DSN in the project-root `.env`:

```bash
NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5433/niches
```

It wins over `POSTGRES_*` in `_dsn()` and stays host-only on purpose --
compose does not pass it to containers, and `scripts/mcp-docker.sh` /
`scripts/diag.sh` strip it before `docker run`, since inside a container
`localhost` is the container itself. Do **not** use `POSTGRES_PORT=5433`
instead: compose hands that same variable to the containers as the
in-network port and breaks `web`/`worker`/`mcp`.

Same thing, shorter, via the Makefile (from the project root):

```bash
make local-install   # venv + dependencies, once
make local-test      # smoke tests on the host
make local-run       # MCP server on the host (no Docker)
make dev             # HTTP dashboard on the host: uvicorn api:app --reload on :8080
```

Claude Desktop config:

```json
{
  "mcpServers": {
    "niche-finder": {
      "command": "/path/to/youtube-niche-finder/backend/.venv/bin/python3",
      "args": ["/path/to/youtube-niche-finder/backend/server.py"]
    }
  }
}
```

History isn't collected automatically in this mode — run `python3 worker.py`
separately (or via cron), otherwise the velocity fields stay empty.

---

## Tools

### Collection (spends quota)

| Tool | What it does | Cost |
|---|---|---|
| `collect_niche` | search by topic → videos + channels + embeddings into the database. `period="24h"` instead of a manual date | 1 search/page out of 100 a day |
| `collect_channel` | a channel's uploads via its uploads playlist; accepts a UC id, @handle, or URL | ~1 unit / 50 videos |
| `collect_trending` | a snapshot of the mostPopular chart (Music/Movies/Gaming) | ~1 unit / page |
| `refresh_stats` | re-read counters and append a snapshot — this is where velocity numbers come from | ~1 unit / 50 videos |
| `refresh_channels` | a snapshot of channel subscribers/views | ~1 unit / 50 channels |
| `refresh_categories` | an up-to-date id → category-name map | 1 unit / region |
| `video_comments` | top-level comments for one video, live, not stored | 1 unit |
| `backfill_embeddings` | embeddings for collected videos that don't have one yet | 0 quota, local compute |

### Sections (free, run as much as you like)

| Tool | What it gives you |
|---|---|
| `viral_videos_small_channels` | viral videos on small channels over a period; VSR, age-adjusted outlier, VPH, acceleration |
| `recently_added_outlier_channels` | the same, at the channel level: multiplier + strength band 0–4 |
| `high_future_competition` | young, fast-growing channels about to become your competitors |
| `most_popular_categories` | category ranking over a period + share shift vs. the previous window; `rank_by="views"` or `"channels"` |
| `trending_keywords` | growing phrases with momentum and outlier-lift |
| `search_outliers` | outlier search over the database, semantically ranked by `query` |
| `top_tags_by_category` | literal YouTube tags, as creators set them, ranked per category by frequency and breakout correlation |
| `check_ideas` | batch-check up to 50 content ideas against the corpus: free / recent / proven / flopped |
| `niche_overview` | niche density: channel-size distribution, viral skew, Shorts share |
| `niche_overview_from_channel` | the same read, anchored on a channel: finds its closest peers via `similar_channels` |
| `niche_videos` | flat per-video list for a niche: date, views, channel, rolling and period outlier, duration |
| `niche_map` | informal niches from k-means over channel embeddings: name, audience, median outlier, velocity, faceless share, competition |
| `similar_channels` / `similar_videos` | semantically closest channels / videos in the local corpus (pgvector when available) |
| `list_niches`, `db_stats` | what's been collected |
| `data_coverage` | whether there's enough data for the requested window — call this first if a section comes back empty |

### Curated tags (theme / trigger / format / ...)

| Tool | What it gives you |
|---|---|
| `tag_videos` | attach your own tags to videos, by group; `manual`/`claude-mcp` tags are never overwritten by `llm`-sourced ones |
| `list_video_tags` | every tag on one video, or every tagged video in a niche |
| `tag_stats` | per-tag videos/hits/hitRate/lift within one tag_group and niche — which angle actually breaks out |
| `list_proposed_tags` / `resolve_proposed_tag` | LLM tags outside the niche's taxonomy, waiting for a human accept/reject |

### Channel tracking and analysis

| Tool | What it gives you |
|---|---|
| `track_channel` / `untrack_channel` / `list_tracked_channels` | a watchlist for history |
| `channel_analytics` | profile, cadence, median vs. mean, viral skew, 24h/7d/30d/90d growth, momentum, grade, projections, two revenue models, top outliers |
| `compare_channels` | comparison, ranked by views per subscriber |
| `channel_velocity` | lifetime VPH, 24h VPH, daily gain, "accelerating / decelerating" |
| `title_changes` | who renamed a video or swapped its thumbnail |
| `best_time_to_publish` | 168 weekly slots by median age-adjusted outlier |
| `title_patterns` | which title phrases correlate with breakouts |
| `calibrate_maturity_curve` | recompute the maturity curve from your own data |

### Alerts (zero quota)

| Tool | What it gives you |
|---|---|
| `scan_for_alerts` | run the alert scan now: new outlier, acceleration, title change, a channel posting again after silence (tracked channels only) |
| `list_events` / `mark_events_seen` | the event feed, optionally unseen-only or one kind |

Delivery to Telegram or a webhook is optional -- see [Configuration](#configuration).

### Titles, metadata and drafts

| Tool | What it gives you |
|---|---|
| `score_titles` | score title candidates 0–100 against the niche/channel's own title patterns + near-duplicate check; works without an LLM |
| `suggest_titles` | generate up to n titles in the style of the best performers, then score them — requires an LLM |
| `review_metadata` | check a draft title/description/tags against your corpus: signals with sample sizes, never one made-up score |
| `save_draft` / `list_drafts` / `link_draft` | keep a draft, then link it to the real video_id after publishing |
| `draft_outcomes` | the review snapshot next to the actual outcome, for linked drafts old enough to have views |

### Swipe file

| Tool | What it gives you |
|---|---|
| `save_item` | save a video or channel, optionally with a snapshot of its metrics at the time |
| `list_saved_items` / `delete_saved_item` | browse (by kind / folder) or remove entries |

### Transcripts (manual paste, stage 19)

| Tool | What it gives you |
|---|---|
| `request_transcript` | queue a video; the text is pasted by hand on the dashboard's transcripts screen — subtitles are never fetched automatically |
| `list_transcript_queue` | the queue, by status `pending` / `ready` / `error` |
| `search_transcripts` | hybrid search (vector + Postgres full-text, RRF-merged) over saved transcript chunks, with timestamped links |

### LLM features (optional, need `LLM_PROVIDER`)

| Tool | What it gives you | Cost |
|---|---|---|
| `comment_insights` | pains / requests / video ideas mined from a video's comments; cached `LLM_INSIGHTS_TTL_DAYS` | 1 unit + LLM call on a cache miss |
| `niche_comment_insights` | merges the cached `comment_insights` of a niche's top videos into one summary | free |
| `explain_outlier` | why a video beat its channel's baseline: hooks, title pattern, timing, formula, confidence; cached `LLM_WHY_VIRAL_TTL_DAYS` | LLM call, 0 quota |
| `enrich_channels` | label channels: faceless, content format, topic, language, ... (the worker also does this) | LLM budget |
| `tag_new_videos` | auto-tag videos in niches whose taxonomy has ≥ `LLM_MIN_MANUAL_TAGS` manual tags | LLM budget |

---

## Configuration

Everything is set in the project-root `.env`; [`.env.example`](../.env.example)
lists every variable with its default and a comment. Only `YOUTUBE_API_KEY` is
required. The optional groups:

| Group | Variables |
|---|---|
| Database | `POSTGRES_*`, `NICHE_DATABASE_URL` (host-only DSN, see above), `NICHE_DB_SCHEMA` (default `public`) |
| Worker schedule | `WORKER_RSS_INTERVAL_MIN`, `WORKER_ALERTS_INTERVAL_MIN`, `WORKER_HOT_*`, `WORKER_EMBED*`, `WORKER_DAILY_INTERVAL_MIN`, `WORKER_FULL_*`, `WORKER_REGIONS`, `WORKER_TRENDING`, `WORKER_QUERIES`, `WORKER_QUERY_*`, `WORKER_ENRICH_*`, `WORKER_CLUSTER_INTERVAL_MIN` |
| LLM (off by default) | `LLM_PROVIDER` (`none` / `openrouter` / `ollama`), `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_MODEL_LONG` (long-context model for comment insights), `OPENROUTER_REFERER`, `OPENROUTER_TITLE`, `OLLAMA_URL`, `OLLAMA_MODEL`, `LLM_DAILY_BUDGET_USD`, `LLM_RELABEL_DAYS`, `LLM_MIN_MANUAL_TAGS`, `LLM_INSIGHTS_TTL_DAYS` (7), `LLM_WHY_VIRAL_TTL_DAYS` (14) |
| Niche clusters | `NICHE_CLUSTERS_MIN_CHANNELS` (10), `NICHE_CLUSTERS_COMPETITION_SUBS` (100000) |
| Alert delivery (off by default) | `NOTIFY_TELEGRAM_BOT_TOKEN`, `NOTIFY_TELEGRAM_CHAT_ID`, `NOTIFY_WEBHOOK_URL`, `NOTIFY_MAX_PER_CYCLE`, `NOTIFY_DASHBOARD_URL` |
| Servers | `WEB_PORT`, `RATE_LIMIT_PER_MINUTE`, `MCP_TRANSPORT` (`stdio`), `MCP_HOST`, `MCP_PORT` |

What leaves the machine when the LLM or alert delivery is on is described in
[PRIVACY.md](../PRIVACY.md).

---

## How to use this

**Day one — build up the corpus.** The cheap path: find 20–50 channels in
your topic and load them via `collect_channel` (that's ~1 unit per 50
videos, doesn't spend search). The expensive path, but necessary for
discovering new topics, is `collect_niche`.

```
collect_niche(query="hypotheses about the brain and memory", label="brain", language="en", period="30d", pages=2)
collect_channel(channel="@some-channel", niche="brain")
```

### Important: what "in the last 24 hours" means

Every section has a `period_by` parameter:

- `"published"` (default) — **what came out** in the window. The normal
  human reading.
- `"discovered"` — **what we first saw** in the window.

This isn't pedantry. NexLev's own screenshots show videos tagged "1 year
ago" in a "Viral Videos On Small Channels — Last 24 hours" list — so their
window is about hitting the index, not the publish date. Both modes are
useful: `published` answers "what's new that came out", `discovered`
answers "what's new that I found". To reproduce NexLev's behavior, pass
`period_by="discovered"`.

**Next — look at the sections.** They're free, run them as much as you like:

```
viral_videos_small_channels(period="24h", max_subscribers=10000, sort_by="viral")
viral_videos_small_channels(period="24h", period_by="discovered")   # like NexLev
recently_added_outlier_channels(period="24h")
most_popular_categories(period="7d", rank_by="channels")
trending_keywords(period="24h", sort_by="trend")
niche_overview(niche="brain")
```

**Ongoing — keep the worker running.** After a day, `vph24h` and
`viewsGained24h` show up; after a week, channel growth and `momentum`;
after a month, `calibrate_maturity_curve()` recomputes the curve for your
niches.

To have topics refresh themselves, set in `.env`:

```
WORKER_QUERIES=ai automation,faceless history,ai for business
WORKER_QUERY_PERIOD=24h
```

Each topic is one search call a day, so up to ~90 topics is safe.

### An empty result explains itself

`viral_videos_small_channels` returns a `funnel` — how many videos survived
each filter — and a `hint` naming exactly which parameter filtered
everything out:

```json
"funnel": [
  {"step": "videos in window",        "remaining": 64},
  {"step": "channel subs <= 10,000",  "remaining": 33},
  {"step": "video views >= 10,000",   "remaining": 25}
],
"hint": null
```

Every other section returns a `hint` when the result is empty. The CLI
additionally prints the hint as its own line.

**If a section comes back empty**, it's almost never that "nothing is
trending" — it's that nothing was collected for that window.
`data_coverage(period=…)` shows the gap.

---

## Formulas

Full details are in `docs/research-tools.md` (internal notes, not included
in this repository); the code is in `metrics.py`. In short:

```
outlierScore        = views / median views of the previous 10 long-form uploads
outlierScoreAdjusted= views / (baseline * maturity(age in days))
outlierScoreNexlev  = views / (channel.viewCount // channel.videoCount)   # to cross-check against NexLev
viewsPerSubscriber  = views / subscribers
viralScore          = 30-day view projection / subscribers
vphLifetime         = views / hours since publish          # this is what NexLev's UI calls "VPH"
vph24h              = (views_now - views_24h_ago) / 24               # needs history
acceleration        = today's vph24h / yesterday's vph24h            # >1.5 = accelerating
momentum            = views per day over 30d / views per day over lifetime
revenue             = monthly views / 1000 * niche RPM * 0.70
```

Median instead of mean is deliberate: NexLev's baseline is the channel's
lifetime mean, and a single viral video wrecks it (the observed
mean-to-median ratio runs as high as 27x).

---

## Structure

As of 2026-09-05 the backend has been rewritten in DDD/Clean Architecture
layers (domain → infrastructure → application → interfaces), but **every
entry point stayed at its old path**: `server.py`, `api.py`, `cli.py`,
`worker.py` in `backend/` are thin shims (a composition root) that just
import the real code from its new home. So `docker compose up`,
`python cli.py ...`, `uvicorn api:app`, and the whole Makefile work
unchanged. The old flat modules (`db.py`, `trends.py`, `query.py`, etc.)
were kept for a while in `backend/_legacy_flat_modules/` as a reference,
but nothing in the code referenced them, so that directory has been removed.

```
youtube-niche-finder/
├── docker-compose.yml      postgres, web, worker, MCP (stdio; HTTP + HTTPS under profile http), ollama (profile llm-local)
├── Makefile                make up / logs / test / seed / stats / dev
├── .env.example            key, worker, LLM and alert settings
├── scripts/mcp-docker.sh   MCP launcher in Docker for Claude Desktop
├── frontend/               dashboard: index.html, styles.css, ui.js, app.js
├── extension/              Chrome extension: overlay on YouTube pages
├── docs/                   internal notes, not included in this repository
└── backend/
    ├── Dockerfile
    ├── server.py           shim: python server.py -> interfaces.mcp.server
    ├── cli.py              shim: python cli.py ...  -> interfaces.cli.cli
    ├── api.py              shim: uvicorn api:app    -> interfaces.http.api
    ├── worker.py           shim: python worker.py   -> application.worker_cycle
    ├── migrate_sqlite_to_postgres.py   one-off migration from the old niches.db
    │
    ├── domain/             pure rules, no external dependencies
    │   ├── metrics.py          every formula (outlier, VPH, revenue, ...)
    │   ├── periods.py          parsing 24h / 7d / 30d / all
    │   ├── keywords.py         n-grams, momentum, lift
    │   ├── scoring.py          backward compatibility (see metrics.py)
    │   ├── categories_catalog.py  pure YouTube categories + offline fallback
    │   ├── tag_stats.py        outlier-hit rate per curated tag
    │   ├── alerts.py           event detection (outlier, acceleration, ...)
    │   ├── metadata.py         metadata-review signals
    │   ├── title_scoring.py    deterministic title scoring
    │   ├── idea_verdicts.py    free / recent / proven / flopped for check_ideas
    │   ├── niche_clusters.py   plain numpy k-means
    │   └── transcripts.py      pasted transcript -> timed chunks
    │
    ├── infrastructure/     adapters to the outside world
    │   ├── postgres/           connection.py, schema.py, repositories.py
    │   │                       (Postgres schema v2, sqlite3-compatible shim)
    │   ├── youtube/            client.py (Data API v3 + quota model), rss.py (free upload feed)
    │   ├── embeddings/fastembed_provider.py  local multilingual embeddings
    │   ├── categories/repository.py          categories, cached in Postgres + YouTube API
    │   ├── llm/                optional LLM: openrouter.py, ollama.py, null.py, factory.py
    │   └── notify/             alert delivery: telegram.py, webhook.py, null.py, factory.py
    │
    ├── application/        use-case orchestration
    │   ├── collecting.py       everything that spends YouTube quota (was collector.py)
    │   ├── discovery.py        the three period-based sections (was trends.py)
    │   ├── channel_tracking.py channel tracking and analysis (was tracking.py)
    │   ├── search.py           outlier search and niche overview (was query.py)
    │   ├── inspection.py       one arbitrary video/channel (the extension's overlay)
    │   ├── tags.py             curated tags and their stats
    │   ├── alerts.py           alert scan + delivery
    │   ├── library.py          swipe file
    │   ├── metadata_review.py  SEO review, drafts and outcomes
    │   ├── llm_gateway.py      budget + caching in front of every LLM call
    │   ├── enrichment.py       AI labeling, comment insights, why-viral
    │   ├── niche_clusters.py   niche map: clustering + LLM naming
    │   ├── niche_export.py     niche videos to TSV/CSV
    │   ├── transcripts.py      manual transcript queue and hybrid search
    │   └── worker_cycle.py     the background collector's loop (was worker.py)
    │
    ├── interfaces/         thin adapters facing outward
    │   ├── mcp/server.py       MCP server, 61 tools
    │   ├── http/api.py         HTTP API for the dashboard and extension (FastAPI)
    │   ├── cli/cli.py          same, from the terminal, plus doctor (diagnostics)
    │   └── worker/main.py      background collector's entry point
    │
    └── tests/              one file per feature, smoke tests and the synthetic seed
```

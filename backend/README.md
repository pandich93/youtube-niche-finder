# niche-finder — backend

[![CI](https://img.shields.io/github/actions/workflow/status/pandich93/youtube-niche-finder/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/pandich93/youtube-niche-finder/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fpandich93%2Fyoutube-niche-finder%2Fbadges%2Fcoverage.json&query=%24.totals.percent_covered_display&suffix=%25&label=coverage&style=flat-square)](https://github.com/pandich93/youtube-niche-finder/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white)](#running-without-docker)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](interfaces/http/api.py)
[![PostgreSQL 16](https://img.shields.io/badge/postgres-16-336791?style=flat-square&logo=postgresql&logoColor=white)](../docker-compose.yml)
[![MCP](https://img.shields.io/badge/MCP-80%20tools-8A2BE2?style=flat-square)](interfaces/mcp/server.py)

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

The HTTP API (`interfaces/http/api.py`) has no login, so it guards itself
against other websites open in the same browser. It answers only when the
`Host` header is `127.0.0.1`, `localhost`, `[::1]` or the compose service
name `web` (any port) -- anything else gets `400`, which stops DNS rebinding;
add your own names with `NF_ALLOWED_HOSTS` (comma-separated). Every `POST`,
`PUT`, `PATCH` and `DELETE` must carry `Content-Type: application/json` or an
`X-NF-Client` header (any value), otherwise `403`: both force the browser into
a CORS preflight, which only `chrome-extension://` origins pass. The dashboard
sends `Content-Type: application/json` on every request; a script needs one too:
`curl -X POST -H 'X-NF-Client: script' http://127.0.0.1:8080/api/events/scan`.
The built-in Swagger UI at `/api/docs` sends neither header on routes without
a body (`events/scan`, `enrich/*`, `reindex`, `recompute`, `DELETE
/api/channels/tracked/...`), so "Try it out" gets `403` there; call those with
curl as above. Routes with a JSON body work from Swagger as usual.

The key is only needed at runtime, not at build time: `docker compose build`
works fine with an empty `.env`. If there's no key, the worker says so and
exits, while the read tools keep working off whatever's already collected.

The worker isn't optional, it's a requirement: the YouTube API only ever
returns "how many views right now". View-gain speed, acceleration,
subscriber growth, period comparisons, and thumbnail/title-change detection
only exist because something is regularly recording the numbers. That's the
worker's job.

It also keeps old rows fresh (plan 16): once a day it re-reads, with
`videos.list` / `channels.list`, every stored video and channel not refreshed
for `REFRESH_STALE_DAYS` (25) -- titles, descriptions and counters -- oldest
first, at most `REFRESH_STALE_MAX_VIDEOS` / `REFRESH_STALE_MAX_CHANNELS` (2,500
each, ~100 units a day at most). YouTube's API policies allow keeping data
fetched with a key at most 30 days unrefreshed. Nothing is ever deleted: a row
the API stopped returning is marked gone, and the history stays (see
SECURITY.md for what that means under the policies). `db_stats` reports the
counts under `freshness`.

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
               "-e", "NICHE_DATABASE_URL=",
               "-e", "POSTGRES_HOST=postgres",
               "-v", "/path/to/youtube-niche-finder/backend:/app:ro",
               "-v", "niche-finder-models:/models",
               "niche-finder:latest", "python", "server.py"]
    }
  }
}
```

Use the absolute path to `docker` (`which docker`), not just `"docker"` —
Claude Desktop's MCP launcher doesn't always inherit your shell's `PATH`.

`-e NICHE_DATABASE_URL=` matters once `.env` has the host DSN for `make
local-run` (`localhost:5433`): it outranks `POSTGRES_HOST`, and inside the
container `localhost` is the container itself, so without the blank the server
exits with "Connection refused" and Claude Desktop shows it as disconnected
(compose blanks it the same way for `web` and `worker`). The `backend:/app:ro`
mount runs the repository's current code, so an update needs only a Claude
Desktop restart, not an image rebuild. `--env-file` takes values literally:
keep them unquoted in `.env`.

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

### Multi-user mode (plan 15, experimental, off by default)

Checklist before you give anyone an account: [SECURITY.md](../SECURITY.md).

`NF_MULTI_USER=1` turns on sign-in. Accounts are by invitation — there is no
sign-up page:

```bash
make cli ARGS="create-user ann@example.com --admin"   # asks for the password twice
make cli ARGS="set-password local"                    # your existing data belongs to "local"
make cli ARGS="users"
```

Passwords are scrypt hashes (standard library), at least 10 characters. A
session is a random token in the `nf_session` cookie (HttpOnly,
SameSite=Strict, `Secure` with `NF_COOKIE_SECURE=1` or over HTTPS, 30 days);
the database keeps only its SHA-256, and changing a password ends every
session. Without a session `/api` answers 401 except sign-in, `/api/health` and
the Google OAuth return. Every personal table has a `user_id` (existing rows
moved to user 1, "local").

Per user (sub-stage 5.4): the watchlist, the swipe file, drafts, the
transcript queue, alert read marks and "Мои каналы". Shared by everyone:
public YouTube data (channels, videos, niches, pasted transcripts) and alert
events — a user sees the events of the channels on their own watchlist; the
worker refreshes each tracked channel once, whoever tracks it. MCP over stdio
acts as the local user.

YouTube quota (sub-stages 5.5–5.6): one API key serves the installation, as
the YouTube API policies require one API project per application (III.D.1.c),
and the worker refreshes shared data with it. Each signed-in user spends a
daily share of it — `NF_USER_DAILY_UNITS` (2000) and
`NF_USER_DAILY_SEARCH_CALLS` (20 of the 100 daily searches), 0 = no limit. The
YouTube client checks the share before every call and counts it after; running
out answers 429 to that user only. The dashboard footer shows what is left.

Personal API tokens (sub-stages 5.7–5.8) sign in clients that cannot hold the
cookie: the browser extension (its "Токен доступа" setting) and MCP over HTTP
(`Authorization: Bearer nf_...`). Create one on the dashboard's MCP screen while
signed in, or `make cli ARGS="create-token ann@example.com --name laptop"`. Only
the SHA-256 is stored, the plaintext is shown once, a token cannot create or
list tokens, and it acts as its user — their data and their YouTube budget.
With `NF_MULTI_USER=1`, the HTTP MCP services (`mcp-http`/`mcp-https`) require
such a token (401 without one; set `MCP_PUBLIC_URL` to the address clients use);
MCP over stdio stays local and acts as user 1.

Notifications (sub-stage 5.9): each user sets their own Telegram bot and chat
or webhook and the mode — instant, digest, both or off — on the dashboard's
"Данные" screen (`/api/settings/notifications`). Alerts and the digest cover
only that user's watchlist, and "already delivered" is kept per user. The bot
token and webhook address are encrypted with `OWN_TOKENS_KEY` and never read
back; a user's webhook must be https to a public address, checked when saved
and again before every send (no requests into your own network). The local
user without saved settings keeps `NOTIFY_*` from `.env`.

Limits per user (sub-stage 5.10): the request rate limit
(`RATE_LIMIT_PER_MINUTE`) counts per signed-in user instead of per address,
and each user may spend `NF_USER_DAILY_LLM_USD` (0.25, 0 = no limit) of LLM
money a day inside the installation-wide `LLM_DAILY_BUDGET_USD`; cached answers
cost nothing. LLM usage is recorded per user (`llm_usage.user_id`).

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
make cli ARGS="digest-send"                       # send the daily digest right now
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
python3 -m venv .venv && source .venv/bin/activate   # Python 3.10+; Docker and CI use 3.12
pip install -r requirements.txt
cp ../.env.example ../.env    # fill in YOUTUBE_API_KEY; the .env lives in the project root
python3 tests/test_smoke.py   # should be 17/17 passed -- needs a reachable
                              # Postgres (docker compose up -d postgres, or
                              # your own; see infrastructure/postgres/connection.py)
```

`requirements.txt` pins exact versions of every transitive dependency; the version ranges live in
`requirements.in` (edit them there). Re-pin to the newest allowed versions with
`uv pip compile requirements.in --python-version 3.10 --universal -o requirements.txt --upgrade`
(3.10 is the oldest Python `make local-install` accepts; per-version markers let the same file serve
3.10, 3.11 and the 3.12 used by Docker and CI). Local installs need Linux or an Apple Silicon Mac: the pinned
`onnxruntime` ships no Intel-Mac wheels for Python 3.11+, so use Docker there.

Each test process works in its own `nichetest_<random>` schema and drops it on
exit (`tests/schema_scope.py`), so repeated runs leave nothing behind. Under
pytest every test file gets a fresh schema of its own on top of that
(`nichetest_<random>_m<N>`, dropped after the file -- see `tests/conftest.py`),
so `pytest tests/` in one process sees the same data per file as running the
files one by one. Set `NICHE_KEEP_TEST_SCHEMA=1` to keep the schemas and
inspect the data after a failure; a schema you pass in yourself via
`NICHE_DB_SCHEMA` is never dropped (and is then shared by all files).

"Reachable Postgres" means a reachable *host* address, and that is not the
default one: the compose database is published on `127.0.0.1:5433`
(`POSTGRES_HOST_PORT`), while `_dsn()` falls back to `localhost:5432` --
hence `psycopg2.OperationalError: Connection refused` straight out of
`init_db()`. Put the host DSN in the project-root `.env`:

```bash
NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5433/niches
```

It wins over `POSTGRES_*` in `_dsn()` and stays host-only on purpose --
compose loads `.env` into containers but blanks this one there, and `scripts/mcp-docker.sh` /
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
| `niche_overview` | niche density: channel-size distribution, viral skew, Shorts share; `saturation_v2` -- the trend over its own 120-day window (last 30 days vs the 90 before, whatever `period`): status growing / stable / cooling / saturated / insufficient-data (<20 videos per window), signals supply, demand (median views projected to day 30), entrants (channels created in the window), newcomers breaking out, each with its numbers; `confidence: low` when few videos were seen young. Zero quota. `GET /api/niches/saturation` gives every niche's status in one pass |
| `niche_overview_from_channel` | the same read, anchored on a channel: finds its closest peers via `similar_channels` |
| `niche_videos` | flat per-video list for a niche: date, views, channel, rolling and period outlier, duration |
| `niche_map` | informal niches from k-means over channel embeddings: name, audience, median outlier, velocity, faceless share, competition, and each cluster's `saturation` trend (shown, not used for the order) |
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
| `channel_analytics` | profile, cadence, median vs. mean, viral skew, 24h/7d/30d/90d growth, momentum, grade, projections, two revenue models, top outliers; `yppEligibility` -- which YouTube Partner Program thresholds it visibly meets (500/1,000 subscribers, uploads and Shorts views in 90 days as a lower bound from collected videos; watch hours are not in the API). Not a monetization status: YouTube does not publish one. Until 2027-02-01 it also carries `upcoming` -- the same check under the rules from that date (8,000 watch hours in 365 days or 20M Shorts views in 90 days, plus the activity bar: 2 long videos or 5 Shorts or 1M Shorts views in 90 days); `milestones` -- when the next subscriber milestones come at the pace of the last 30 and 90 days of snapshots (an estimate, not YouTube data) |
| `compare_channels` | comparison, ranked by views per subscriber |
| `template_risk` | how templated a channel's last N uploads look, 0-100: title similarity, shared openings/endings, uniform lengths, metronome rhythm; Shorts and long-form never mixed; needs 10+ videos; a heuristic, not YouTube's verdict |
| `niche_template_risk` | the same for every channel in a niche: low/medium/high counts, share of high-risk channels, the most templated ones |
| `channel_velocity` | lifetime VPH, 24h VPH, daily gain, "accelerating / decelerating" |
| `title_changes` | who renamed a video (thumbnail swaps are invisible to the API -- see `packaging_changes`) |
| `sponsor_map` | `niche, period="all", top_n=10` -- share of a niche's videos with a named sponsor or promo code, top brands (videos, channels, last seen, examples), affiliate brands apart, views with vs without a sponsor; a lower bound (only what is written in descriptions); zero quota |
| `channel_sponsors` | `channel_id, period="all", top_n=10` -- the same for one channel |
| `packaging_changes` | title and thumbnail swaps with before/after and views per hour 48h before vs after; thumbnails come from the worker's image fingerprints of tracked channels; `video_id` for one video's full history |
| `best_time_to_publish` | 168 weekly slots by median age-adjusted outlier |
| `title_patterns` | which title phrases correlate with breakouts |
| `calibrate_maturity_curve` | recompute the maturity curve from your own data |

### Alerts (zero quota)

| Tool | What it gives you |
|---|---|
| `scan_for_alerts` | run the alert scan now: new outlier, acceleration, title change, a channel posting again after silence, a channel or an alerted outlier video that the API stopped returning, a channel passing a subscriber milestone (100, 1k, 10k, ...) between its two latest snapshots (tracked channels only; "gone" needs two misses at least 6h apart, and a failed or over-quota API call never counts as a miss) |
| `list_events` / `mark_events_seen` | the event feed, optionally unseen-only or one kind |
| `watch_topic` / `list_watched_topics` / `unwatch_topic` | topic alerts (plan 19): name a topic in plain words; every video first seen in the last 48 hours (RSS, niche collections, trending -- whatever the database collects, not all of YouTube) whose embedding has cosine >= threshold (0.6) raises a personal `topic_match` event that only you see and get. Zero quota |
| `daily_digest` | the last 24h in one summary -- new outliers, accelerating videos, rising channels, title/thumbnail swaps, disappeared channels/videos -- without sending it |

Delivery to Telegram or a webhook is optional -- see [Configuration](#configuration).

### Titles, metadata and drafts

| Tool | What it gives you |
|---|---|
| `score_titles` | score title candidates 0–100 against the niche/channel's own title patterns + near-duplicate check; works without an LLM |
| `suggest_titles` | generate up to n titles in the style of the best performers, then score them — requires an LLM |
| `review_metadata` | check a draft title/description/tags against your corpus: signals with sample sizes, never one made-up score |
| `save_draft` / `list_drafts` / `link_draft` | keep a draft, then link it to the real video_id after publishing |
| `build_brief` | `gap_topic` (from `content_gaps`) makes the overlap check and title candidates about that viewer question. One outlier -> a brief for your own video: hook (first ~75 words of a pasted transcript), why it worked (LLM), niche title patterns and best time, whether the topic is already covered (source excluded), title candidates (LLM) and thumbnail references; unavailable parts are listed in `skipped`; `save=true` stores a draft (`drafts.source_video_id`) and queues a missing transcript, `save=false` writes nothing |
| `policy_signals` / `niche_policy_signals` | plan 22: signals a channel shows, from public data, for the three "inauthentic content" categories of YouTube's monetization policies -- generic_repetitive (template risk, look-alike thumbnails), unsatisfying (share of shock-marker titles), ai_persona_sensitive (health / legal / finance / politics topics plus a synthetic-media disclosure or a faceless label, never above "watch"); levels with reasons, examples and the policy text, never one risk percentage. For a niche: how many channels are high / watch per category. Zero quota |
| `explain_scores` | plan 23: what a number is -- YouTube data or an estimate of niche-finder (`source`), its formula, inputs and minimum sample; one key or the whole catalog (`domain/score_catalog.py`, the same text as the dashboard's "?" tips and the help page) |
| `video_trajectory` | plan 20: views by age (hours since publishing) for 1-5 videos from the worker's snapshots, each with its channel's expected curve (median views x maturity curve) and marks for title/thumbnail swaps; `observedFromHours` says from when a late-found video is watched. Zero quota |
| `format_repeatability` | plan 21: did this video's format work for OTHER channels too? Its embedding neighbours on other channels (cosine >= `min_similarity`, 0.6), each scored with the usual outlier baseline, one channel counted once by its best video: `repeatable` (3+ channels got >= 2x), `mixed`, `one_off`, or `unknown` (fewer than 5 similar videos or 3 channels collected). Plus how many other channels start their titles the same way. Zero quota |
| `draft_outcomes` | the review snapshot next to the actual outcome, for linked drafts old enough to have views |

### Thumbnails (plan 13, zero quota)

CLIP vectors of thumbnails (`infrastructure/embeddings/image_provider.py`, ONNX on
CPU): the worker fills them with `WORKER_THUMB_EMBED=1` (off by default), or
`embed_thumbnails` does it on demand. Thumbnails come from the plan-05 archive or
`i.ytimg.com` (not the Data API); only the 512-d vector is kept, in
`videos.thumb_embedding` and `thumb_embedding_v` (HNSW) when pgvector is there. A
thumbnail swap re-embeds the video. Disk: ~0.34 GB image model + ~0.25 GB text
model in the models volume; the process peaks at ~0.7 GB RAM while embedding;
~60 ms of CPU per thumbnail, ~1 s with the download and its polite pause.

| Tool | What it gives you |
|---|---|
| `similar_thumbnails` | videos whose thumbnail looks most like this one's (optionally one niche, other channels only) |
| `search_thumbnails` | thumbnails matching a short visual description, e.g. "red arrow, shocked face" |
| `thumbnail_styles` | a niche's thumbnails in k-means style groups with videos, share, median outlier score and views, best examples (12+ vectors needed) |
| `embed_thumbnails` | vectors for up to `limit` thumbnails without one, newest first or one niche's |

CLIP compares style and content, not the words written on a thumbnail.

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
| `hook_report` | `video_id, niche?, llm=false, force_refresh=false` -- score 0-100 of the first ~30 s of a saved transcript (text only): features hit, filler penalties, up to 3 tips; optional niche comparison. `llm=true` sends the intro text to the LLM provider (costs money, cached 30 days in `video_insights`, task `hook`). Zero quota |
| `niche_hook_benchmark` | `niche` -- hooks of a niche's outliers (outlierScore >= 3) vs ordinary videos (< 1.5): mean scores, features more common in outliers; `insufficient-data` below 10 transcripts per group. Zero quota |
| `score_hook_text` | `text, niche?` -- score a draft intro against the rules and a niche benchmark; the draft is never stored, logged or sent to an LLM. Zero quota |
| `search_transcripts` | hybrid search (vector + Postgres full-text, RRF-merged) over saved transcript chunks, with timestamped links |

### LLM features (optional, need `LLM_PROVIDER`)

| Tool | What it gives you | Cost |
|---|---|---|
| `comment_insights` | pains / requests / video ideas mined from a video's comments; cached `LLM_INSIGHTS_TTL_DAYS` | 1 unit + LLM call on a cache miss |
| `niche_comment_insights` | merges the cached `comment_insights` of a niche's top videos into one summary | free |
| `content_gaps` | `niche, top_videos=10, use_llm=None, fetch=False, limit=20` -- viewer questions/requests from the comments of a niche's top videos that no collected video or pasted transcript covers: status free/partial, demand, example comments, source videos, nearest video/transcript, `skippedVideos` with reasons and `coverageBase`. Works without an LLM too (rules, cached as `comment_questions` in `video_insights`: question text and like count only, no authors) | `fetch=false`: free (cache only); `fetch=true`: 1 unit per uncached video (+ an LLM call each in LLM mode) |
| `explain_outlier` | why a video beat its channel's baseline: hooks, title pattern, timing, formula, confidence; cached `LLM_WHY_VIRAL_TTL_DAYS` | LLM call, 0 quota |
| `enrich_channels` | label channels: faceless, content format, topic, language, ... (the worker also does this) | LLM budget |
| `tag_new_videos` | auto-tag videos in niches whose taxonomy has ≥ `LLM_MIN_MANUAL_TAGS` manual tags | LLM budget |

### Scenarios (MCP prompts)

Ready-made workflows in [`interfaces/mcp/prompts.py`](interfaces/mcp/prompts.py).
A client lists them (Claude Desktop: "+" → niche-finder); picking one puts a
step-by-step instruction into the chat, in Russian, naming the tools above in
order. The client's model runs the steps; the prompt itself costs nothing.
Scenarios that collect call `db_stats` first and stop when the quota is short.
`tests/test_mcp_prompts.py` fails if a scenario names a tool that no longer exists.

| Prompt | Arguments | Steps | Quota |
|---|---|---|---|
| `find_niche` | `topic`, `pages=1` (1-3) | `list_niches` → `db_stats` → `collect_niche` (only if not collected) → `niche_overview` → `viral_videos_small_channels` → `niche_template_risk` | `pages` of the 100 daily searches, only if not collected |
| `analyze_competitor` | `channel` | `db_stats` → `collect_channel` → `channel_analytics` → `title_patterns` → `best_time_to_publish` → `similar_channels` | ~2-3 units |
| `validate_idea` | `idea`, `niche` | `check_ideas` → `score_titles` → `high_future_competition` | 0 |
| `outlier_to_video` | `video_id`, `use_llm=yes` | `build_brief` (preview, saved only on consent) | 0 |
| `weekly_review` | `period=7d` | `daily_digest` → `list_events` → `packaging_changes` → `recently_added_outlier_channels` | 0 |
| `find_content_gaps` | `niche` | `content_gaps` from cache → on consent `db_stats` + `content_gaps(fetch=true)` → `build_brief(gap_topic)` | 0, or 1 unit per unread video |
| `niche_health` | `niche` | `niche_overview` → `niche_template_risk` → `sponsor_map` → `niche_hook_benchmark` | 0 |

With the Docker setup above (or `scripts/mcp-docker.sh`) the code is mounted
from the repository, so new scenarios appear after a Claude Desktop restart.
Only a config without the `backend:/app:ro` mount needs `docker compose build`.

### Your own channels (plan 14, OAuth)

Connect your own channels to see their real YouTube Analytics numbers next to
the niche: views, watch time, retention (average view percentage),
subscribers gained and, on a monetized channel, revenue, CPM and RPM (revenue
per 1,000 views, as in YouTube Studio). Impressions and thumbnail
click-through rate are not in the Analytics API — only YouTube Studio shows
them.

One-time setup (each user brings their own OAuth client; nothing is shared):

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project
   and enable **YouTube Data API v3** and **YouTube Analytics API**.
2. OAuth consent screen: user type External, publishing status **Testing**, add
   your own Google account as a test user. Testing mode is enough for yourself
   (Google does not verify apps used only by their test users); consent in
   testing mode expires after 7 days, so reconnect when a sync reports
   `invalid_grant`.
3. Credentials → Create credentials → OAuth client ID → **Desktop app**. Put
   the Client ID and secret into `.env`:
   `OWN_OAUTH_CLIENT_ID=...` and `OWN_OAUTH_CLIENT_SECRET=...`.
4. Generate the key that encrypts the stored refresh token and put it into
   `.env` as `OWN_TOKENS_KEY=...`:
   `docker compose run --rm web python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   Keep it only in `.env`; losing or changing it means reconnecting.
5. `docker compose up -d web worker`, open the dashboard → **Мои каналы** →
   **Подключить канал**, sign in and allow read-only access.

**Running it as a service (plan 25, `OWN_OAUTH_MODE=web`).** Customers do not
create OAuth clients: the service has one, of type **Web application**, and a
customer only clicks "Подключить канал" and allows read-only access in
Google's consent window. Set `OWN_OAUTH_MODE=web` and
`OWN_OAUTH_REDIRECT_URI=https://<your domain>/api/own/oauth/callback`
(registered as an authorized redirect URI of the client; the status refuses
anything but https or a localhost dev address). In multi-user mode only an
admin sees which settings are missing; customers see that connecting is not
available yet. Revenue (`yt-analytics-monetary.readonly`) is asked for only
when the customer ticks "показывать доход" or later presses "Добавить доход"
-- the base consent is `youtube.readonly` + `yt-analytics.readonly`. The
service serves public `/privacy` and `/terms` pages (Limited Use disclosure,
YouTube Terms of Service, Google Privacy Policy, revoking access, deletion)
filled from `NF_SERVICE_NAME`, `NF_OPERATOR_NAME` and `NF_CONTACT_EMAIL`. What
stays outside the code: Google's OAuth app verification (until it passes,
only up to 100 test users can connect and Google shows "unverified app"),
YouTube's API compliance audit for more Data API quota, and the 30-day rule
for public YouTube data (SECURITY.md, item 5).

Google sends you back to `/api/own/oauth/callback` on the loopback address the
dashboard is open on (`localhost:8080` or `127.0.0.1:8080`; set
`OWN_OAUTH_REDIRECT_URI` to pin one); a Desktop app client accepts loopback
addresses, so there is nothing to register. Finish the sign-in in the same
browser you started it in: the start sets a short-lived cookie that the return
must match, so a consent link opened anywhere else is refused and cannot attach
someone else's channel to your account. The
consent asks only for read-only scopes (`youtube.readonly`,
`yt-analytics.readonly`, `yt-analytics-monetary.readonly`); the flow uses PKCE
and a single-use state that expires in 10 minutes.

The refresh token is stored Fernet-encrypted (`own_channels.token_enc`) and is
never returned by the API, logged or put in an error. The worker syncs
connected channels once a day (`WORKER_OWN_SYNC`), per video, for the last 28
days and lifetime, ending 3 days ago because Analytics data arrives late; this
uses your OAuth project's Analytics quota, not the Data API key's. **Отключить**
revokes the token at Google (best effort) and deletes everything stored for the
channel; access can also be removed at https://myaccount.google.com/permissions.

| Tool | What it gives you |
|---|---|
| `own_channels` | connection status and your channels with their last-28-day views, revenue, RPM, median retention |
| `own_vs_niche` | your videos' real lifetime views and retention against a niche's collected videos |
| `own_channel_formats` | plan 24: your connected channel by format (Analytics `creatorContentType`): views, watch minutes, new subscribers and subscribers per 1,000 views for Shorts / long / live over 90 days, weekly series, whether Shorts and long-form views move together (Pearson r over 8+ weeks, a correlation), and long + live watch hours in 365 days toward the YPP bar (4,000; 8,000 from 2027-02-01) with the date at the recent pace -- approximate, not YouTube's qualified hours |
| `rpm_calibration` | your real 28-day RPM next to the low / mid / high range niche-finder estimates from public data |
| `sync_own_channels` | pull fresh Analytics numbers now (the worker does it daily) |

`draft_outcomes` also shows the real numbers (`ownMetrics`) of a linked draft's
video when it is on a connected channel.

---

## Configuration

Everything is set in the project-root `.env`; [`.env.example`](../.env.example)
lists every variable with its default and a comment. Only `YOUTUBE_API_KEY` is
required. The optional groups:

| Group | Variables |
|---|---|
| Database | `POSTGRES_*`, `NICHE_DATABASE_URL` (host-only DSN, see above), `NICHE_DB_SCHEMA` (default `public`) |
| Worker schedule | `WORKER_RSS_INTERVAL_MIN`, `WORKER_ALERTS_INTERVAL_MIN`, `WORKER_HOT_*`, `WORKER_EMBED*`, `WORKER_DAILY_INTERVAL_MIN`, `WORKER_FULL_*`, `WORKER_REGIONS`, `WORKER_TRENDING`, `WORKER_QUERIES`, `WORKER_QUERY_*`, `WORKER_ENRICH_*`, `WORKER_CLUSTER_INTERVAL_MIN`, `WORKER_THUMBS` (1), `WORKER_THUMBS_INTERVAL_MIN` (360), `WORKER_THUMBS_LIMIT` (500), `WORKER_SPONSORS` (1), `WORKER_SPONSORS_INTERVAL_MIN` (60), `WORKER_SPONSORS_LIMIT` (5000) |
| LLM (off by default) | `LLM_PROVIDER` (`none` / `openrouter` / `ollama`), `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_MODEL_LONG` (long-context model for comment insights), `OPENROUTER_REFERER`, `OPENROUTER_TITLE`, `OLLAMA_URL`, `OLLAMA_MODEL`, `LLM_DAILY_BUDGET_USD`, `LLM_RELABEL_DAYS`, `LLM_MIN_MANUAL_TAGS`, `LLM_INSIGHTS_TTL_DAYS` (7), `LLM_WHY_VIRAL_TTL_DAYS` (14), `LLM_HOOK_TTL_DAYS` (30) |
| Niche clusters | `NICHE_CLUSTERS_MIN_CHANNELS` (10), `NICHE_CLUSTERS_COMPETITION_SUBS` (100000) |
| Alert delivery (off by default) | `NOTIFY_TELEGRAM_BOT_TOKEN`, `NOTIFY_TELEGRAM_CHAT_ID`, `NOTIFY_WEBHOOK_URL`, `NOTIFY_MAX_PER_CYCLE`, `NOTIFY_DASHBOARD_URL`, `NOTIFY_MODE` (`instant` / `digest` / `both`), `DIGEST_HOUR` (8, container time zone), `DIGEST_SKIP_EMPTY` (1), `WORKER_DIGEST_CHECK_INTERVAL_MIN` (10) |
| Servers | `WEB_PORT`, `RATE_LIMIT_PER_MINUTE`, `NF_ALLOWED_HOSTS` (extra `Host` names for the HTTP API), `MCP_TRANSPORT` (`stdio`), `MCP_HOST`, `MCP_PORT` |

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
after about a month, the worker switches age-adjusted scores over to a
maturity curve measured on your own niches (`calibrate_maturity_curve()`
shows how close it is; `db_stats` shows which curve is in use).

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
rpm range           = that effective RPM / 2 ... * 2    # published estimates disagree by up to 7x
```

Median instead of mean is deliberate: NexLev's baseline is the channel's
lifetime mean, and a single viral video wrecks it (the observed
mean-to-median ratio runs as high as 27x).

**The 2026-08-24 view-count change.** Since then YouTube counts a public view
the moment a video starts playing (Data API revision history, 2026-08-27).
Snapshots on either side of that date were counted by different rules, so
`vph24h` and `acceleration` never pair a snapshot from before it with one
from after it, `calibrate_maturity_curve` leaves out videos whose history
straddles it, and the channel chart marks the date. The outlier baseline
still compares videos published before and after the change: whether that
skews it is not measured yet -- a database with snapshots from before
2026-08-24 is needed to check.

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
├── frontend/               dashboard: index.html, styles.css, ui.js, app.js, router.js, shared.js, screens/
├── extension/              Chrome extension: overlay on YouTube pages
├── docs/                   internal notes, not included in this repository
└── backend/
    ├── Dockerfile
    ├── server.py           shim: python server.py -> interfaces.mcp.server
    ├── cli.py              shim: python cli.py ...  -> interfaces.cli.cli
    ├── api.py              shim: uvicorn api:app    -> interfaces.http.api
    ├── worker.py           shim: python worker.py   -> application.worker_cycle
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
    │   ├── packaging.py        thumbnail fingerprint distance, before/after views effect
    │   ├── sponsors.py         sponsor / promo-code / affiliate extraction from descriptions, brand normalisation
    │   ├── template_risk.py    title similarity / shared skeleton / length + cadence -> template score
    │   ├── saturation.py       niche trend: supply, demand, entrants, newcomers -> growing / stable / cooling / saturated
    │   ├── hook_scoring.py     rule-based hook score, hook text extraction, niche aggregation
    │   ├── content_gaps.py     question picking from comments, grouping, demand vs coverage
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
    │   ├── thumbnails.py       thumbnail download (i.ytimg.com, no quota) + dHash fingerprint
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
    │   ├── digest.py           once-a-day summary to Telegram/webhook
    │   ├── library.py          swipe file
    │   ├── metadata_review.py  SEO review, drafts and outcomes
    │   ├── llm_gateway.py      budget + caching in front of every LLM call
    │   ├── enrichment.py       AI labeling, comment insights, why-viral
    │   ├── niche_clusters.py   niche map: clustering + LLM naming
    │   ├── niche_export.py     niche videos to TSV/CSV
    │   ├── packaging.py        thumbnail fingerprinting, repackaging feed and history
    │   ├── sponsors.py         sponsor scan (worker step + backfill), sponsor_map, channel_sponsors
    │   ├── template_risk.py    per-channel and per-niche template risk
    │   ├── saturation.py       niche trend over the last 120 days: one niche, a channel set, all niches
    │   ├── hook_score.py       hook_report, niche_hook_benchmark, score_hook_text (LLM review cached in video_insights)
    │   ├── briefs.py           outlier -> brief for your own video (+ draft)
    │   ├── content_gaps.py     viewer questions from comments vs what the niche already covers
    │   ├── transcripts.py      manual transcript queue and hybrid search
    │   └── worker_cycle.py     the background collector's loop (was worker.py)
    │
    ├── interfaces/         thin adapters facing outward
    │   ├── mcp/server.py       MCP server, 89 tools
    │   ├── mcp/prompts.py      7 ready-made scenarios (MCP prompts)
    │   ├── http/api.py         HTTP API for the dashboard and extension (FastAPI)
    │   ├── cli/cli.py          same, from the terminal, plus doctor (diagnostics)
    │   └── worker/main.py      background collector's entry point
    │
    └── tests/              one file per feature, smoke tests and the synthetic seed
```

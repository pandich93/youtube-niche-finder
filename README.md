# niche-finder

[![Stars](https://img.shields.io/github/stars/pandich93/youtube-niche-finder?style=flat-square)](https://github.com/pandich93/youtube-niche-finder/stargazers)
[![Forks](https://img.shields.io/github/forks/pandich93/youtube-niche-finder?style=flat-square)](https://github.com/pandich93/youtube-niche-finder/forks)
[![Release](https://img.shields.io/github/v/release/pandich93/youtube-niche-finder?style=flat-square)](https://github.com/pandich93/youtube-niche-finder/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/pandich93/youtube-niche-finder/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/pandich93/youtube-niche-finder/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fpandich93%2Fyoutube-niche-finder%2Fbadges%2Fcoverage.json&query=%24.totals.percent_covered_display&suffix=%25&label=coverage&style=flat-square)](https://github.com/pandich93/youtube-niche-finder/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/pandich93/youtube-niche-finder?style=flat-square)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square&logo=python&logoColor=white)](backend/Dockerfile)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](backend/interfaces/http/api.py)
[![PostgreSQL 16](https://img.shields.io/badge/postgres-16-336791?style=flat-square&logo=postgresql&logoColor=white)](docker-compose.yml)
[![Docker Compose](https://img.shields.io/badge/docker-compose-2496ED?style=flat-square&logo=docker&logoColor=white)](docker-compose.yml)
[![MCP](https://img.shields.io/badge/MCP-80%20tools-8A2BE2?style=flat-square)](backend/interfaces/mcp/server.py)
[![Last commit](https://img.shields.io/github/last-commit/pandich93/youtube-niche-finder?style=flat-square)](https://github.com/pandich93/youtube-niche-finder/commits/main)
[![Open issues](https://img.shields.io/github/issues/pandich93/youtube-niche-finder?style=flat-square)](https://github.com/pandich93/youtube-niche-finder/issues)
[![Open PRs](https://img.shields.io/github/issues-pr/pandich93/youtube-niche-finder?style=flat-square)](https://github.com/pandich93/youtube-niche-finder/pulls)

A self-hosted alternative to NexLev / vidIQ / ViewStats: find niches, viral
videos from small channels, trending categories and keywords over arbitrary
periods (24h, 48h, 7/30/90 days), plus full channel tracking and analytics.
Runs on the free YouTube Data API v3 and a local PostgreSQL — no
subscription, no paid LLM key: semantic classification like "faceless / on
topic" is done by the model calling these tools, not the server.

![niche-finder dashboard: overview — channels, videos, outlier channels, trending categories and keywords](assets/dashboard.jpg)

The project has three parts that together make up the "product":

- **`backend/`** — Python: an MCP server (80 tools for Claude), an HTTP API
  for the dashboard, and a background worker that logs view/subscriber
  history on a schedule (without this, "growth rate over 24 hours" doesn't
  exist — the YouTube API only ever returns "right now").
- **`frontend/`** — the same functionality, but visual: a dashboard built on
  plain ES modules (no npm, no build step) that calls the backend's HTTP API.
- **`extension/`** — a Chrome extension (Manifest V3) that puts the same
  metrics on top of YouTube itself, the way vidIQ and NexLev do: outlier
  score, view velocity and tags on a watch page, growth and best publishing
  times on a channel page, multiplier badges on thumbnails in any list. It
  talks only to `127.0.0.1` — nothing leaves the machine.

Both parts and the Postgres store come up together with one command (see
below) — the dashboard and Claude Desktop end up looking at the same
database.

## Features

- Find viral videos and outlier channels by niche over an arbitrary period
  (24h / 48h / 7 / 30 / 90 days)
- Trending categories and keywords, best time to publish, title patterns
- Track specific channels: view/subscriber growth rate, snapshot history
- Repackaging: title and thumbnail swaps after publishing, before/after
  side by side and views per hour around the swap (thumbnails are
  fingerprinted from the image itself — the API URL never changes)
- Hook score: rates the text of a video's first ~30 seconds (a pasted transcript, or your
  own draft intro) 0-100 from a question to the viewer, a concrete number, a promised
  payoff, intrigue, "you" and pace, minus filler like "welcome back" or "subscribe",
  in English and Russian; compares outliers' hooks with ordinary videos in a niche
  (needs 10+ transcripts per group). Text only, not visuals; zero quota, no LLM needed
- Sponsor map: which brands pay creators in a niche or on a channel, read from
  the video descriptions already in the database — "sponsored by", promo codes
  and affiliate links (Amazon etc.) kept apart; a lower bound, only what is
  written in a description; zero quota
- The exact same calculation in Claude Desktop (via MCP) and on the web
  dashboard — one shared codebase, not two implementations
- Template-risk check: how much a channel's (or a whole niche's) recent uploads
  look like one template repeated — the pattern behind YouTube's "inauthentic
  content" demonetisations — from title similarity, shared title openings,
  uniform lengths and a metronome upload rhythm (a heuristic, not a verdict)
- Niche clusters (k-means over channel embeddings) and a niche map;
  semantic similarity of channels and videos via pgvector
- Niche trend: growing / holding / cooling / saturated, from the last 30 days
  against the 90 before — how many videos come out, how the new ones are
  watched (projected to day 30), how many channels were created and whether
  newcomers break out; "not enough data" under 20 videos per window, and a
  "low confidence" mark when most videos were not seen young
- Idea checker, title scoring and title suggestions, SEO review of a
  draft's title/description/tags, drafts linked to published videos
- Outlier to brief: one call turns a video that beat its channel into a
  working brief for your own video — its hook, niche title patterns, whether
  the topic is already covered, title candidates and thumbnail references —
  saved as a draft linked back to the source; parts that need an LLM are
  named as skipped instead of faked
- "Why viral" explanations for outlier videos and comment insights per
  video or niche
- Content gaps: questions and requests from the comments of a niche's top
  videos that no collected video or pasted transcript answers yet, ranked by
  demand (askers, likes, how many videos they asked under), with the nearest
  existing video and a "to brief" link; works without an LLM (rules, English
  and Russian, noisier) and with one; comments are read only on click,
  1 quota unit per video, then cached
- Transcripts: a queue, manual paste, hybrid (keyword + semantic) search
- Alerts (new outliers, view acceleration, title changes, a channel
  breaking its silence, a tracked channel or an alerted outlier video that
  disappeared from YouTube), delivered to Telegram or a webhook one by one
  or as one morning digest; a swipe file for saved videos and channels
- Export a niche's videos to TSV/CSV
- Similar thumbnails: CLIP image vectors (local, ONNX on CPU) find videos whose
  thumbnail looks like an outlier's, thumbnails matching a text description
  ("red arrow, shocked face"), and a niche's thumbnail styles with how each
  performs. Opt-in (`WORKER_THUMB_EMBED=1` or a button on the niche screen):
  the models take ~0.6 GB of disk in the models volume, the process peaks at
  ~0.7 GB of RAM while embedding, ~60 ms of CPU per thumbnail (about a second
  with the polite download pace); only the
  vector is kept, and search takes milliseconds with pgvector
- Your own channels: connect them through Google OAuth (your own client, read-only)
  and see their real YouTube Analytics numbers — views, retention, revenue, CPM,
  RPM — next to a niche, and your real RPM against niche-finder's estimate; the
  refresh token is stored encrypted and the worker syncs daily. Thumbnail CTR is
  not in the Analytics API, so it is not shown
- YPP thresholds: which YouTube Partner Program bars a channel visibly meets
  (subscribers, uploads and Shorts views in 90 days), on the channel screen and
  in the extension — deliberately not a "monetized" badge, since YouTube does
  not publish that and the page signals for it proved unreliable
- Seven ready-made scenarios for Claude (MCP prompts, under "+" in Claude
  Desktop): find a niche, analyse a competitor, validate an idea, outlier to
  your own video, weekly review, content gaps, niche health — each names the
  tools in order, states its quota cost and checks the remaining quota before
  collecting anything
- Only the free YouTube Data API v3 and local PostgreSQL — no paid
  subscriptions and no LLM key required. An LLM (via OpenRouter or a local
  Ollama) is opt-in and off by default — see [Privacy](#privacy)

## Table of Contents

- [Screens](#screens)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Repository layout](#repository-layout)
- [Contributing](#contributing)
- [Privacy](#privacy)
- [Changelog](#changelog)
- [Read next](#read-next)

## Screens

<table>
<tr>
<td width="50%"><img src="assets/viral.jpg" alt="Viral videos from small channels"><br><sub>Viral videos — small channels that overperformed expectations</sub></td>
<td width="50%"><img src="assets/outliers.jpg" alt="Outlier channels"><br><sub>Outlier channels — best video's multiplier against the channel's median</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/categories.jpg" alt="Categories"><br><sub>Categories — share and growth across YouTube niches</sub></td>
<td width="50%"><img src="assets/keywords.jpg" alt="Keywords"><br><sub>Keywords — trendScore, lift, momentum</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/tracker.jpg" alt="Channel tracker"><br><sub>Channel tracker — collect and follow specific channels</sub></td>
<td width="50%"><img src="assets/niches.jpg" alt="Niches"><br><sub>Niches — everything collected under user-defined labels</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/data.jpg" alt="Data"><br><sub>Data — database state and manual collection/refresh</sub></td>
<td width="50%"></td>
</tr>
</table>

## How it works

```mermaid
flowchart LR
    CD["Claude Desktop"] -->|MCP| MCP["MCP server\nbackend/server.py"]
    FE["Browser / frontend"] -->|HTTP| API["HTTP API\nbackend/api.py"]
    EXT["Chrome extension\non youtube.com"] -->|"HTTP (localhost)"| API
    YT["YouTube Data API v3"] <-->|"on schedule"| W["Background worker\nbackend/worker.py"]
    MCP --> PG[("PostgreSQL")]
    API --> PG
    W --> PG
```

The MCP server, HTTP API, and worker are three different entry points into
the same code: all three call the same use cases from `backend/application/`,
so the result in Claude Desktop and on the dashboard is literally the same
calculation — not two separate implementations. A detailed breakdown of the
backend's layers (as of September 5, 2026 — DDD: domain → infrastructure →
application → interfaces) is in
[backend/README.md](backend/README.md#structure).

## Quick start

With Docker (recommended — brings up Postgres, the worker, and the dashboard
together):

```bash
cp .env.example .env          # fill in YOUTUBE_API_KEY (not required to build)
docker compose build
make up                       # or: docker compose up -d web worker
make doctor                   # check the key, network, and database
open http://localhost:8080    # dashboard
```

Without Docker (needs your own reachable Postgres):

```bash
make local-install            # venv + backend dependencies, once
make dev                      # HTTP dashboard on http://localhost:8080
make local-run                # or: MCP server on the host, for Claude Desktop
```

Running on the host (`make dev` / `make local-run`) uses the same database,
but at a different address: the Postgres container is published on
`127.0.0.1:5433`, while the code defaults to `localhost:5432`, so without a
hint the process fails with `psycopg2.OperationalError: Connection refused`.
Add this line to `.env`:

```bash
NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5433/niches
```

(`NICHE_DATABASE_URL` takes precedence over `POSTGRES_*` and stays
host-only: compose loads `.env` into the containers but blanks this one, and
`scripts/mcp-docker.sh` and `scripts/diag.sh` strip it explicitly). Do
**not** replace it with `POSTGRES_PORT=5433` -- compose hands that same
variable to the containers as the in-network port, which would break
`web`/`worker`/`mcp`.

`make dev` listens on the same `:8080` as the `web` container. You can't run
both at once: the host uvicorn grabs the port and the container's port
mapping silently drops (`docker ps` shows `8080/tcp` with no mapping). Either
run `docker compose stop web` before `make dev`, or edit the code inside the
container -- `./backend` is mounted in, and `docker compose restart web`
picks up changes without a rebuild.

`make help` prints every available command with a one-line description.

## Repository layout

| Path | What's inside |
|---|---|
| [`backend/`](backend/README.md) | MCP server, HTTP API, worker — all the logic and data storage |
| [`frontend/`](frontend/README.md) | dashboard: index.html, styles.css, ui.js, app.js, router.js, shared.js, screens/ |
| [`extension/`](extension/README.md) | Chrome extension: panels and badges on top of YouTube |
| `docker-compose.yml` | postgres + worker + web + mcp/mcp-http/mcp-https services, plus an optional `ollama` service (profile `llm-local`) |
| `Makefile` | commands to run everything, via Docker or straight on the host |
| `.env.example` | YouTube key and worker settings |
| `scripts/mcp-docker.sh` | MCP server launcher in Docker for Claude Desktop |

`docs/` (decision history and market research) — internal notes, not
included in this repository.

## Contributing

Bug reports, bug fixes, and documentation PRs are welcome — for anything
bigger (a new MCP tool, API endpoint, or dashboard screen), please open an
issue first to agree on the shape. See
[CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a dev environment and
run the test suite.

## Multi-user mode (experimental)

Off by default: niche-finder is a single-user tool on your own machine.
`NF_MULTI_USER=1` adds sign-in with invited accounts (`cli.py create-user`), and
each user gets their own watchlist, swipe file, drafts, transcript queue,
alerts and a daily share of the YouTube quota; the extension and MCP over HTTP
sign in with personal tokens, and each user sets up their own Telegram or
webhook alerts. The LLM budget and rate limit are still shared, so do not give
accounts to other people yet. Details: [backend/README.md](backend/README.md#multi-user-mode-plan-15-experimental-off-by-default).

## Privacy

Self-hosted, no telemetry, no account: everything stays in your own Postgres,
and by default the app contacts exactly two external hosts —
`www.googleapis.com` for the YouTube Data API and `www.youtube.com` for
channel RSS feeds. Comments read by `video_comments` are never stored. An
optional LLM step via [OpenRouter](https://openrouter.ai) (`LLM_PROVIDER`,
off by default) adds a third, `openrouter.ai`, only when you turn it on —
left unset, `OPENROUTER_MODEL` picks and rotates through OpenRouter's free
models on its own; set it to pin one specific model (free or paid) instead.
`LLM_PROVIDER=ollama` (stage 11) skips OpenRouter entirely and points that
same LLM step at a local [Ollama](https://ollama.com) install instead — no
external host, no API key, everything stays on your machine. See
[PRIVACY.md](PRIVACY.md) for the full picture, including what the first run
downloads and how to delete everything.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a history of notable changes, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## Read next

- [backend/README.md](backend/README.md) — YouTube API quotas, all 80 tools
  with descriptions, how to read `period_by`, running with and without
  Docker, the DDD layer structure.
- [frontend/README.md](frontend/README.md) — dashboard screens, where the
  data comes from, how the palette is built.
- [extension/README.md](extension/README.md) — what the extension shows on
  each kind of YouTube page, how to load it unpacked, and what it costs in
  API quota.

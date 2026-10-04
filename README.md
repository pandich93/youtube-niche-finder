# niche-finder

**A self-hosted YouTube research tool — your own NexLev / vidIQ / ViewStats, running on the free YouTube Data API and a local PostgreSQL.**

[![Release](https://img.shields.io/github/v/release/pandich93/youtube-niche-finder?style=flat-square)](https://github.com/pandich93/youtube-niche-finder/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/pandich93/youtube-niche-finder/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/pandich93/youtube-niche-finder/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fpandich93%2Fyoutube-niche-finder%2Fbadges%2Fcoverage.json&query=%24.totals.percent_covered_display&suffix=%25&label=coverage&style=flat-square)](https://github.com/pandich93/youtube-niche-finder/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/pandich93/youtube-niche-finder?style=flat-square)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white)](backend/README.md#running-without-docker)
[![PostgreSQL 16 + pgvector](https://img.shields.io/badge/postgres-16%20%2B%20pgvector-336791?style=flat-square&logo=postgresql&logoColor=white)](docker-compose.yml)
[![Docker Compose](https://img.shields.io/badge/docker-compose-2496ED?style=flat-square&logo=docker&logoColor=white)](docker-compose.yml)
[![MCP](https://img.shields.io/badge/MCP-100%20tools-8A2BE2?style=flat-square)](backend/README.md#tools)
[![Stars](https://img.shields.io/github/stars/pandich93/youtube-niche-finder?style=flat-square)](https://github.com/pandich93/youtube-niche-finder/stargazers)

Find niches, viral videos from small channels, outlier channels, trending
categories and keywords over any period (24 hours to 90 days and beyond);
track channels and watch their growth; check a title, hook or idea against
what actually worked in a niche — and get the same numbers in three places:

- **Claude Desktop** (or any MCP client) — 100 tools and 7 ready-made scenarios;
- **a web dashboard** on `localhost:8080`;
- **a Chrome extension** that puts the metrics on top of YouTube itself.

No subscription and no paid LLM key: semantic judgement ("is this channel
faceless?", "is this on topic?") is done by the model calling the tools, not
by the server. An LLM is optional and off by default.

![niche-finder dashboard: overview — corpus totals, the last 24 hours (new outliers, accelerating videos, growing channels), recent outlier channels and future competition](assets/dashboard.jpg)

## Contents

- [Why niche-finder](#why-niche-finder)
- [Features](#features)
- [Screens](#screens)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Documentation](#documentation)
- [Repository layout](#repository-layout)
- [Privacy and security](#privacy-and-security)
- [Contributing](#contributing)
- [License](#license)

## Why niche-finder

- **Yours, end to end.** Your key, your database, your machine. No telemetry;
  by default the app talks to exactly two hosts — `www.googleapis.com` and
  `www.youtube.com` (RSS feeds).
- **History the API doesn't give you.** The YouTube API only ever says "how
  many views right now". A background worker writes the numbers down on a
  schedule, so views per hour, acceleration, subscriber growth and title or
  thumbnail swaps actually exist.
- **Honest numbers.** RPM and revenue come as a range, not one invented
  figure; every signal carries its sample size; below a minimum sample you
  get "not enough data" instead of a guess; there is no single opaque
  "SEO score".
- **Quota-aware.** Reading is cheap, searching is scarce. Every collector
  reports what it spent, uploads are walked through playlists (1 unit per 50
  videos), and new uploads are spotted through free RSS feeds.
- **One codebase, three doors.** The MCP server, the HTTP API and the worker
  call the same use cases, so Claude and the dashboard can never disagree.

## Features

### Find a niche

- Viral videos from small channels and outlier channels (a video against its
  own channel's median), over any period, with subscriber, length, Shorts,
  RPM and YouTube Partner Program threshold filters.
- Trending categories and keywords with lift and momentum; best time to
  publish; title phrases that correlate with breakouts.
- Niche trend — growing / stable / cooling / saturated — from the last 30
  days against the 90 before: supply, demand (views projected to day 30), new
  channels and whether newcomers break out.
- A niche map (k-means over channel embeddings) and semantic similarity of
  channels and videos via pgvector.
- Idea checker: is a topic free, recently covered, proven or a flop in this
  niche?
- Content calendar: drafts on a week or month grid, drag one onto a day, the
  niche's best hours as a hint, a Telegram/digest reminder the day before and
  when it is due.
- Collaboration partners: similar channels of your size (×0.5–×2 subscribers),
  active in the last 30 days and not templated, each with the numbers that
  picked it, one click to the tracker.
- Net profit: revenue range minus your cost profiles (per video, per minute of
  video, monthly overhead) for a channel's month, a video or a niche's typical
  video, with break-even views; your real RPM on a connected channel.
- What outliers share: for a niche, what its outliers have in common against
  ordinary videos -- number or "?" in the title, video and title length, tags,
  weekday and time -- by the numbers, no LLM, Shorts and long-form apart.
- Where to enter: every niche scored 0–100 from six visible terms (demand trend,
  supply growth, newcomers breaking out, RPM, templated channels, policy
  signals), sortable by any column, with a side-by-side comparison of 2–3 niches.
- Language gaps: outliers in one language and whether anything like them exists
  in another (not made / made weakly / already a hit), from the videos you
  collected.
- Export a niche's videos to TSV or CSV.

### Study channels and videos

- Channel tracking: growth by window, views-per-hour of every upload, a
  revenue range, which YPP thresholds it visibly meets (and how it fares
  under the rules from 2027-02-01), the next subscriber milestones with an
  estimated date, and a "gone" mark
  when a channel disappears from YouTube.
- Repackaging: title and thumbnail swaps after publishing, before and after
  side by side, with views per hour around the swap.
- Template risk: how much a channel's (or a niche's) recent uploads look like
  one template repeated — the pattern behind "inauthentic content"
  demonetisations. A heuristic, not a verdict.
- Sponsor map: which brands pay creators in a niche, read from descriptions;
  promo codes and affiliate links kept apart.
- Similar thumbnails with local CLIP vectors: thumbnails that look like an
  outlier's, search by description ("red arrow, shocked face"), and a niche's
  thumbnail styles with how each performs. Opt-in.

### Plan your own video

- **Outlier to brief:** one call turns a video that beat its channel into a
  working brief — hook, niche title patterns, whether the topic is already
  covered, title candidates and thumbnail references.
- **Metadata review:** a draft's title, description and tags checked against
  your corpus, signal by signal, with near-duplicate topics; drafts can be
  linked to the published video to see whether the review held up.
- **Hook score:** the first ~30 seconds of a transcript or your draft intro,
  rated 0–100 in English and Russian; compared with a niche's outliers.
- **Content gaps:** questions and requests from a niche's comments that no
  video answers yet, ranked by demand.
- Title scoring and suggestions, transcripts with hybrid (keyword + semantic)
  search, comment insights and "why did it take off" explanations (the last
  ones need the optional LLM).

### Stay on top of it

- Alerts for your watchlist: a new outlier, view acceleration, a title change,
  a channel breaking its silence, a channel or video that vanished, a subscriber
  milestone passed, and a new video on a topic you named in plain words — on the
  dashboard, in the extension's badge, and to Telegram or a webhook (one by one
  or as a morning digest).
- A swipe file for videos and channels you want to come back to.
- **Your own channels:** connect them through Google OAuth (your own client,
  read-only) and see real YouTube Analytics numbers — views, retention,
  revenue, RPM — next to a niche, and your real RPM against niche-finder's
  estimate.

### Optional extras

- An LLM via [OpenRouter](https://openrouter.ai) (free models rotate on their
  own) or a local [Ollama](https://ollama.com) for background labelling,
  comment insights, title generation and explanations — with a daily budget.
- Multi-user mode (experimental): invited accounts, each with their own
  watchlist, drafts, alerts and a share of the quota.

## Screens

<table>
<tr>
<td width="50%"><img src="assets/viral.jpg" alt="Viral videos from small channels"><br><sub><b>Viral videos</b> — small channels that beat their expectations</sub></td>
<td width="50%"><img src="assets/outliers.jpg" alt="Outlier channels"><br><sub><b>Outlier channels</b> — best video's multiplier against the channel's median</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/channel.jpg" alt="Channel analytics"><br><sub><b>Channel</b> — growth, snapshots, revenue range, similar channels</sub></td>
<td width="50%"><img src="assets/keywords.jpg" alt="Keywords"><br><sub><b>Keywords</b> — trendScore, lift, momentum</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/categories.jpg" alt="Categories"><br><sub><b>Categories</b> — share and growth across YouTube niches</sub></td>
<td width="50%"><img src="assets/tracker.jpg" alt="Channel tracker"><br><sub><b>Tracker</b> — collect and follow specific channels</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/niches.jpg" alt="Niches"><br><sub><b>Niches</b> — everything collected under your own labels</sub></td>
<td width="50%"><img src="assets/data.jpg" alt="Data"><br><sub><b>Data</b> — database state and manual collection</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/find.jpg" alt="Find a niche"><br><sub><b>Find a niche</b> — semantic search over the collected corpus, no quota</sub></td>
<td width="50%"><img src="assets/alerts.jpg" alt="Alerts"><br><sub><b>Alerts</b> — new outliers and accelerating videos on tracked channels</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/packaging.jpg" alt="Repackaging history"><br><sub><b>Repackaging</b> — title and thumbnail changes, before / after</sub></td>
<td width="50%"><img src="assets/ideas.jpg" alt="Idea checker"><br><sub><b>Idea checker</b> — free, recently covered, proven demand or flop</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/compare.jpg" alt="Compare trajectories"><br><sub><b>Compare</b> — views by age for up to five videos, side by side</sub></td>
<td width="50%"><img src="assets/policy.jpg" alt="Monetization-policy signals"><br><sub><b>Policy signals</b> — YouTube's three inauthentic-content categories, no risk percentage</sub></td>
</tr>
</table>

The dashboard has 24 sections in the sidebar plus niche, channel and brief
pages — alerts, idea checker, transcripts, niche map, title check,
repackaging, metadata review, your own channels and more;
[frontend/README.md](frontend/README.md#screens) walks through each one.

## Quick start

You need Docker and a free
[YouTube Data API v3 key](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
(Google Cloud Console → enable the API → Credentials → API key; 10,000 units
a day at no cost).

```bash
git clone https://github.com/pandich93/youtube-niche-finder.git
cd youtube-niche-finder
cp .env.example .env          # put your key into YOUTUBE_API_KEY (no quotes)
docker compose build
make up                       # Postgres + dashboard + background worker
make doctor                   # checks the key, the network and the database
open http://localhost:8080
```

On the dashboard, collect a channel or a niche from the **Data** screen and
the sections fill in. `make help` lists every command.

**No key yet?** `make up-db && make seed && make web` fills the database with
synthetic demo data so you can click around first.

### Connect Claude Desktop

Add the MCP server to `claude_desktop_config.json` — the exact `docker run`
config is in [backend/README.md](backend/README.md#running-with-docker-recommended),
and the dashboard's **MCP connection** screen walks you through it and checks
the connection. Then try one of the scenarios under "+" in Claude Desktop,
for example *find a niche*.

### Install the Chrome extension

1. Download `niche-finder-extension-<version>.zip` from the
   [latest release](https://github.com/pandich93/youtube-niche-finder/releases/latest)
   and unzip it (or use the `extension/` folder of your clone).
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and pick the folder.
3. Open any YouTube video — the panel appears in the right-hand column.

Details: [extension/README.md](extension/README.md).

### Without Docker

```bash
make local-install            # venv + backend dependencies, once (Python 3.10+)
make dev                      # dashboard on http://localhost:8080
make local-run                # or: the MCP server on the host
```

You need your own Postgres; with the compose database, add
`NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5433/niches` to
`.env`. Why, and the other pitfalls:
[backend/README.md](backend/README.md#running-without-docker).

## How it works

```mermaid
flowchart LR
    CD["Claude Desktop<br/>any MCP client"] -->|MCP| MCP["MCP server<br/>backend/server.py"]
    BR["Dashboard<br/>frontend/"] -->|HTTP| API["HTTP API<br/>backend/api.py"]
    EXT["Chrome extension<br/>on youtube.com"] -->|"HTTP, localhost only"| API
    W["Worker<br/>backend/worker.py"]
    MCP --> APP["Use cases<br/>backend/application/"]
    API --> APP
    W --> APP
    APP --> PG[("PostgreSQL<br/>+ pgvector")]
    APP <-->|"quota-aware"| YT["YouTube Data API v3<br/>+ channel RSS"]
    APP -.->|optional| LLM["LLM: OpenRouter<br/>or local Ollama"]
    APP -.->|optional| YA["YouTube Analytics API<br/>your channels, OAuth"]
    W -.->|optional| TG["Telegram / webhook<br/>alerts"]
```

The MCP server, the HTTP API and the worker are three entry points into the
same code in `backend/application/`, so a number in Claude Desktop and on the
dashboard is literally the same calculation. The worker runs on a schedule:
free RSS checks for new uploads, view-count refreshes, channel snapshots,
alerts and the optional background jobs. The backend follows DDD layers —
`domain` (pure formulas) → `infrastructure` (Postgres, YouTube, embeddings) →
`application` (use cases) → `interfaces` (MCP, HTTP, CLI, worker); see
[backend/README.md](backend/README.md#structure).

## Documentation

| Document | What's inside |
|---|---|
| [backend/README.md](backend/README.md) | YouTube quota, running with and without Docker, the CLI, all 100 MCP tools and 7 scenarios, configuration, formulas, code structure |
| [frontend/README.md](frontend/README.md) | every dashboard screen, where its data comes from, what costs quota |
| [extension/README.md](extension/README.md) | what the extension shows on each YouTube page, installation, quota cost |
| [docs/http-api.md](docs/http-api.md) | all 115 HTTP routes with parameters, costs and MCP twins (generated from the code) |
| [`.env.example`](.env.example) | every setting, commented in place |
| [SECURITY.md](SECURITY.md) · [PRIVACY.md](PRIVACY.md) | reporting a problem, what protects each mode, what is stored and where traffic goes |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md) | dev setup and tests; what changed in each release |

A map of all of it: [docs/README.md](docs/README.md).

## Repository layout

| Path | What's inside |
|---|---|
| [`backend/`](backend/README.md) | MCP server, HTTP API, worker and CLI — all logic and storage (Python, FastAPI, psycopg2) |
| [`frontend/`](frontend/README.md) | the dashboard: plain ES modules, no npm, no build step |
| [`extension/`](extension/README.md) | Chrome extension (Manifest V3): panels and badges on top of YouTube |
| [`docs/`](docs/README.md) | documentation map and the HTTP API reference |
| [`scripts/`](scripts) | MCP-in-Docker launcher, diagnostics, the API-docs generator, benchmarks |
| [`infra/caddy/`](infra/caddy) | HTTPS reverse proxy for MCP over HTTP (`mcp-https` service) |
| [`assets/`](assets) | screenshots |
| `docker-compose.yml` | `postgres`, `worker`, `web`, `mcp`, `mcp-http`, `mcp-https`, and an optional `ollama` (profile `llm-local`) |
| `Makefile` | every command, via Docker or straight on the host (`make help`) |

## Privacy and security

Self-hosted, no telemetry, no account. Everything stays in your Postgres;
comments are read live and not stored as themselves (only derived questions
and LLM summaries are cached); the dashboard and the extension talk only to
`127.0.0.1`. The optional LLM adds `openrouter.ai` only when you
turn it on — or nothing at all with a local Ollama.
[PRIVACY.md](PRIVACY.md) has the full picture, including how to delete
everything.

niche-finder is a single-user tool by default. `NF_MULTI_USER=1` turns on
invited accounts with per-user data, quota shares, personal API tokens and
alert settings — read [SECURITY.md](SECURITY.md) (HTTPS, `NF_COOKIE_SECURE`,
`OWN_TOKENS_KEY`, backups, YouTube's 30-day storage rule) before giving
anyone an account.

## Contributing

Bug reports, fixes and documentation PRs are welcome. For anything bigger —
a new MCP tool, API route or dashboard screen — please open an issue first to
agree on the shape. [CONTRIBUTING.md](CONTRIBUTING.md) explains the dev
setup, the tests (`make local-test`, no YouTube key needed) and the style.
Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE). niche-finder is not affiliated with YouTube, Google, NexLev,
vidIQ or ViewStats; you use your own API key under
[YouTube's API Terms of Service](https://developers.google.com/youtube/terms/api-services-terms-of-service).

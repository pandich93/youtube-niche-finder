# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Chrome extension** (`extension/`, Manifest V3) — vidIQ/NexLev-style
  panels on top of YouTube, served entirely from the local backend: outlier
  score against the channel's own median, view velocity and acceleration,
  views per subscriber, engagement, 30-day projection, revenue range and
  tags on a watch page; growth, grade, best publishing times, title patterns
  and similar channels on a channel page; multiplier badges on thumbnails in
  search, home and recommendations. Network access is limited to
  `127.0.0.1` by `host_permissions`.
- **`/api/inspect/video`, `/api/inspect/channel`, `/api/inspect/videos`** —
  endpoints behind the extension (`backend/application/inspection.py`).
  They read the local Postgres first and only fall back to a single
  `videos.list` / `channels.list` call (1 unit each, batched 50 ids per
  call) when a row is missing or stale — 6h for videos, 24h for channels —
  storing whatever they fetch, so browsing YouTube also fills the database.
- **`backend/tests/test_inspection.py`** — 13 tests for the above that need
  neither Postgres nor an API key (sqlite double for the store, stub for the
  HTTP client).
- **Tool annotations on all 43 MCP tools** — every tool now declares
  `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`
  as explicit booleans, so a client can tell a free local query from one that
  spends YouTube quota or deletes a row (and OpenAI's directory stops
  rejecting the server for missing hints). 28 tools are read-only;
  `destructiveHint` is true only for `untrack_channel` and
  `delete_saved_item`; `openWorldHint` is true for the 8 tools that reach the
  YouTube Data API. `collect_*` / `refresh_*` are marked non-idempotent
  because each call appends a new stats snapshot.

- **Tests for the remaining 17 MCP tools** — every tool is now exercised
  through `interfaces/mcp/server.py` itself rather than the layer beneath it
  (43/43, up from 26/43), with the YouTube client monkeypatched so nothing
  needs a key or the network. The new tests pin the behaviour the tool
  annotations claim: `refresh_stats` / `refresh_channels` append a second
  history row on a second call, `refresh_categories` upserts instead,
  `untrack_channel` keeps what was collected, and `calibrate_maturity_curve`
  writes nothing at all.

- **[PRIVACY.md](PRIVACY.md)** — what the project stores, where its traffic
  goes, and how to delete everything, linked from the README. Every claim is
  checked against the code: no telemetry, comments read by `video_comments`
  are never stored, and the application contacts exactly two external hosts
  (`www.googleapis.com` and `www.youtube.com`). It also records what a source
  scan misses — the embedding model is fetched from Hugging Face on first run
  — and what one gets wrong: `www.w3.org` is the Atom namespace identifier in
  `rss.py`, not a host anything connects to.

- **Rate limiting on the HTTP API** — a sliding one-minute window over
  `/api/*`, no new dependency. `RATE_LIMIT_PER_MINUTE` defaults to 600 and 0
  turns it off; over the limit the API answers 429 with `Retry-After`. The
  service listens on `127.0.0.1`, so this is a fuse against a looping client
  rather than a defence against outside traffic, and the default is sized from
  what the extension actually sends (~120-200 requests a minute while
  scrolling search results). Static frontend files are not counted, and the
  429 still carries CORS headers.

### Fixed

- **`scripts/mcp-docker.sh` больше не полагается на `docker run --env-file`.**
  `docker compose` читает `.env` по правилам dotenv и снимает кавычки вокруг
  значения, а `docker run --env-file` берёт строку буквально — из-за чего
  `YOUTUBE_API_KEY="AIza..."` попадал в контейнер вместе с кавычками. Ломался
  при этом только MCP-сервер (его запускает этот скрипт), а воркер и веб через
  compose работали как ни в чём не бывало: любой инструмент, ходящий в YouTube
  Data API, падал, а `db_stats`, `search_outliers` и эмбеддинги отвечали
  мгновенно. Скрипт теперь разбирает `.env` сам, снимает обрамляющие кавычки
  (одинарные и двойные), терпит CRLF, комментарии, пустые строки и префикс
  `export`, и передаёт переменные через `-e`.
- **`scripts/diag.sh`** — диагностика связки с YouTube API одной командой:
  `docker ps`, curl к `videoCategories`/`search` с хоста и изнутри контейнера,
  `cli.py doctor`, логи воркера. Пишет `scripts/diag-output.txt` с
  замаскированным ключом.

### Changed

- HTTP API now sends CORS headers for `chrome-extension://` origins; it
  still binds to `127.0.0.1` only.
- CI and `make local-test` now run every `backend/tests/test_*.py` (one
  pytest process per file) instead of three hand-picked files; test scripts
  run directly exit non-zero on failure.

## [0.1.0] - 2026-09-06

Initial public release.

### Added

- **MCP server** (`backend/interfaces/mcp/server.py`) — 24 tools for Claude
  Desktop: collection (`collect_niche`, `collect_channel`,
  `collect_trending`, `refresh_stats`, `refresh_channels`,
  `refresh_categories`), free sections (`viral_videos_small_channels`,
  `recently_added_outlier_channels`, `high_future_competition`,
  `most_popular_categories`, `trending_keywords`, `search_outliers`,
  `niche_overview`, `list_niches`, `db_stats`, `data_coverage`), and channel
  tracking/analysis (`track_channel`, `channel_analytics`,
  `compare_channels`, `channel_velocity`, `title_changes`,
  `best_time_to_publish`, `title_patterns`, `calibrate_maturity_curve`, and
  more).
- **HTTP API** (`backend/interfaces/http/api.py`, FastAPI) exposing the same
  use cases as the MCP server, for the dashboard.
- **Background worker** (`backend/interfaces/worker/main.py`) that snapshots
  view/subscriber counts on a schedule — the only reason growth rate,
  acceleration, and period comparisons can exist at all.
- **Dashboard** (`frontend/`, no build step, plain ES modules): Overview,
  Viral videos, Outlier channels, Categories, Keywords, Channel tracker,
  Niches, Channel detail, and Data screens.
- **PostgreSQL storage** with a schema migration path from the earlier
  SQLite-based prototype (`backend/migrate_sqlite_to_postgres.py`).
- **Docker Compose** setup bringing up Postgres, the worker, the dashboard,
  and the MCP server (stdio and HTTP profile) with one command.
- **CI** (GitHub Actions): smoke tests against a real Postgres service
  container, a `docker compose build` check, and an auto-updated test
  coverage badge.
- MIT license.

[Unreleased]: https://github.com/pandich93/niche-finder/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pandich93/niche-finder/releases/tag/v0.1.0

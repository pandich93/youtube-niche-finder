# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Self-calibrating maturity curve** (`backend/application/maturity_curve.py`,
  `metrics.fit_maturity_curve`) — the worker re-fits the "share of 30-day
  views by age" curve from `video_stats_history` every
  `WORKER_CALIBRATE_INTERVAL_MIN` (1440) and, once 30+ videos have been
  watched from publication to 28+ days with a point at days 1-21, stores it
  in `meta`; web, MCP and worker then use it for every age-adjusted outlier
  score and `projected30dViews` instead of the shipped curve. Views are
  interpolated to whole days between snapshots at most 3 days apart, the
  curve is forced non-decreasing and capped at 1.0. A later failing check
  never drops a stored curve; `MATURITY_CURVE_AUTO=0` turns it off.
  `/api/health` (`maturityCurve`) and MCP `db_stats` (`maturity_curve`) say
  which curve is in use and why; `calibrate_maturity_curve` now reports
  per-age sample counts and missing ages, and only counts videos that
  actually contributed points.

- **Shared YouTube quota counter** — every request the YouTube client sends
  (retries and failures included) is added to a per-Pacific-day counter;
  `/api/health` (`unitQuota`), MCP `db_stats` (`unit_quota`) and the
  dashboard footer show units left out of `YOUTUBE_DAILY_UNIT_LIMIT`
  (10,000).

- **Local Ollama LLM provider** (`backend/infrastructure/llm/ollama.py`,
  stage 11) — `LLM_PROVIDER=ollama` routes every `llm_gateway.run()` call to
  a local [Ollama](https://ollama.com) install (`OLLAMA_URL`, `OLLAMA_MODEL`)
  instead of OpenRouter, so every AI feature works with nothing leaving the
  machine. `POST /api/chat` with `format: <json-schema>`, `stream: false`;
  `cost_usd` is always `0.0`. `docker-compose.yml` gets an optional `ollama`
  service under the `llm-local` profile. `cli.py doctor` checks reachability
  and whether `OLLAMA_MODEL` is actually pulled. 18 tests (mocked HTTP, no
  network) in `backend/tests/test_ollama_provider.py`, covering the provider
  itself and `infrastructure/llm/factory.py`'s provider selection.

- **Niche video export to TSV/CSV** (`backend/application/niche_export.py`,
  stage 18) — `GET /api/niche/{slug}/export.tsv` / `export.csv` and
  `cli.py export-niche` dump every collected video in a niche with channel
  and video metadata, both outlier scores and accepted `video_tags` joined
  with `;`. Zero YouTube quota — it reads only what is already in Postgres.
  The CSV carries a UTF-8 BOM so Excel opens it correctly; the niche page
  header gets two download links.

- **Semantic keyword merging** (stage 10) — `trending_keywords(
  keywords_mode="semantic")` merges n-gram phrases whose embeddings are
  near-duplicates ("cold shower" / "cold showers" / "ice bath") into one
  canonical entry, with previous-period stats realigned so momentum still
  lines up. The default `keywords_mode="ngram"` path is unchanged (pinned by
  a byte-identical regression test). Exposed via the MCP tool, `GET
  /api/keywords` (`keywords_mode`, `semantic_similarity`) and a checkbox on
  the Keywords screen. A fastembed/DB failure degrades to plain n-grams
  instead of failing the request.

- **Title scoring and generation** (`backend/domain/title_scoring.py`,
  stage 09) — `score_titles(candidates, niche|channel_id)` gives a
  deterministic 0-100 score (length, digits, brackets, matched
  `title_patterns`, near-duplicates of the niche's own titles) that works with
  `LLM_PROVIDER=none`; with an LLM it adds strengths, risks and a rewrite per
  title, grounded in the niche's top titles by outlier. `suggest_titles(topic)`
  generates candidates in the niche's style and scores them the same way.
  MCP `score_titles` / `suggest_titles`, `POST /api/titles/score` /
  `/api/titles/suggest`, and a Titles screen in the dashboard.

- **Niche clusters** (`backend/domain/niche_clusters.py`,
  `backend/application/niche_clusters.py`, stage 08) — k-means (numpy,
  k-means++ init, no new dependency) over per-channel embedding centroids,
  with per-cluster median outlier score, view velocity, faceless share and a
  competition count of channels over 100k subscribers. Clusters are named by
  the LLM, or after their top-3 tags when it is off. The worker recomputes
  them every `WORKER_CLUSTER_INTERVAL_MIN`; MCP `niche_map`, `GET
  /api/niche-clusters`, `POST /api/niche-clusters/recompute`, and a niche-map
  screen sorted by opportunity.

- **Manual transcripts with hybrid search** (`backend/domain/transcripts.py`,
  `backend/application/transcripts.py`, stage 19) — transcripts are never
  fetched: you queue a video, paste the text copied from YouTube's own
  transcript panel, and it is parsed into timestamped segments, chunked into
  ~160-word overlapping windows and embedded locally. `search_transcripts`
  ranks by Reciprocal Rank Fusion of vector similarity and Postgres full-text
  search, and every hit links to `youtu.be/<id>?t=<sec>`. MCP
  `request_transcript`, `list_transcript_queue`, `search_transcripts`;
  `/api/transcripts/*`; a Transcripts screen with pending/ready/error tabs.

- **Alert delivery to Telegram or a webhook** (`backend/infrastructure/notify/`,
  stage 07) — the worker sends new alerts right after scanning for them,
  deduplicated in `alert_deliveries`. Up to `NOTIFY_MAX_PER_CYCLE` (default
  10) go out one by one and the rest as a single summary. Configured with
  `NOTIFY_TELEGRAM_BOT_TOKEN` + `NOTIFY_TELEGRAM_CHAT_ID` (takes priority) or
  `NOTIFY_WEBHOOK_URL`; with neither set, delivery is skipped entirely.
  `cli.py notify-test` sends a test message.

- **"Why it went viral" explanations** (stage 05) — `explain_outlier(video_id)`
  asks the LLM for hooks, a title pattern, a timing factor, a replicable
  formula and a confidence score, grounded only in numbers already in the
  database (both outlier multipliers, channel median, VPH, sibling titles,
  channel labels). Zero YouTube quota, cached per video. MCP tool, `GET
  /api/video/{id}/why` (204 when the LLM is off or over budget), a button on
  the channel page and on the extension's video panel.

- **pgvector similarity search** (stage 06) — the Postgres image is now
  `pgvector/pgvector:pg16`; `videos.embedding_v vector(384)` with an HNSW
  index backs `similar_videos`, `similar_channels` and the SEO review's
  near-duplicate check. Everything is best-effort: on a plain Postgres image
  the same functions fall back to the existing in-Python cosine comparison
  with the same response shape. See "Upgrading to pgvector" in
  `backend/README.md`; `scripts/bench_similar.py` benchmarks both paths.

- **Comment insights** (stage 04) — `comment_insights(video_id)` reads up to
  200 comments (1 quota unit) and has the LLM extract pains, requests, video
  ideas, sentiment and language, cached in `video_insights`.
  `niche_comment_insights(niche)` summarises the cached per-video results
  without fetching anything. Click-only, never run by the worker: `POST
  /api/videos/{id}/insights`, `GET /api/niches/{slug}/insights` and buttons
  on the dashboard. Comment text (not authors) leaves the machine only for
  this opt-in feature — see PRIVACY.md.

- **Batch idea checker** (`backend/domain/idea_verdicts.py`, stage 17) —
  `check_ideas` takes up to 50 ideas and labels each `free`, `recent`,
  `proven` or `flopped` by matching it against the local corpus (title
  substring always, embedding similarity on top when vectors exist;
  `semanticSearchAvailable` says which). MCP tool, `POST /api/ideas/check`,
  and an Ideas screen with expandable matches and CSV export.

- **Niche scatter chart** (stage 15) — `niche_videos(niche)` returns a flat
  per-video list (`GET /api/niches/{slug}/videos`, MCP tool), drawn on the
  niche page as a hand-written SVG scatter: publish date against views on a
  log scale, coloured by channel, with outliers and videos under 30 days
  marked. Filterable by channel and with a hide-Shorts toggle.

- **Period-window outlier baseline** (stage 14) — alongside the rolling
  baseline, `outlierScorePeriod` compares a video with the channel's median
  for the same format (Shorts vs long-form) published within ±15 days,
  falling back to the whole-channel median when that window is thin.
  `outlierScoreRolling` is an alias of the unchanged `outlierScore`; both
  appear wherever outlier fields already did.

- **Background AI labelling** (`backend/application/enrichment.py`, stage 03)
  — with an LLM configured, the worker classifies tracked channels (faceless,
  format, topic) and tags new videos every `WORKER_ENRICH_INTERVAL_MIN`.
  Tags outside a niche's taxonomy land as proposals and stay out of
  `tag_stats` until accepted. MCP `enrich_channels`, `tag_new_videos`,
  `list_proposed_tags`, `resolve_proposed_tag` and matching `/api/enrich/*`,
  `/api/tags/proposed*`; faceless/format/topic filters on the tracked-channel
  list; an AI-label badge in the extension; `scripts/eval_enrichment.py` to
  spot-check label quality on real data.

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

- **Worker backfills embeddings on a schedule.** `collect_channel`,
  `collect_trending` and the RSS watch collect with `embed=False` for speed,
  so most of the corpus built that way had `videos.embedding IS NULL` and
  `similar_channels` / `similar_videos` / `niche_overview_from_channel` /
  `review_metadata` ran on an incomplete index. The worker now runs
  `backfill_embeddings` every `WORKER_EMBED_INTERVAL_MIN` (default 60min),
  up to `WORKER_EMBED_BATCH` videos (default 500) per pass — zero YouTube
  quota, pure local compute over title+description already in Postgres.
  `WORKER_EMBED=0` turns the step off; a missing/broken fastembed model logs
  a warning and skips the step instead of crashing the cycle. `db_stats` and
  the dashboard overview now show `videos_without_embedding`.

- **Optional LLM enrichment via OpenRouter** (`backend/infrastructure/llm/`,
  `backend/application/llm_gateway.py`) — off by default
  (`LLM_PROVIDER=none`), so no existing function changes behaviour until
  someone sets `LLM_PROVIDER=openrouter` and an `OPENROUTER_API_KEY`.
  `llm_gateway.run(task, system, user, schema)` caches identical requests in
  Postgres (`llm_cache`) so the same input never costs twice, tracks spend
  per day/model (`llm_usage`) and stops calling out once
  `LLM_DAILY_BUDGET_USD` (default $1.00/day) is spent for the UTC day —
  mirroring how the worker already backs off from YouTube's search quota.
  `OpenRouterProvider` retries 429/5xx twice with backoff, falls back to
  parsing JSON out of plain text on models that reject `response_format`,
  and never lets the API key reach a log line. Leave `OPENROUTER_MODEL`
  unset and it rotates through `FREE_MODEL_FALLBACKS` — currently
  `nvidia/nemotron-3-super-120b-a12b:free` and three more, pulled from
  OpenRouter's live `/api/v1/models` catalog and filtered to models that
  support `response_format` — until one answers, so a single renamed or
  rate-limited free model doesn't take enrichment down; pin `OPENROUTER_MODEL`
  to skip the rotation and always use one specific (free or paid) model.
  `db_stats` / `GET /api/stats` expose
  `llm: {provider, model, today_cost_usd, budget_usd, blocked}`, and
  `cli.py doctor --llm` pings the configured provider with a one-token
  request.

- **Curated video tags and their outlier-hit rate** (`video_tags` table,
  `backend/domain/tag_stats.py`, `backend/application/tags.py`) — group
  videos by your own labels (theme/trigger/format/...) and see which ones
  actually correlate with breaking out: `videos`, `hits`, `hitRate`, `lift`
  (against the whole niche's hit rate, not just the tagged subset),
  `medianViews`, `medianOutlier` per tag. A tag's `source` is `manual`,
  `claude-mcp` or `llm` — `llm` (stage 03's planned auto-tagging) never
  overwrites or deletes a `manual`/`claude-mcp` tag, enforced at the
  repository layer so it holds for `replace` mode too. New MCP tools
  `tag_videos`, `list_video_tags`, `tag_stats` (43 → 46) and matching
  `POST`/`GET /api/tags`, `GET /api/tags/stats`. The niche detail page now
  shows a hit-rate bar chart per tag group and an inline tag editor on each
  video.

- **Alerts** (`backend/domain/alerts.py`, `backend/application/alerts.py`) —
  the worker scans already-collected data every `WORKER_ALERTS_INTERVAL_MIN`
  for outliers, acceleration, title changes and channels breaking a silence,
  deduplicated on `(kind, ref_id)` so repeated cycles stay idempotent. MCP
  `scan_for_alerts`, `list_events`, `mark_events_seen`, `/api/events*`, and
  an events list with an unread badge in the extension.
- **Swipe file** (`backend/application/library.py`) — save a video or
  channel together with a snapshot of its metrics at save time, at zero
  quota. MCP `save_item`, `list_saved_items`, `delete_saved_item`,
  `/api/saved`, a Saved screen, and a save button in the extension.
- **SEO review of a draft** (`backend/domain/metadata.py`,
  `backend/application/metadata_review.py`) — scores a draft title,
  description and tags against your own corpus signal by signal (deliberately
  never as one number), and keeps draft history so outcomes can be checked
  after publishing. MCP `review_metadata`, `save_draft`, `list_drafts`,
  `link_draft`, `draft_outcomes`.
- **RSS watch** (`backend/infrastructure/youtube/rss.py`) — each tracked
  channel's Atom feed is polled every `WORKER_RSS_INTERVAL_MIN` as a free
  novelty check between the quota-costing refreshes, falling back to the
  uploads playlist after ~15 missed uploads.
- **`mcp-https` compose service** — Caddy with a locally trusted certificate
  (`infra/caddy/Caddyfile`) in front of `mcp-http`, which is no longer
  published to the host directly.
- **Top tags by category** — `top_tags_by_category` ranks the tags winning
  videos in each YouTube category actually use (the literal tag, not the
  n-grams `trending_keywords(source="tags")` builds), by frequency and
  outlier lift. MCP tool, `GET /api/tags/top-by-category`, and its own screen.
- **Channel-anchored niche overview** — `niche_overview_from_channel` finds
  a channel's closest peers by embedding and runs the `niche_overview`
  saturation/opportunity analysis over them, with no pre-collected niche
  needed. MCP tool, `GET /api/channels/{id}/niche-overview`, and a section on
  the channel page. The outlier-channels feed gained adjustable multiplier
  and subscriber filters, and `trending_keywords` a relative 0-100
  `opportunityScore`.
- **Free local niche search in the dashboard** — a Find screen (`#/find`)
  over the existing `search_outliers` semantic search, with NexLev-style
  RPM, video length and Shorts filters (`min_rpm`, `max_rpm`,
  `min_video_length`, `max_video_length`, `only_shorts`, also on the MCP tool
  and `GET /api/search`), an `estimatedRpm` badge on every result, a list of
  active filters when nothing matches, and a search-quota indicator in the
  sidebar.
- **Comments and similar channels** — MCP `video_comments` and
  `similar_channels` (per-channel embedding centroids) with matching HTTP
  routes, a similar-channels card on the channel page and a per-video
  comments button that spends quota only on click. `backfill_embeddings`
  (MCP tool and `cli.py embed-videos`) embeds videos collected before
  `embed=True`.

### Fixed

- **`scripts/mcp-docker.sh` no longer relies on `docker run --env-file`.**
  `docker compose` reads `.env` by dotenv rules and strips the quotes around
  a value, while `docker run --env-file` takes the line literally — so
  `YOUTUBE_API_KEY="AIza..."` reached the container with its quotes. Only the
  MCP server broke (this script is what starts it); the worker and web
  services under compose kept working: every tool that calls the YouTube
  Data API failed, while `db_stats`, `search_outliers` and embeddings answered
  instantly. The script now parses `.env` itself, strips surrounding single
  and double quotes, tolerates CRLF, comments, blank lines and the `export`
  prefix, and passes the variables via `-e`.
- **`scripts/diag.sh`** — one-command diagnosis of the YouTube API
  connection: `docker ps`, curl to `videoCategories`/`search` from the host
  and from inside the container, `cli.py doctor`, worker logs. Writes
  `scripts/diag-output.txt` with the key masked.
- **Tracking a channel by handle.** `track_channel(collect=False)` (MCP) and
  `POST /api/channels/track` (HTTP) stored the raw `@handle`/URL in the
  watchlist instead of the channel_id, so the worker could not poll the
  channel and its history silently never accumulated. The new
  `resolve_channel_id` looks the channel up in the local database by
  `custom_url` first (0 quota), then via `channels.list?forHandle=`; a
  channel that cannot be resolved is never written to the watchlist.
  `cli.py fix-tracked [--apply]` cleans up what had already piled up in
  `tracked_channels`.
- **`collect_channel(niche=...)` did not create the niche.** Videos were
  linked in `video_niches`, but no row appeared in `niches`, so the Niches
  screen and `list_niches` saw nothing. `collect_channel` now upserts into
  `niches`, as `collect_niche` does.
- **Viral hid big competitors inside an already chosen niche.** Added
  `preset="niche_all"` to `viral_videos_small_channels` (MCP, HTTP,
  dashboard): it drops the subscriber/view/VSR thresholds and shows every
  collected video in the niche.
- **`collect_niche` lost new niches on a mid-search failure.** It only
  committed at the very end, so any exception in the `search.list` loop
  (usually `QuotaExceeded`) discarded the new niche row and leaked a pooled
  connection. The niche and each spent search call now commit as they go,
  the call is refused up front once the daily limit is hit, and
  `/api/collect/niche` answers 429 with a clear message instead of a 500.
- **Search quota tracking and worker backoff.** `search.list` calls are
  counted against the real Pacific-Time quota day and the remaining budget
  shows in `db_stats` / `data_coverage`; the worker now actually reads
  `worker_quota_blocked_until`, so `WORKER_QUERIES` stops after the quota is
  exhausted.
- **Running outside Docker.** A host process fell back to `localhost:5432`
  while the compose database is published on `127.0.0.1:5433`.
  `NICHE_DATABASE_URL` in `.env` now takes priority over `POSTGRES_*`, and
  `scripts/mcp-docker.sh` / `scripts/diag.sh` strip it before forwarding
  `.env` into a container.
- **Test runs left Postgres schemas behind.** `backend/tests/schema_scope.py`
  now drops each process's throwaway `nichetest_*` schema at exit (keep it
  with `NICHE_KEEP_TEST_SCHEMA=1`; a schema passed in via `NICHE_DB_SCHEMA` is
  never dropped), and `make test` drops its fixed schema before running, so
  it is repeatable.

### Changed

- HTTP API now sends CORS headers for `chrome-extension://` origins; it
  still binds to `127.0.0.1` only.
- CI and `make local-test` now run every `backend/tests/test_*.py` (one
  pytest process per file) instead of three hand-picked files; test scripts
  run directly exit non-zero on failure.
- CI now publishes the coverage badge data to a separate orphan `badges`
  branch (just `coverage.json`) instead of committing
  `assets/coverage.json` to `main`, so pushes to `main` no longer get
  rejected by the bot's commit and need a `pull --rebase`. The branch is
  created on the first push-to-`main` run; unchanged numbers commit
  nothing. `assets/coverage.json` is gone from `main`, and the coverage
  badges in `README.md` and `backend/README.md` read the `badges` branch and
  link to the CI workflow runs.

### Removed

- Removed `backend/migrate_sqlite_to_postgres.py` (one-off migration from the
  SQLite prototype).

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

[Unreleased]: https://github.com/pandich93/youtube-niche-finder/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pandich93/youtube-niche-finder/releases/tag/v0.1.0

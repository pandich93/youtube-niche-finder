# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **YPP rules from 2027-02-01 and subscriber milestones** (plan 17) — YouTube
  raises the Partner Program bar for new creators on 2027-02-01 (1,000
  subscribers and 8,000 watch hours in 365 days or 20M Shorts views in 90
  days, plus an activity bar). `yppEligibility` switches to those rules on
  that day and until then carries the same check under them as `upcoming`;
  the expanded tier keeps its 2026 numbers, since YouTube's page does not say
  it changes. `channel_analytics` and the extension's channel panel gain
  `milestones`: when the next subscriber milestones come at the pace of the
  last 30 and 90 days of snapshots, and whether 1,000 comes before the rules
  change -- an estimate, not YouTube data. A tracked channel passing a
  milestone between its two latest snapshots raises a new `milestone` alert
  (dashboard, extension, Telegram/webhook).

### Changed

- **Old rows are re-read from YouTube daily** (plan 16) — YouTube's API
  policies allow keeping data fetched with a key at most 30 days unrefreshed,
  while the worker only refreshed videos up to 30 days old, and through
  `videos.batchGetStats`, which returns counters, not titles or descriptions.
  A new daily worker step re-reads every stored video and channel not
  refreshed for `REFRESH_STALE_DAYS` (25) with `videos.list` /
  `channels.list`, oldest first, capped by `REFRESH_STALE_MAX_VIDEOS` /
  `REFRESH_STALE_MAX_CHANNELS` (2,500 each, ~100 units a day at most);
  `WORKER_FRESHNESS=0` switches it off. Nothing is deleted: a row the API no
  longer returns is marked gone. `db_stats` gains `freshness`, the "Данные"
  screen a "Хранение данных" card, and SECURITY.md / PRIVACY.md say what is
  kept and what YouTube's derived-metrics approval would change.
- **Velocity across the 2026-08-24 view-count change** (plan 18) — YouTube
  now counts a public view from the first frame, so `vph24h` and
  `acceleration` no longer pair a snapshot from before 2026-08-24 with one
  from after it, `calibrate_maturity_curve` leaves out videos whose history
  straddles the date, and the channel's views chart marks it. Histories that
  start after the change (most installations) are not affected.
- README screenshots retaken for the 0.2.0 dashboard (21-item sidebar) and
  four new ones added: find a niche, alerts, repackaging, idea checker.

## [0.2.0] - 2026-10-04

Everything since the first public release: the tool count grew from 24 to 80
MCP tools plus 7 ready-made scenarios, the Chrome extension arrived, and the
project learned to plan your own videos, watch your channels and run for a
small team. Highlights:

- **On top of YouTube:** a Chrome extension with panels on watch and channel
  pages and multiplier / views-per-hour badges on every thumbnail list.
- **Plan your own video:** outlier to brief, metadata review with drafts
  linked to the published video, hook score, title scoring and suggestions,
  content gaps from comments, an idea checker.
- **Read a niche deeper:** niche trend (growing / stable / cooling /
  saturated), niche map, template risk, sponsor map, similar thumbnails and
  thumbnail styles, repackaging history, RPM as a range, YPP thresholds.
- **Stay on top of it:** alerts (new outlier, acceleration, title change,
  silence break, gone channel or video) on the dashboard, in the extension
  and to Telegram or a webhook, plus a morning digest; a free RSS watch for
  new uploads.
- **Your own channels:** real YouTube Analytics numbers through your own
  Google OAuth client, and your real RPM against the estimate.
- **Optional LLM** via OpenRouter or a local Ollama, with a daily budget.
- **Multi-user mode (experimental):** invited accounts, per-user data, quota
  shares, personal API tokens and alert settings; see SECURITY.md.
- **Foundations:** pgvector similarity search, a shared YouTube quota
  counter, HTTP rate limiting, PRIVACY.md and SECURITY.md.

### Added

- **Documentation map and HTTP API reference** — `docs/README.md` says which
  document answers what; `docs/http-api.md` lists all 103 HTTP routes with
  their parameters, cost and MCP twin. It is generated from
  `backend/interfaces/http/api.py` by `scripts/gen_http_api_docs.py`
  (`make api-docs`), and CI fails when a route changes without regenerating
  it. The rest of `docs/` stays internal and out of the repository.
- **Release packaging** — `scripts/package_extension.sh` (`make
  extension-zip`) packs the committed `extension/` into
  `dist/niche-finder-extension-v<version>.zip`, and pushing a `v*` tag runs
  `.github/workflows/release.yml`: it checks the tag against
  `extension/manifest.json` and this changelog, then publishes the GitHub
  release with this section as notes and the extension zip attached.
  CONTRIBUTING.md describes the steps.

- **"Алерты" screen and a "gone" mark on the channel screen** (plan 04
  follow-up) — the dashboard now lists the alert events of your watchlist
  (`/api/events`) with a filter by type, "only new" and "mark all read", so a
  channel or video that disappeared is visible without Telegram. A channel the
  API stopped returning (confirmed after two misses 6+ hours apart) gets a red
  note on its screen; `channel_analytics` returns `gone` for it. The demo seed
  has a gone channel and two events, and the smoke test opens both screens.

- **YPP-threshold filter in outlier search** (plan 12 follow-up) —
  `search_outliers` and `recently_added_outlier_channels` (MCP, HTTP, the
  "Найти нишу" and "Outlier-каналы" screens) take `min_ypp_status`
  (`subscribers-met` or `shorts-path-met`) to keep only channels past the
  YouTube Partner Program thresholds they visibly meet. Off by default; still
  not a monetization status, and a hidden subscriber count never passes.

- **Niche RPM range in the extension** (plan 06 follow-up) — the channel panel
  shows the niche-model revenue with its RPM range next to the Social Blade
  one, and both revenue lines say they are ads only, without sponsorships.
  README and the help screen explain the range.

- **SECURITY.md: why there is no CSRF token** — the `SameSite=Strict` session
  cookie plus the JSON / `X-NF-Client` rule (which forces a CORS preflight that
  only `chrome-extension://` passes) stand in for it; a new plain-form route
  or a GET that changes data would bypass both.

- **SECURITY.md** (plan 15, sub-stage 5.11) — how to report a problem, what
  protects single-user and multi-user mode, and the checklist before giving
  anyone an account: HTTPS with `NF_COOKIE_SECURE` and `NF_ALLOWED_HOSTS`,
  `OWN_TOKENS_KEY`, backups, and YouTube's one-project and 30-day storage
  rules for a service other people use.

- **Per-user LLM budget and rate limit** (plan 15, sub-stage 5.10) — the
  request rate limit counts per signed-in user instead of per address (sign-in
  itself stays limited per address), and each user may spend
  `NF_USER_DAILY_LLM_USD` (default 0.25, 0 = no limit) a day inside the
  installation-wide `LLM_DAILY_BUDGET_USD`. `llm_usage` is keyed by
  `(user_id, day, model)`; the worker's LLM work is booked to the local user.

- **Per-user notifications** (plan 15, sub-stage 5.9) — each user sets their
  own Telegram bot and chat or webhook and mode (instant / digest / both / off)
  on the "Данные" screen or `/api/settings/notifications`, with a test button.
  Alerts and the digest cover only that user's watchlist; `alert_deliveries`
  is keyed by `(user_id, alert_key)`, so every user gets each alert once. The
  bot token and webhook address are encrypted with `OWN_TOKENS_KEY` and never
  read back. A user's webhook must be https to a public address, checked when
  saved and again right before every send, with redirects off (no SSRF into the
  server's own network). The local user without saved settings keeps
  `NOTIFY_*` from `.env`; the worker delivers and sends digests per user.

- **Personal API tokens for the extension and MCP over HTTP** (plan 15,
  sub-stages 5.7–5.8) — with `NF_MULTI_USER=1`, `Authorization: Bearer nf_...`
  signs in like the session cookie. Tokens are created on the dashboard's MCP
  screen ("Личные токены") or with `cli.py create-token`, shown once, stored as
  SHA-256, listed with last use, and revocable; a token cannot manage tokens.
  The extension gets an "Токен доступа" setting (kept in
  `chrome.storage.local`, masked in the popup). The HTTP MCP services require a
  token (the SDK's `token_verifier`), tools act as the token's user and YouTube
  calls count against that user's budget; stdio stays local as user 1.

- **Per-user YouTube budgets** (plan 15, sub-stages 5.5–5.6) — in multi-user
  mode each signed-in user spends a daily share of the installation's one
  YouTube key (`NF_USER_DAILY_UNITS`, default 2000; `NF_USER_DAILY_SEARCH_CALLS`,
  default 20; 0 = no limit). The YouTube client checks the share before every
  call and counts it after, so every quota-spending route is covered; running
  out answers 429 to that user only. The worker and single-user mode are not
  limited. `/api/auth/me` and the dashboard footer show what is left.

- **Per-user data** (plan 15, sub-stage 5.4) — with `NF_MULTI_USER=1` every
  signed-in user has their own watchlist, swipe file, drafts, transcript queue
  and alert read marks, through HTTP; someone else's saved item or draft id
  answers 404. `tracked_channels` and `transcript_requests` are keyed by
  `(user_id, …)`, so two users can track the same channel or ask for the same
  transcript; the worker, the alert scan and thumbnail fingerprints handle
  each tracked channel once. Events stay shared facts with a new
  `events.channel_id` (filled from the payload for older rows): a user sees
  the events of their own watchlist, and "read" moved from `events.seen_at` to
  the personal `event_reads` (existing marks kept for the local user). A pasted
  transcript answers every user's request for it, and a new request for a video
  that already has one is ready at once. Isolation tests cover the application
  and HTTP layers. Single-user mode behaves as before.

- **Multi-user groundwork** (plan 15, sub-stages 5.1–5.3, experimental) —
  `NF_MULTI_USER` (off by default: nothing changes). When on, `/api` needs a
  sign-in: invited accounts (`cli.py create-user` / `set-password` / `users`,
  no sign-up page), scrypt password hashes, a session token in an HttpOnly
  SameSite=Strict cookie (`nf_session`, `Secure` with `NF_COOKIE_SECURE` or
  HTTPS) stored only as SHA-256, a sign-in screen and a sign-out button in the
  dashboard. New tables `users` (user 1 = "local") and `sessions`; every
  personal table (`tracked_channels`, `saved_items`, `drafts`,
  `alert_deliveries`, `transcript_requests`, `llm_usage`) gets `user_id`, with
  existing rows moved to user 1; `SCHEMA_VERSION` 4. "Мои каналы" already reads
  as the signed-in user. Separating the rest per user is sub-stage 5.4 — until
  then do not give accounts to other people. One YouTube API key per
  installation (YouTube API policies III.D.1.c), per-user quota budgets later.

- **Your own channels** (plan 14) — connect your channels through Google OAuth
  (your own "Desktop app" client, read-only scopes, loopback redirect with PKCE
  and a single-use 10-minute state) and get their real YouTube Analytics
  numbers: per video views, watch time, retention, subscribers gained and, on a
  monetized channel, revenue, CPM and RPM, for the last 28 days and lifetime.
  A "Мои каналы" screen (setup steps, connect, sync, disconnect), MCP
  `own_channels`, `own_vs_niche`, `rpm_calibration`, `sync_own_channels` (80
  tools), `/api/own/*` routes, a daily worker step (`WORKER_OWN_SYNC`) and real
  numbers in `draft_outcomes` (`ownMetrics`). `rpm_calibration` sets your real
  RPM against the plan-06 range. The refresh token is Fernet-encrypted with
  `OWN_TOKENS_KEY` from the environment and never returned or logged;
  disconnecting revokes it at Google and deletes everything. New personal
  tables `own_channels`, `own_video_metrics`, `own_oauth_pending` with
  `user_id` (plan 15 rules; `domain/users.py`). Thumbnail CTR is not in the
  Analytics API and is not shown. `cryptography` is now a direct dependency
  (it was already installed). `PRIVACY.md` updated; a security review of the
  flow found no issues.

- **Similar thumbnails** (plan 13) — MCP `similar_thumbnails`,
  `search_thumbnails`, `thumbnail_styles`, `embed_thumbnails` (76 tools),
  `/api/videos/{id}/similar-thumbnails`, `/api/thumbnails/search`,
  `/api/niches/{slug}/thumbnail-styles`, `POST /api/thumbnails/embed`, a
  "Стили превью" block with text search on the niche screen and "Похожие по
  картинке" in the brief. Thumbnails become local CLIP vectors (fastembed, ONNX
  on CPU, `Qdrant/clip-ViT-B-32-vision`; text search with
  `Qdrant/clip-ViT-B-32-text`): visually similar thumbnails, thumbnails matching
  a description, and a niche's k-means style groups with their median outlier
  score. New columns `videos.thumb_embedding` / `thumb_embedded_at` and, with
  pgvector, `thumb_embedding_v` with an HNSW index (search ~1 ms on a real
  database). An opt-in worker step (`WORKER_THUMB_EMBED`, off by default)
  reuses plan-05 archived images, downloads the rest from i.ytimg.com (zero API
  quota), keeps only the vector, re-embeds after a thumbnail swap and retries an
  unreadable image after 7 days. Models: ~0.6 GB of disk; the process peaks
  at ~0.7 GB RAM while embedding, ~60 ms of CPU per thumbnail (measured).
  `PRIVACY.md` updated.

- **YPP thresholds** (plan 12) — `yppEligibility` in `channel_analytics` (MCP,
  channel screen) and `inspect_channel` (the extension's channel panel): which
  YouTube Partner Program thresholds a channel visibly meets — 500 / 1,000
  subscribers, 3 uploads and 3M / 10M Shorts views in 90 days, the last two as
  a lower bound from the videos we collected; watch hours are not in the API
  and always shown as unknown. Statuses below-threshold / subscribers-met /
  shorts-path-met / unknown, each with a note that this is not a monetization
  status. The plan's spike found no dependable page signal (ads also run on
  non-partner channels; the "Join" button needs a signed-in viewer), so there
  is no "monetized" badge and nothing reads YouTube pages. No schema change,
  zero quota.

- **Scenarios for Claude** (plan 11) — seven MCP prompts in
  `interfaces/mcp/prompts.py`, listed by the client (Claude Desktop: "+" →
  niche-finder): `find_niche`, `analyze_competitor`, `validate_idea`,
  `outlier_to_video`, `weekly_review`, `find_content_gaps`, `niche_health`.
  Each puts a Russian step-by-step instruction into the chat with the exact
  tools in order, its quota cost and the shape of the answer; the ones that
  collect call `db_stats` first and stop when the quota is short, and reading
  comments or saving a draft waits for the user's consent. Arguments carry
  descriptions for the client menu. A "Сценарии" section on the dashboard's
  MCP screen. `tests/test_mcp_prompts.py` fails if a scenario names a tool that
  does not exist. The tool count stays 72.

- **Niche trend** (plan 08) — `saturation_v2` in `niche_overview` /
  `niche_overview_from_channel` (MCP and `/api/niches/{slug}`), a new
  `GET /api/niches/saturation` for every niche at once, a "Тренд ниши" block
  on the niche screen, a "Тренд" column in the niche list and on the cluster
  map. The last 30 days against the 90 before them, always in its own 120-day
  window whatever the requested period: supply (videos), demand (median views
  projected to day 30, so old and new videos compare; Shorts and long-form
  never mixed; videos under 3 days left out), entrants (channels created in
  the window) and newcomers (channels under 180 days with a video at
  outlier >= 2). Status growing / stable / cooling / saturated, or
  insufficient-data under 20 videos in either window; every signal is a reason
  with its numbers, and `confidence: low` marks a trend when most videos were
  not seen young, since it may only reflect how they were collected. Clusters
  show the trend but keep their opportunity order. The old `saturation_hint`
  stays. No schema change, zero quota.

- **Content gaps** (plan 03) — MCP `content_gaps`, `GET` / `POST
  /api/niches/{slug}/content-gaps` and a "Пробелы в контенте" card on the niche
  screen. Questions and requests from the comments of a niche's most-viewed
  videos that no collected video or pasted transcript answers yet: similar
  questions are grouped (embeddings, or normalised text without them), coverage
  comes from `check_ideas` over titles plus cosine over transcript chunks, and
  gaps are ranked by demand (askers, likes, how many videos they asked under)
  x (1 - coverage), each with example comments and the nearest existing video.
  With an LLM the questions come from `comment_insights`; without one, from
  rules (question mark or explicit request, English and Russian) — noisier,
  and the answer says so. `GET` and the MCP default read only the cache (zero
  quota); `POST` / `fetch=true` reads uncached videos' comments, 1 unit each,
  and stops at an exhausted quota, listing what it skipped. The no-LLM cache
  (`video_insights`, task `comment_questions`) keeps question text and like
  count only, never the author; `PRIVACY.md` updated. `build_brief` takes an
  optional `gap_topic`, and the card's "В бриф" opens `#/brief/<video>?gap=…`.
  No schema change.

- **Hook score** (plan 10) — MCP `hook_report` / `niche_hook_benchmark` /
  `score_hook_text`, `/api/videos/{id}/hook`, `/api/niches/{slug}/hook-benchmark`,
  `/api/hooks/score`, a "Крючок" button on the Transcripts screen and an
  intro checker on the title screen. Rates the text of the first ~30 seconds of
  a pasted transcript (or your own draft) 0-100 by rules, English and Russian:
  question to the viewer, concrete number, promised payoff, intrigue, "you",
  first-sentence length and pace, minus filler such as greetings and
  "subscribe", with up to three cautious tips. The niche benchmark contrasts
  outliers' hooks with ordinary videos and says "not enough data" until there
  are 10+ transcripts in each group. An optional LLM review (`llm=true`) is
  opt-in and cached in `video_insights`; a draft is never stored, logged or
  sent to an LLM. Text only, not the visual hook. No schema change, zero
  quota; `PRIVACY.md` updated.

- **Sponsor map** (plan 09) — MCP `sponsor_map` / `channel_sponsors`,
  `/api/niches/{slug}/sponsors`, `/api/channels/{id}/sponsors` and "Спонсоры"
  cards on the niche and channel screens. Finds sponsor mentions, promo codes
  and affiliate links in video descriptions (English and Russian, including
  `erid`-marked ads), normalises brands (`nordvpn.com/xyz` and `NordVPN` become
  `nordvpn`), and ignores social links, Patreon, calls to become the channel's
  own sponsor, disclaimers and the creator's own merch. A new worker step
  `sponsors` (`WORKER_SPONSORS`, `WORKER_SPONSORS_INTERVAL_MIN`,
  `WORKER_SPONSORS_LIMIT`) scans new or edited descriptions and backfills
  existing ones; shared tables `video_sponsors` and `sponsor_scan`. Zero quota.
  A lower bound: sponsorship not named in a description is invisible.

- **Outlier to brief** (plan 02) — MCP `build_brief`, `POST /api/briefs`, a
  `#/brief/<videoId>` dashboard screen, a "бриф" link on every video card and
  a "Бриф" button in the extension. One outlier becomes a working brief for
  your own video: its numbers and hook (first ~75 words of a pasted
  transcript), niche title patterns and best time, whether the topic is already
  covered (the source itself excluded), title candidates and why it worked
  (both need an LLM) and similar videos as thumbnail references. Every part
  that could not run is listed in `skipped` with the reason — without an LLM
  there is no angle and no new titles, and no template stands in. The preview
  writes nothing; saving stores a draft linked to the source
  (`drafts.source_video_id`) and queues a missing transcript. Zero quota.

- **Template-risk check** (plan 01) — MCP `template_risk` / `niche_template_risk`,
  `/api/channels/{id}/template-risk`, `/api/niches/{slug}/template-risk`, a card
  on the channel and niche screens and a line in the extension's channel panel.
  It scores how much a channel's last 30 uploads look like one template
  repeated — the pattern YouTube's "inauthentic content" policy targets — 0-100
  with the reasons: mean title similarity (embeddings), the share of titles
  reusing the same opening or ending, how uniform the video lengths are and how
  metronomic the upload rhythm is. Shorts and long-form are never mixed, fewer
  than 10 videos gives `insufficient-data`, and no single signal decides (a
  streamer with alike titles but varied lengths stays low). Thresholds come
  from the distribution over the channels in the local database; there is no
  labelled set of penalised channels, so it is a heuristic, not YouTube's
  verdict. The niche view counts low/medium/high channels and lists the most
  templated. Zero quota, no LLM, no schema change.

- **Morning digest** (plan 07) — `NOTIFY_MODE=digest` (or `both`) sends one
  Telegram/webhook message a day instead of one per alert: new outliers and
  accelerating videos on tracked channels, rising channels, title/thumbnail
  swaps and disappeared channels/videos, top 5 each, always under Telegram's
  4096-character limit. Sent once per local day, not before `DIGEST_HOUR`
  (8), remembered in `meta` so a restart never sends twice; an empty day is
  skipped (`DIGEST_SKIP_EMPTY`). Events the digest covered are marked
  delivered, so switching back to `instant` does not replay them. Default
  stays `instant` — nothing changes unless you opt in. Also: MCP
  `daily_digest`, `GET /api/digest`, `POST /api/digest/send` and
  `cli.py digest-send` (send now), and a "За сутки" block on the Overview.

- **RPM as a range** (plan 06) — every RPM estimate now also comes as
  `low / mid / high` (`estimatedRpmRange` on videos and categories,
  `rpm_range` and `monthly_usd_low/high` in a channel's `nicheModel`): the
  existing niche model is the middle, the band is half to double it, marked
  `confidence: "low"` with the reason (public RPM estimates for one niche
  disagree by up to 7x). Old fields and the `min_rpm`/`max_rpm` filters are
  unchanged and compare the middle. Dashboard cards, the categories table and
  the channel revenue tile show the range.

- **Repackaging — "Перепаковки"** (plan 05) — a dashboard screen, MCP tool
  `packaging_changes` and `/api/packaging` for title and thumbnail swaps after
  publishing: old vs new title or the archived before/after thumbnails side by
  side, with views per hour 48h before vs after (an observed effect, not a
  cause). Thumbnail swaps were never detected before: the API's thumbnail URL
  stays the same when the image changes, so the old URL comparison could not
  fire. The worker now downloads the 320x180 thumbnail of tracked channels'
  recent videos from `i.ytimg.com` every `WORKER_THUMBS_INTERVAL_MIN` (360; no
  API quota; `WORKER_THUMBS=0` turns it off), fingerprints it with a 64-bit
  dHash and keeps every distinct version in the new `thumbnail_archive` table,
  logging swaps as `video_changes.field = 'thumbnail_image'`. The extension's
  video panel shows "repackaged N times" with a link to the dashboard.
  `pillow` is now a direct dependency (it was already installed via fastembed).

- **"Gone" alerts** (plan 04) — `refresh_channels` / `refresh_stats` now
  record ids the YouTube API stopped returning in a new `gone_items` table
  (`gone` in their results). An item counts as gone only after two misses
  at least 6 hours apart; a failed or over-quota API call is never counted
  as a miss, and an item that shows up again is cleared. The alert scan
  emits `channel_gone` for tracked channels and `video_gone` for their
  videos that were already alerted as outliers, with the last known
  subscribers/views in the Telegram/webhook message and the extension popup.

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

- **Draft titles no longer land in the shared LLM cache** (plan 15, rule 4) —
  `score_titles` and `suggest_titles` carry the user's own titles and topic, so
  `llm_gateway.run(..., private=True)` skips `llm_cache` for them; budget and
  usage are still counted.
- **The daily digest lists only your own repackaging** in multi-user mode —
  it used the shared feed, so title and thumbnail swaps on channels only other
  users track showed up in your digest. `packaging_feed(user_id=...)` limits
  the feed to that user's active watchlist; single-user mode is unchanged.
- **Extension numbers ending in zero** — the number formatter stripped zeros
  from whole numbers too, so a $1000 revenue estimate read "$1" and 50% Shorts
  read "5%". Only zeros after the decimal comma are stripped now.

- **Plan 15 review notes** — untracking a channel now stops its Telegram/webhook
  alerts and digest lines (the feed keeps its history); the worker syncs every
  user's connected own channels, not only the local user's; the worker's LLM
  spend is booked to a "system" user (0) instead of eating the local user's
  personal budget; changing a password also revokes that user's API tokens.
  `SECURITY.md` lists the known limits of multi-user mode.

- **Own-channel connect could be finished by someone else's browser** (plan 14,
  found by the plan-15 security review) — the OAuth state was not bound to the
  browser that started it, so a consent link forwarded to another user would
  attach their channel to the sender's account. The start now sets a
  short-lived HttpOnly `nf_oauth_state` cookie (SameSite=Lax, callback path
  only) that the callback must match; the return address follows the loopback
  host the dashboard is open on (`localhost` vs `127.0.0.1`) and is stored
  with the state so the token exchange repeats it.

- **Slow loads on large windows** — `load_window` looked up the category title
  with one database query per video (3573 on a 120-day window, about 90% of its
  time). It now looks each (category, region) pair up once per call: a
  120-day window, the `trending-us` overview, the cluster map and the niche
  trend list went from 4-5 s to about 0.4 s on a real database. Same titles,
  no global cache (categories can still be renamed by `refresh_categories`).

- The categories table showed the niche's base RPM (before the 0.70
  monetisation discount) while video cards showed the discounted one, so the
  same niche had two different numbers; both now show the discounted range.

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

- **README rewritten as the project's front page** — what it is and why, the
  features grouped by job, screenshots, a five-minute quick start (key,
  Docker, Claude Desktop, the extension from a release zip), an updated
  architecture diagram and a documentation table. The details moved to the
  part READMEs instead of being repeated.
- **Docs brought in line with the code** — `frontend/README.md` describes the
  "Мои каналы" screen and `hook_view.js`; `extension/README.md` explains
  installing from a release zip and no longer says `search.list` costs 100
  units; `backend/README.md` copies `.env.example` from the project root in
  the no-Docker setup and states Python 3.10+ (Docker and CI use 3.12);
  `.env.example` documents `LLM_HOOK_TTL_DAYS` and
  `WORKER_DIGEST_CHECK_INTERVAL_MIN`, which the code already read.
- Extension version 0.2.0.
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
  nothing, and a run that finishes after a newer push to `main` leaves the
  badge to that newer run. `assets/coverage.json` is gone from `main`, and the coverage
  badges in `README.md` and `backend/README.md` read the `badges` branch and
  link to the CI workflow runs.
- Split `frontend/app.js` into `router.js`, `shared.js` and one module per
  screen under `frontend/screens/`; no behavior change. Added a
  headless-Chromium smoke test of every dashboard screen (`make
  frontend-test`, CI job `frontend-smoke`).

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

[Unreleased]: https://github.com/pandich93/youtube-niche-finder/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/pandich93/youtube-niche-finder/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/pandich93/youtube-niche-finder/releases/tag/v0.1.0

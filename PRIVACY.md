# Privacy

niche-finder is self-hosted software. You run it on your own machine, against
your own Postgres, with your own YouTube API key. There is no niche-finder
service, no account, and no server of ours for your data to reach — so for the
purposes of any privacy regulation, **you are the data controller and we are
not a processor**. Nothing here is a promise about a service we operate; it is
a description of what the code does, which you can verify yourself.

## No telemetry

The project collects no usage data, no crash reports and no analytics. There
is no opt-out because there is nothing to opt out of: `grep -ri
"telemetry\|sentry\|posthog\|analytics" backend/ frontend/ extension/` returns
nothing.

## What is stored, and where

Everything lives in the Postgres database you point the app at (by default the
`niche-finder-postgres-data` Docker volume on your machine):

- **Public YouTube metadata** — videos, channels, titles, descriptions, tags,
  thumbnails, view/like/comment counts, and a time series of stats snapshots
  built by repeatedly re-reading those public counters.
- **Your own working data** — the watchlist (`tracked_channels`), the swipe
  file (`saved_items`), metadata drafts (`drafts`), and alert events
  (`events`).
- **Embeddings** — vectors computed locally from titles and descriptions for
  semantic search.
- **Thumbnail versions** (`thumbnail_archive`) — the 320x180 thumbnail image of
  tracked channels' recent videos, one copy per distinct version, so a
  replaced thumbnail can still be shown as "before".
- **Thumbnail vectors** (`videos.thumb_embedding`, plan 13, opt-in) — a
  512-number CLIP vector per thumbnail for "similar thumbnails" and thumbnail
  styles. Only the vector is kept, never the image (plan-05 archived images are
  reused when present).
- **Accounts** (plan 15, only with `NF_MULTI_USER=1`) — each user's email, a
  scrypt hash of the password (never the password), and the SHA-256 of each
  sign-in session token with its expiry (`users`, `sessions`). Personal rows
  carry the owner's `user_id`: the watchlist, swipe file, drafts, transcript
  queue and which alerts each user has read (`event_reads`) are visible only
  to their owner. Personal API tokens (`api_tokens`) are stored as SHA-256
  only; the extension keeps its token in `chrome.storage.local` and sends it
  only to the backend address you set. Each user's notification settings
  (`user_settings`): the Telegram chat id in clear, the bot token and webhook
  address encrypted with `OWN_TOKENS_KEY`.
- **Your own channels** (plan 14, only if you connect one) — the channel id and
  title, the granted scopes, the OAuth refresh token **encrypted** with
  `OWN_TOKENS_KEY` from your `.env` (Fernet; the key is never in the database),
  and per-video YouTube Analytics numbers (views, watch time, retention,
  subscribers gained, revenue, CPM) for the last 28 days and lifetime
  (`own_channels`, `own_video_metrics`). This is private data about you. It
  never leaves your database except as the read requests to Google below; the
  token is never logged or returned by the API. **Отключить** on the "Мои
  каналы" screen revokes the token at Google and deletes all of it; you can
  also remove access at https://myaccount.google.com/permissions.

The YouTube metadata is public information about other people's channels,
retrieved through YouTube's official API. It is not private data about you,
but it is data about third parties, and how you use it is governed by the
[YouTube Terms of Service](https://www.youtube.com/t/terms) and the
[YouTube API Services Terms](https://developers.google.com/youtube/terms/api-services-terms-of-service).

**Comments are not stored as themselves.** The `video_comments` tool reads
comment threads live from the API and hands them straight back to the caller;
nothing is written to the database (the one narrow exception, questions without
authors, is `content_gaps` below). This is deliberate — comments are
user-generated content attached to identifiable authors, and keeping a local
copy of them is a liability the tool does not need.

**Exception: `comment_insights` (stage 04), and only when you call it.**
This tool sends comment *text* (not author names) to OpenRouter to extract
pains/requests/video ideas, and caches the LLM's *output* (not the raw
comments) in `video_insights`. It never runs on its own — the worker has no
comment-fetching step, and neither the dashboard nor the MCP server call it
automatically. Like every other `llm_gateway` call, it is a no-op unless you
have already set `LLM_PROVIDER=openrouter`, and it additionally requires an
explicit click (dashboard) or tool call (MCP) naming a specific video —
there is no bulk or background mode.

**Second exception: `content_gaps` (plan 03), and only when you ask it to
fetch.** With `fetch=true` (the "Прочитать комментарии" button on a niche
screen) it reads the top comments of that niche's most-viewed videos, 1 quota
unit per video. Without an LLM it keeps, per video, only the comments that ask
a question or request a video -- their **text and like count, never the
author** -- in `video_insights` (task `comment_questions`), for
`LLM_INSIGHTS_TTL_DAYS`, so a repeat call does not spend quota again. With an
LLM it goes through `comment_insights` above and stores only that output.
The worker never calls it; without `fetch` it only reads that cache.

**Transcripts (stage 19) are never fetched — you paste them, and they stay
local.** There is no subtitle-download code anywhere in this project: you
copy the text off YouTube's own transcript panel and paste it into the
dashboard's Транскрипты screen (or via the MCP tool). The pasted text and
its chunk embeddings are stored only in your own Postgres
(`transcripts`/`transcript_chunks`) -- nothing about a transcript is sent
to YouTube, OpenRouter, or anywhere else. Chunk embeddings are computed by
the same local `fastembed` model everything else in this project uses (see
"Optional LLM enrichment" above for the one-time model download, the only
network call in this whole feature).

*One exception, opt-in and per video:* `hook_report(llm=true)` (the
"LLM-разбор" checkbox next to the "Крючок" button) sends the text of the
first ~30 seconds of ONE transcript to your configured LLM provider. It
never happens automatically, and it is a no-op with `LLM_PROVIDER=none`.
The cache (`video_insights`, task `hook`) stores only the model's answer and a
hash of that text, not the text itself. `score_hook_text` (the "Проверить
вступление" card) scores a draft intro in memory: it does not save it, log
it, or send it anywhere.

## How long it is kept

Nothing is deleted automatically. Rows you collected stay until you delete
them (see "Deleting everything" below).

Once a day the worker re-reads from the YouTube API every stored video and
channel not refreshed for 25 days (`REFRESH_STALE_DAYS`), oldest first, up to
`REFRESH_STALE_MAX_VIDEOS` videos and `REFRESH_STALE_MAX_CHANNELS` channels:
titles, descriptions, tags and counters are replaced with what YouTube shows
now, and a new snapshot is added. A video or channel the API no longer
returns is marked "gone" and kept. The "Данные" screen shows how many rows
are older than that. The history (stats snapshots, title and thumbnail
changes, archived thumbnails) is kept without limit; YouTube's API policies
allow that only with their approval, see SECURITY.md.

## Where network traffic goes

By default — `LLM_PROVIDER=none`, the setting nothing changes out of the
box — the application contacts exactly three external hosts:

| Host | Why | Sends your API key? |
|---|---|---|
| `www.googleapis.com` | YouTube Data API v3 — every quota-spending call | Yes |
| `www.youtube.com` | Channel RSS feeds (`/feeds/videos.xml`), used by the free upload watcher | No |
| `i.ytimg.com` | Thumbnail images of tracked channels' videos from the last `WORKER_FULL_PERIOD`, every `WORKER_THUMBS_INTERVAL_MIN`, to notice thumbnail swaps (the API cannot). Plain image GETs — Google sees which videos you check. `WORKER_THUMBS=0` turns it off | No |
| `i.ytimg.com` | Plan 13, **off by default**: with `WORKER_THUMB_EMBED=1` (or the "Посчитать векторы превью" button / `embed_thumbnails`) the thumbnails of any collected videos, to compute their CLIP vectors locally; the image is dropped after that | No |
| `accounts.google.com`, `oauth2.googleapis.com` | Plan 14, only when you connect your own channel: the sign-in page, then the code-for-token exchange, token refresh before each sync, and the revoke on disconnect | Your OAuth client id/secret and refresh token, to Google only |
| `youtubeanalytics.googleapis.com`, `www.googleapis.com` | Plan 14: your channel's Analytics reports (daily, and on "Обновить цифры") and which channel the token belongs to | The OAuth access token, to Google only |
| `huggingface.co` | Plan 13: the CLIP image model (~0.34 GB) the first time a thumbnail is embedded, and the CLIP text model (~0.25 GB) the first time thumbnails are searched by text; cached in the models volume after that | No |

Two more appear the first time you set the project up, and are not the running
application:

- **Hugging Face** — `fastembed` downloads the embedding model
  (`paraphrase-multilingual-MiniLM-L12-v2`) on first use and caches it under
  `HF_HOME=/models`. Build with `--build-arg PREFETCH_MODEL=1` to fetch it at
  image build time instead. After that, embeddings are computed locally and no
  text ever leaves your machine for them.
- **`auth.docker.io`** — `docker compose pull`, i.e. Docker itself.

If you see `www.w3.org` reported by an automated scanner, it is a false
positive: the string appears once, in
[`backend/infrastructure/youtube/rss.py`](backend/infrastructure/youtube/rss.py),
as the Atom XML namespace identifier. Namespace URIs are names, not addresses;
nothing fetches it.

## Optional LLM enrichment

Off by default (`LLM_PROVIDER=none`). Every call to
[`backend/application/llm_gateway.py`](backend/application/llm_gateway.py)
returns `None` without touching the network unless you set
`LLM_PROVIDER=openrouter` and an `OPENROUTER_API_KEY`.

| Host | Why | Sends your API key? |
|---|---|---|
| `openrouter.ai` | Chat completions for whatever feature calls `llm_gateway.run()` | Yes (`OPENROUTER_API_KEY`, never your YouTube key) |

What actually goes out is only what the calling code builds as `system`/
`user` text and a JSON schema — a title, a description, a short prompt about
one video or channel, or (only for `comment_insights`, see above) the text of
that video's comments — never your YouTube API key. Every request is cached
in Postgres (`llm_cache`, keyed by
task + model + normalized input) so the identical request is never sent
twice, and `llm_usage` tracks daily spend against `LLM_DAILY_BUDGET_USD`
(default $1.00/day) so a runaway loop can't run up an unbounded bill.

Leave `OPENROUTER_MODEL` unset and the request goes to whichever model in
`infrastructure/llm/openrouter.py:FREE_MODEL_FALLBACKS` answers first — all
of OpenRouter's free (`:free`) tier, $0 per token, so `llm_usage.cost_usd`
stays 0 and the daily budget effectively never triggers. Free models carry
their own OpenRouter-side rate limit (50 requests/day without purchased
credits, 1000/day with $10+), independent of `LLM_DAILY_BUDGET_USD`. Set
`OPENROUTER_MODEL` to pin one specific model — free or paid — instead.

**`LLM_PROVIDER=ollama` (stage 11) sends nothing anywhere.** `infrastructure/
llm/ollama.py` talks only to `OLLAMA_URL` — your own [Ollama](https://ollama.com)
install, whether that's the host machine (`127.0.0.1:11434`) or the optional
`ollama` service in `docker-compose.yml` (`--profile llm-local`, still on
your machine, just in its own container). No OpenRouter call, no external
host, no API key — every `llm_gateway.run()` request that would otherwise go
to `openrouter.ai` goes to that local server instead, and `llm_usage.cost_usd`
is always 0. The one exception is the model file itself: the first `ollama
pull <model>` downloads it from Ollama's own registry, same one-time
category as the `fastembed` download mentioned above.

## Alert delivery (Telegram / webhook)

Off by default -- the worker only writes detected events (outlier, acceleration,
title change, silence break) to Postgres; nothing is sent anywhere until you
set `NOTIFY_TELEGRAM_BOT_TOKEN` + `NOTIFY_TELEGRAM_CHAT_ID`, or
`NOTIFY_WEBHOOK_URL`, in `.env`.

| Host | Why | Sends your bot token? |
|---|---|---|
| `api.telegram.org` | `sendMessage` for each new alert (or a batched summary), and/or one daily digest when `NOTIFY_MODE` is `digest`/`both` | Yes, as part of the URL path -- masked in any logged error |
| your `NOTIFY_WEBHOOK_URL` | Same, as a POST body `{"text": ...}` | No |

What goes out is only the alert's own numbers and a video/channel title --
the same data `list_events`/the dashboard's alerts view already show you,
never comment text or anything from `comment_insights`. The bot token
itself is never logged (masked in every error path in
`infrastructure/notify/telegram.py`) and never written to the database;
`alert_deliveries` stores only which alert was sent and when, not the
message text.

## Your API key

`YOUTUBE_API_KEY` is read from `.env` at startup and used only as a query
parameter to `www.googleapis.com`. It is not logged, not written to the
database, and not sent anywhere else. `.env` is listed in `.gitignore`, so it
does not end up in git — but it is a plaintext file on your disk, so treat it
like any other credential and do not commit it or paste it into an issue.

## Network exposure

Every published port binds to `127.0.0.1` only — Postgres (`5433`), the
dashboard and HTTP API (`8080`), and the optional MCP HTTP transport (`8765`).
Nothing is reachable from your local network or the internet unless you change
that yourself. The HTTP API also applies a request limit
(`RATE_LIMIT_PER_MINUTE`, default 600); on a loopback-only service that is a
fuse against a looping client rather than a defence against outside traffic.

## The Chrome extension

The extension's `host_permissions` are limited to `http://127.0.0.1/*` and
`http://localhost/*`. It talks only to your own backend. It does not send
anything to any third party, and it has no permission to.

## Deleting everything

Stop the stack and drop the volume that holds the database:

```sh
docker compose down
docker volume rm niche-finder-postgres-data
```

To drop the cached embedding model as well:

```sh
docker volume rm niche-finder-models
```

Both are recreated empty on the next `docker compose up`. If you pointed the
app at your own Postgres via `NICHE_DATABASE_URL`, drop that database or
schema instead.

## Changes

This document describes the code as it stands. If the set of hosts contacted
or data stored changes, the change belongs in the same commit, and in
[CHANGELOG.md](CHANGELOG.md).

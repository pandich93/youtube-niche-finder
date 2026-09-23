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

The YouTube metadata is public information about other people's channels,
retrieved through YouTube's official API. It is not private data about you,
but it is data about third parties, and how you use it is governed by the
[YouTube Terms of Service](https://www.youtube.com/t/terms) and the
[YouTube API Services Terms](https://developers.google.com/youtube/terms/api-services-terms-of-service).

**Comments are not stored as themselves.** The `video_comments` tool reads
comment threads live from the API and hands them straight back to the caller;
nothing is written to the database. This is deliberate — comments are
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

## Where network traffic goes

By default — `LLM_PROVIDER=none`, the setting nothing changes out of the
box — the application contacts exactly two external hosts:

| Host | Why | Sends your API key? |
|---|---|---|
| `www.googleapis.com` | YouTube Data API v3 — every quota-spending call | Yes |
| `www.youtube.com` | Channel RSS feeds (`/feeds/videos.xml`), used by the free upload watcher | No |

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
| `api.telegram.org` | `sendMessage` for each new alert (or a batched summary) | Yes, as part of the URL path -- masked in any logged error |
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

# Run niche-finder yourself

The hosted version of niche-finder is not open yet. Until it is, you can run
your own copy: everything in it works today on your machine, with your own
free YouTube API key and a local PostgreSQL. This page is the install guide
that used to live in the project README.

- [Quick start](#quick-start)
- [Connect Claude Desktop](#connect-claude-desktop)
- [Install the Chrome extension](#install-the-chrome-extension)
- [Without Docker](#without-docker)
- [How it works](#how-it-works)
- [Your copy and your data](#your-copy-and-your-data)
- [Giving accounts to other people](#giving-accounts-to-other-people)

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

## Connect Claude Desktop

Add the MCP server to `claude_desktop_config.json` — the exact `docker run`
config is in [backend/README.md](../backend/README.md#running-with-docker-recommended),
and the dashboard's **MCP connection** screen walks you through it and checks
the connection. Then try one of the scenarios under "+" in Claude Desktop,
for example *find a niche*.

## Install the Chrome extension

1. Download `niche-finder-extension-<version>.zip` from the
   [latest release](https://github.com/pandich93/youtube-niche-finder/releases/latest)
   and unzip it (or use the `extension/` folder of your clone).
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and pick the folder.
3. Open any YouTube video — the panel appears in the right-hand column.

Details: [extension/README.md](../extension/README.md).

## Without Docker

```bash
make local-install            # venv + backend dependencies, once (Python 3.10+)
make dev                      # dashboard on http://localhost:8080
make local-run                # or: the MCP server on the host
```

You need your own Postgres; with the compose database, add
`NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5433/niches` to
`.env`. Why, and the other pitfalls:
[backend/README.md](../backend/README.md#running-without-docker).

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
[backend/README.md](../backend/README.md#structure).

## Your copy and your data

Your key, your database, your machine. There is no telemetry: by default the
app talks to exactly two hosts, `www.googleapis.com` and `www.youtube.com`
(RSS feeds). Everything stays in your Postgres; comments are read live and
not stored as themselves (only derived questions and LLM summaries are
cached); the dashboard and the extension talk only to `127.0.0.1`. The
optional LLM adds `openrouter.ai` only when you turn it on — or nothing at
all with a local Ollama. [PRIVACY.md](../PRIVACY.md) has the full picture,
including how to delete everything.

## Giving accounts to other people

A copy is single-user by default. `NF_MULTI_USER=1` turns on accounts with
their own watchlists, drafts and alerts, per-user API tokens and tariffs:
daily limits, count limits and features per tariff, edited by an admin on the
dashboard's **Админка** screen ([backend/README.md](../backend/README.md#tariffs-saas-sub-project-1-nf_multi_user1)).
Accounts are created from the command line:

```bash
make cli ARGS="create-user you@example.com --admin"   # asks for the password twice
make cli ARGS="create-user friend@example.com"
```

Read [SECURITY.md](../SECURITY.md) (HTTPS, `NF_COOKIE_SECURE`,
`OWN_TOKENS_KEY`, backups, YouTube's 30-day storage rule) before giving
anyone an account.

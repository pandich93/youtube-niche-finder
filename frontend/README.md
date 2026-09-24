# frontend

[![CI](https://img.shields.io/github/actions/workflow/status/pandich93/youtube-niche-finder/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/pandich93/youtube-niche-finder/actions/workflows/ci.yml)
[![No build step](https://img.shields.io/badge/build%20step-none-informational?style=flat-square)](index.html)
[![Vanilla JS](https://img.shields.io/badge/JS-ES%20modules-F7DF1E?style=flat-square&logo=javascript&logoColor=black)](app.js)
[![License: MIT](https://img.shields.io/github/license/pandich93/youtube-niche-finder?style=flat-square)](../LICENSE)

The dashboard for niche-finder — the same thing NexLev shows in its own
Niche Finder, but backed by your local database and with no subscription.

```bash
cd ~/Desktop/projects/youtube/analytic
docker compose build            # picked up fastapi + uvicorn
docker compose up -d web worker
open http://localhost:8080
```

Without Docker (the same dashboard, the same `frontend/`, reads files from
right here):

```bash
make local-install   # once: venv + backend dependencies
make dev             # uvicorn api:app --reload on http://localhost:8080
```

More on running without Docker —
[backend/README.md](../backend/README.md#running-without-docker).

## How it's built

No build step: plain ES modules, no npm, no `node_modules`, no build. Edit a
file and see it after a page reload — the folder is mounted into the
container read-only, no image rebuild needed.

| File | What's inside |
|---|---|
| `index.html` | shell: sidebar, header with global filters, screen container |
| `styles.css` | dark and light themes, components |
| `ui.js` | formatters, tooltips, toasts, and components (cards, tables, bars, chart) |
| `app.js` | screens and hash routing |

Data comes from `backend/api.py` (a shim over `interfaces/http/api.py`,
FastAPI), which calls exactly the same use cases as the MCP server. There's
no logic in the frontend — every metric is computed on the backend, the UI
just displays it.

## Screens

The "Niche" and "Period" global filters in the header apply to every screen
and are remembered in the browser.

### Overview

Everything at once, in one call to `/api/overview`: outlier channels,
upcoming competition, categories, keywords, viral videos.

![Overview](../assets/dashboard.jpg)

### Viral videos

Videos from small channels, with filters (subscribers, views, VSR, publish
window) and a funnel underneath: shows exactly which threshold filtered out
the results, instead of just an empty list.

![Viral videos](../assets/viral.jpg)

### Outlier channels

The best age-adjusted multiplier among a channel's videos in the window,
against the median of its previous uploads. Strength bands: <2x, 2–3x,
3–5x, 5–10x, >10x.

![Outlier channels](../assets/outliers.jpg)

### Categories

YouTube category rankings over a period, with a share shift against the
previous window of the same length — rank by views or by channel count.

![Categories](../assets/categories.jpg)

### Keywords

Trending phrases: momentum, lift, trendScore, share of videos, and median
views, with an example video for each phrase.

![Keywords](../assets/keywords.jpg)

### Channel tracker

The watchlist the worker builds snapshot history for, plus forms to add a
channel (by handle/URL) or collect by search query.

![Channel tracker](../assets/tracker.jpg)

### Niches

Everything collected under user-defined labels (a slug plus the original
query), with video counts and the last collection time for each niche.

![Niches](../assets/niches.jpg)

### Channel

A detailed breakdown of one channel: subscribers, views, median per video,
growth rate by window (24h/7d/30d), and a revenue estimate. The "views over
time" chart is built from the worker's snapshots, because the YouTube API
only ever returns the state "right now" — the screenshot below shows the
state with a single snapshot, before the chart has built up any history.

![Channel](../assets/channel.jpg)

### Data

Key, database, and history state (how many channels/videos/snapshots have
been collected), plus the same collection forms and a manual stats refresh.

![Data](../assets/data.jpg)

## Security

The service listens on `127.0.0.1` — your YouTube key lives inside the
container, and there's no reason to expose it to the local network. The
port is changed via `WEB_PORT` in `.env`. POST endpoints (`/api/collect/*`,
`/api/refresh`) spend quota, GET endpoints don't.

## Design

The palette is a validated set from the `dataviz` skill: magnitude is drawn
with a single series (blue), so no legend is needed; status colors are only
applied to deltas and always come with a sign and a label, so meaning never
rests on color alone. Dark and light themes are separate sets of values
tuned to their own background, not an automatic inversion.

## API

Live schema: `http://localhost:8080/api/docs`.

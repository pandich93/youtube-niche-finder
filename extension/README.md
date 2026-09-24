# Chrome extension — analytics on top of YouTube

A local replacement for the vidIQ / NexLev panels: the same metrics, but the
data comes from your own `niche-finder` backend, not someone else's cloud.
The extension never talks to the outside world — only to `127.0.0.1`.

## What it shows

**Video page** (right-hand column):

* outlier multiplier against the median of the channel's recent videos of
  the same format (Shorts are compared with Shorts), falling back to the
  channel's lifetime average when there isn't enough history — this is the
  NexLev metric;
* views per hour over the last 24 hours from our own snapshots, acceleration
  (day vs. week), views per subscriber, engagement;
* a day-30 views forecast along the maturation curve, and a revenue
  estimate;
* the video's tags — as a list and copied to the clipboard in one click;
* similar videos from your database (by embeddings), marking ones from the
  same channel;
* "Show comments" — a live fetch of up to 50 comments (1 quota unit) with how
  many arrived in the first 24 hours; raw text, no sentiment analysis;
* "Why did it take off?" — an LLM explanation of why the video beat its
  channel (`/api/video/{id}/why`, cached). The button only appears when an
  LLM provider is configured on the backend;
* buttons: "collect channel", "track", "save" (to the swipe file, see
  below), and a link to the dashboard.

**Channel page** (under the header): subscribers, average views per video,
growth estimate and momentum, revenue forecast, the channel's best
outliers, the best time to publish in your time zone, title phrases that
produce outliers, and similar channels. When background enrichment has
classified the channel, an AI-label chip shows faceless / on-camera and the
content format. The sections below the first block appear once the database
holds at least 5 of the channel's videos — until then, just press "Collect
channel". A "save" button stores the channel in the swipe file.

**Video lists** (search, home, recommendations, channel tab): a badge with
the multiplier on the thumbnail. The colour matches the outlier strength. An
optional second badge (blue, under the thumbnail) shows views per hour — over
24 hours when there's history, otherwise over the video's lifetime.

**Saving to the swipe file.** "Save" on a video or channel panel stores it via
`POST /api/saved` together with a snapshot of its metrics at that moment. The
saved items are listed on the dashboard's "Saved" screen (`#/saved`).

## Popup (click the extension icon)

Besides the settings below, the popup has:

* **Alerts** — events from the backend's alert scan: new outlier videos,
  view acceleration, title changes, and channels breaking a long silence. "Check now" triggers a scan
  (`POST /api/events/scan`) without waiting for the worker; "Mark as read"
  clears the unseen counter.
* **Draft metadata review** — the same check as the dashboard's "Metadata
  review" screen: enter a title, description, tags, niche and optionally a
  channel ID and get per-signal verdicts from your own corpus
  (`POST /api/metadata/review`); "Save draft" stores it (`POST /api/drafts`)
  so you can link it to the published video later from the dashboard.
* "Check connection" and a link to the dashboard.

## Installation

1. Start the project backend if it isn't running yet:
   `make dev` (without Docker) or `docker compose up -d postgres web`.
   Check: <http://127.0.0.1:8080/api/health>.
2. Open `chrome://extensions` and turn on "Developer mode".
3. "Load unpacked" → pick the `extension/` folder.
4. Open any video on YouTube. The panel appears in the right-hand column.

The extension icon's badge tells you the state. A red `!` means the backend
is unreachable: check that it's running and that the address in the
extension settings matches the port (default `http://127.0.0.1:8080`). A
blue number is the count of unseen alerts. The badge is refreshed via
`chrome.alarms` — backend health every 5 minutes, alerts every 10.

## Settings (click the icon)

| Setting | What it does |
|---|---|
| Backend address | if you changed `WEB_PORT`, enter the new address |
| Fetch from the YouTube API | the video isn't in the database — pull it with one request (1 unit) and save it. Turn off to work strictly from the collected database |
| Badges on cards | multipliers on thumbnails in lists |
| Outliers only | the badge appears from ×1.5 and up |
| Views-per-hour badge | a second, blue badge under the thumbnail with views per hour |

## What it costs in quota

Fetching one video costs 1 unit (`videos.list`), plus 1 unit for the channel
if it's unknown (`channels.list`). List badges cost 1 unit per 50 videos. For
comparison, one `search.list` costs 100 units, and the daily limit is
10,000. Everything fetched goes into the same Postgres, so viewing the same
page again is free: a video is re-read from the database until it goes stale
(6 hours), a channel — for a day. The ⟳ button in the panel header forces a
re-read.

The "Collect channel" button is more expensive: it walks the uploads
playlist (1 unit per 50 videos + 1 unit per 50 videos for stats), which is
why it's an explicit action. "Show comments" costs 1 unit per click.
"Why did it take off?" costs no quota — only an LLM call on a cache miss.

## How it's built

```
content.js     panels and badges, knows nothing about the network
     |  chrome.runtime.sendMessage
background.js  the only way out to the network + response cache + icon badge
     |  http://127.0.0.1:8080/api/*  (inspect, channels, video/why,
     |                                saved, events, metadata, drafts, ...)
backend/application/inspection.py (and the other use cases)
     |  Postgres first, on a miss — a targeted request to the YouTube Data API
```

Why the network lives only in the service worker: the YouTube page is served
over https, and a request to `http://127.0.0.1` from the page context is
treated by the browser as mixed content. The extension has its own origin,
and with `host_permissions` that request goes through normally.

Permissions: `storage` (settings, synced via `chrome.storage.sync`),
`alarms` (periodic health/alert checks for the icon badge), and host access
to `127.0.0.1` / `localhost` only.

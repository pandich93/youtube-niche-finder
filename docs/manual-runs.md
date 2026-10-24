# Manual runs by regular users (multi-user plan P2-11)

The worker does the server's own work on a schedule: refreshing stats, the
alert scan, AI labelling, thumbnail vectors, the niche map. Until P2-11 only
an admin could start any of it by hand (P0-02). Now an admin can let regular
users start **part** of it themselves, over **their own** watchlist and a
limited number of times an hour.

Off by default: `NF_USER_MANUAL_RUNS_PER_HOUR=0` keeps the old rule (a regular
user gets `403`, only an admin runs these). Nothing changes until the owner
decides.

## The limit

- Tariff limit **`manual_runs`** ("Ручные запуски в час"): runs per hour **per
  operation** per user. `0` = admins only (the default, from
  `NF_USER_MANUAL_RUNS_PER_HOUR`), a number = that many runs in any 60
  minutes, empty (`null`) = no limit. An admin sets it per tariff on the
  **Админка** screen like any other limit; the base tariff takes the `.env`
  value until then.
- Admins and single-user mode are never limited and keep the corpus-wide run.
- Past the limit the route answers **`429`** with `Retry-After` (seconds) and
  `{"error": "ManualRunLimit", "operation", "limit", "retryAfter", "nextAt",
  "detail"}`. The dashboard button turns into "Снова можно в HH:MM" and comes
  back on its own (`GET /api/manual-runs`).
- The counter is the table `manual_runs` in Postgres: it survives restarts and
  is the same for every `web` replica. The check and the insert run in one
  transaction under an advisory lock per (operation, user)
  (`db.lock_until_commit`, as the tariff count limits of P1-10), so two
  parallel requests cannot both take the last run. Rows older than the hour
  are removed on the next check. The table is personal (`PERSONAL_TABLES`):
  Row-Level Security covers it, and deleting an account empties it.
- A run that fails (no YouTube key, quota exhausted, an API error) gives its
  slot back.
- Spending is the user's, as with every request of theirs: YouTube units count
  against their daily budget and LLM dollars against their plan's `llm_usd`
  (`quota_owner`, set by `session_guard`). The two user operations below do
  not call the LLM at all.

## Decision per operation

| Operation | Route / MCP tool | What it spends | Scope | Who may run it by hand | Why |
|---|---|---|---|---|---|
| Refresh stats | `POST /api/refresh` / `refresh_stats` | YouTube quota: ~1 unit per 50 videos and per 50 channels | admin: recent videos of the whole corpus and every tracked channel; user: **only their watchlist** and its videos from the last 30 days (at most 1000) | admin always; user within `manual_runs` | "Refresh my channels now" is the common wish. Bounded by the user's tracked-channel cap and paid from their own daily budget; `scope`/`limit` from the body are ignored for users |
| Alert scan | `POST /api/events/scan` / `scan_for_alerts` | zero quota, database CPU | admin: every watchlist; user: **only their watchlist** | admin always; user within `manual_runs` | Cheap and bounded by the user's channels; events are shared facts the worker would raise anyway |
| AI channel labelling | `POST /api/enrich/channels` / `enrich_channels` | LLM budget | whole corpus (unlabelled channels anywhere) | admin only | Spends the shared daily LLM budget on the shared corpus; the worker does it |
| AI video tags | `POST /api/enrich/videos` / `tag_new_videos` | LLM budget | whole corpus | admin only | Same as above; also writes proposals only an admin resolves |
| Thumbnail vectors | `POST /api/thumbnails/embed` / `embed_thumbnails` | CPU (CLIP model), downloads from i.ytimg.com | whole corpus or one shared niche | admin only | Heavy CPU on the shared server; a niche is shared corpus too |
| Recompute the niche map | `POST /api/niche-clusters/recompute` | CPU (k-means over every channel) | rewrites the shared `niche_clusters` table | admin only | One global result for everyone; recomputing it for one user changes it for all |

The MCP tools stay admin-only for now (`MCP_ADMIN_TOOLS`): the limited
operations are for the dashboard and the extension's "check now"; an agent in
a loop would just hit the limit.

## Action log

Runs and refusals are **not** written to `audit_log`. A run is an ordinary use
of the user's own tariff, like collecting a channel, which is not logged
either; the `manual_runs` rows are the record of the last hour. A refusal by
the limit is a throttle, not a security event, and a looping client could fill
the log with them (`RATE_LIMIT_PER_MINUTE` allows hundreds a minute). Changing
the limit itself is a tariff edit, which is already logged.

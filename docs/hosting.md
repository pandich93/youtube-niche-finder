# Host niche-finder for other people

This page is for running one copy of niche-finder on a server, on your own
domain, for a group of people: a closed beta, a team, friends. For a copy that
only you use on your own machine, see [self-hosting.md](self-hosting.md).

The production setup is `docker-compose.prod.yml`, layered on top of the usual
`docker-compose.yml`. Compared with a local copy:

- **Caddy is the only thing exposed to the internet.** It listens on ports 80
  and 443 for your domain and gets and renews a Let's Encrypt certificate by
  itself. The dashboard (8080) and Postgres (5433) are not published at all.
- **Everyone signs in.** Accounts are on (`NF_MULTI_USER=1`), the sign-in
  cookie is `Secure`, and the API answers only to your domain. The request
  and sign-in limits count each visitor by their own address
  (`NF_TRUSTED_PROXIES`), not all of them as the proxy.
- **MCP works over HTTPS** at `https://YOUR-DOMAIN/mcp`, with a personal API
  token per person.
- **The database is backed up on a schedule.** Each dump is checked by a
  trial restore, and old ones are rotated out.

- [Before you start](#before-you-start)
- [First start](#first-start)
- [Connect an MCP client](#connect-an-mcp-client)
- [Backups](#backups)
- [Monitoring](#monitoring)
- [Restore from a backup](#restore-from-a-backup)
- [Updates](#updates)
- [Moving a local copy to the server](#moving-a-local-copy-to-the-server)
- [Troubleshooting](#troubleshooting)

## Before you start

- **A Linux server** with Docker Engine and Docker Compose 2.18 or newer
  (`docker compose version`), 2 GB of RAM or more (the embedding model for
  semantic search lives in memory), and 10 GB of disk for the database, the
  model and the backups.
- **A domain name** whose DNS `A` record (and `AAAA` record, if the server
  has IPv6) points at the server, for example `niches.example.com`.
- **Ports 80 and 443 open** in the server's firewall. Port 80 is needed for
  the certificate check and to redirect visitors to HTTPS.
- **A YouTube Data API v3 key.** The whole server shares this one key and its
  daily quota of 10,000 units. Each person's share is set by their tariff on
  the dashboard's **Admin** («Админка») screen.
- **Read [SECURITY.md](../SECURITY.md)** first, especially "What you have to
  do before you give anyone an account". Two of its points you have to settle
  yourself: YouTube's 30-day storage rule for a service other people use, and
  the privacy and terms pages.

## First start

```bash
git clone https://github.com/pandich93/youtube-niche-finder.git
cd youtube-niche-finder
cp .env.example .env
```

In `.env`, set at least these values:

| Setting | Value |
| --- | --- |
| `YOUTUBE_API_KEY` | your key |
| `NF_DOMAIN` | your domain, for example `niches.example.com` |
| `POSTGRES_PASSWORD` | a long random password. Set it **before** the first start: the database keeps the password it was created with. |
| `OWN_TOKENS_KEY` | an encryption key for the notification and channel secrets people save; how to generate it is just below |
| `NF_DEFAULT_LOCALE` | `en` for an English-speaking audience (`ru` by default) |
| `NF_SERVICE_NAME`, `NF_OPERATOR_NAME`, `NF_CONTACT_EMAIL` | who runs the service; shown on `/privacy` and `/terms` |

You don't need to set `NF_MULTI_USER`, `NF_COOKIE_SECURE`, `NF_ALLOWED_HOSTS`,
`NF_TRUSTED_PROXIES` or `MCP_PUBLIC_URL`: the production file sets them from
`NF_DOMAIN`. Compose refuses to start if `NF_DOMAIN` or `POSTGRES_PASSWORD` is
missing.

Generate `OWN_TOKENS_KEY` once and keep it only in `.env`. Losing it makes the
saved secrets unreadable:

```bash
docker compose build web
docker compose run --rm --no-deps web python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Rotating the key.** You can change `OWN_TOKENS_KEY` without losing what
people saved, for example after the key may have leaked:

1. Generate a new key with the command above.
2. In `.env`, move the current key into `OWN_TOKENS_KEY_OLD` and put the new
   one into `OWN_TOKENS_KEY`. `OWN_TOKENS_KEY_OLD` takes several keys,
   comma-separated. It is only ever used to read.
3. Restart with `make prod-up`. New secrets are now encrypted with the new
   key, and the old ones are still read with the old key.
4. See what would change: `make prod-cli ARGS="rotate-secrets"`. This is a dry
   run. It counts the values to re-encrypt and the ones no key reads.
5. Re-encrypt: `make prod-cli ARGS="rotate-secrets --apply"`. It prints how
   many values were re-encrypted and how many are unreadable. The exit code is
   1 while anything is unreadable.
6. When a dry run reports 0 to re-encrypt, empty `OWN_TOKENS_KEY_OLD` and
   restart again. Backups made before the rotation still need the old key, so
   keep it somewhere safe as long as you keep those backups.

A secret that no key reads is no longer dropped silently. The log says which
field of which account it was, never the value. The **Админка** screen shows
how many stored secrets the current keys cannot read. To fix it, put the
previous key back into `OWN_TOKENS_KEY_OLD`. Without that key, those people
have to reconnect their channels and enter their notification settings again.

Start everything and create your own admin account:

```bash
make prod-up
make prod-cli ARGS="create-user you@example.com --admin"   # asks for the password twice
```

Without `make` (`apt install make` adds it), use the same commands in full:

```bash
P="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$P up -d --build postgres web worker mcp-http proxy backup
$P exec web python cli.py create-user you@example.com --admin
```

Open `https://YOUR-DOMAIN` and sign in. The first start takes a minute or two
while Caddy gets the certificate. `make prod-logs` shows what is happening.

Accounts for other people are created the same way, without `--admin`, or on
the **Admin** screen, which shows a one-time password to hand over. People can
delete their own account on the **Plan** page.

## Connect an MCP client

Each person creates a personal API token on the dashboard (while signed in),
or you create one for them:

```bash
make prod-cli ARGS="create-token friend@example.com --name claude"
```

The client connects to `https://YOUR-DOMAIN/mcp` over streamable HTTP and
sends the header `Authorization: Bearer nf_…`. Without a valid token the
server answers 401. The tools then act as that person: their watchlist, their
drafts, their share of the quota.

## Backups

The `backup` service runs `pg_dump` every `BACKUP_INTERVAL_HOURS` (24 by
default). It writes `niches-YYYYmmdd-HHMMSS.dump` files into `BACKUP_DIR`
(`./backups` next to the compose files) and keeps the newest `BACKUP_KEEP`
(14 by default).

Before a dump is kept, it is restored into a scratch database on the same
server, and its tables are counted against the live database. This way a dump
that cannot be restored shows up in the log today, not on the day you need it:

```text
backup: restore check passed: 45 tables
backup: saved niches-20261023-214352.dump (72K)
```

A failed check logs `RESTORE CHECK FAILED` with the reason. The dump is then
kept as `….dump.failed` for inspection and is not counted as a backup. Check
the log now and then (`docker logs niche-finder-backup`), or point your
monitoring at it.

To make a backup right now, for example before an update:

```bash
make prod-backup
```

**Copy the backups off the server.** A backup on the same disk is lost
together with that disk. Anything that copies a folder works, for example a
nightly `rsync -a server:youtube-niche-finder/backups/ ./niche-backups/` from
another machine. The dumps contain accounts, sessions and encrypted secrets:
protect them like the server itself. Keep a copy of `.env` (with
`OWN_TOKENS_KEY`) somewhere safe too: without the key, the restored secrets
cannot be read. After a key rotation, keep the old key as long as you keep
backups made before it.

## Monitoring

**Logs.** The production file writes logs as one JSON object per line
(`NF_LOG_FORMAT=json`), to stderr. Secrets are masked, and lines carry no
emails or IP addresses. A request line holds the method, the route template
(for example `/api/videos/{video_id}`), the status, the duration and the
account id. `make prod-logs` shows them; any log shipper that reads Docker
logs can collect them.

**Operator alerts.** Some problems need you, not the people using the
server: the shared YouTube quota is spent for the day, or a worker step
failed. These alerts go to the Telegram bot or webhook in `.env`
(`NOTIFY_TELEGRAM_*` or `NOTIFY_WEBHOOK_URL`), at most once per kind every
`NF_OPERATOR_ALERT_HOURS` (6 by default). They are always logged as warnings
too. `NF_OPERATOR_ALERTS=0` turns them off.

**Metrics.** Set `NF_METRICS_TOKEN` to a long random string and restart
`web`. Then `http://web:8080/metrics` answers in Prometheus text format to
`Authorization: Bearer <the token>`. Caddy answers 404 for `/metrics` from
outside, so only a Prometheus on the compose network can reach it:

```yaml
scrape_configs:
  - job_name: niche-finder
    metrics_path: /metrics
    authorization: {credentials: "<NF_METRICS_TOKEN>"}
    static_configs: [{targets: ["web:8080"]}]
```

What it reports, all numbers and no names:

| Metric | Meaning |
| --- | --- |
| `nf_http_requests_total{route,method,status}`, `nf_http_request_duration_seconds` | dashboard and API requests |
| `nf_youtube_units_used_today`, `nf_youtube_units_daily_limit`, `nf_youtube_search_calls_today` | the shared YouTube quota |
| `nf_llm_cost_usd_today`, `nf_llm_daily_budget_usd` | the LLM spend |
| `nf_worker_last_cycle_timestamp_seconds` | when the worker last finished a cycle |
| `nf_worker_step_last_ok{step}`, `nf_worker_step_last_duration_seconds{step}`, `nf_worker_step_failures{step}` | each worker step |
| `nf_users{state}` | accounts, active and disabled |

The worker cannot report its own death, so the alert for it comes from
Prometheus. After each cycle the worker sleeps for five minutes; set the
threshold well above your longest cycle (the daily refresh is the long one,
and `nf_worker_step_last_duration_seconds` shows how long it takes):

```yaml
groups:
  - name: niche-finder
    rules:
      - alert: WorkerStopped
        expr: time() - nf_worker_last_cycle_timestamp_seconds > 3600
      - alert: ApiErrors
        expr: sum(rate(nf_http_requests_total{status=~"5.."}[10m])) > 0.1
      - alert: YouTubeQuotaNearlySpent
        expr: nf_youtube_units_used_today / nf_youtube_units_daily_limit > 0.9
```

**Sentry.** Set `SENTRY_DSN` (and `SENTRY_ENVIRONMENT` if you want) to send
unhandled errors from `web`, `worker` and `mcp-http`. Cookies, headers, query
strings and addresses are removed before sending, and secrets are masked.

## Restore from a backup

This **replaces** the current database with the backup.

```bash
P="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$P stop web worker mcp-http backup
$P exec -T postgres dropdb -U niches niches
$P exec -T postgres createdb -U niches niches
$P exec -T postgres pg_restore -U niches -d niches --no-owner < backups/niches-YYYYmmdd-HHMMSS.dump
$P up -d web worker mcp-http backup
```

If you changed `POSTGRES_USER` or `POSTGRES_DB`, use your values instead of
`niches`. On a new server, run `$P up -d postgres` first, then the commands
above.

## Updates

```bash
make prod-backup
git pull
make prod-up
```

`prod-up` rebuilds the image when the dependencies have changed and restarts
the services. Schema changes are applied automatically when the services start.

## Moving a local copy to the server

To take a local copy's data along, make a dump on your machine:

```bash
docker compose exec -T postgres pg_dump -U niches -Fc niches > niches-local.dump
```

Copy the dump to the server and restore it there as in [Restore from a backup](#restore-from-a-backup).
The server's own `POSTGRES_PASSWORD` stays in force: it belongs to the
server's database, not to the dump. The local data belongs to the user
`local`; give it a password with `make prod-cli ARGS="set-password local"`
to sign in as it.

If you already started the server once with the default password `niches` and
want to change it, change it in the database first, then in `.env`:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec postgres \
  psql -U niches -d niches -c "ALTER USER niches PASSWORD 'the-new-password'"
```

## Troubleshooting

- **No certificate, or "connection refused".** Check that the domain resolves
  to the server (`dig +short YOUR-DOMAIN`) and that ports 80 and 443 are open.
  `docker logs niche-finder-proxy` shows Caddy's attempts. Let's Encrypt
  limits repeated failures, so fix DNS before retrying many times.
- **"Host header not allowed".** The address in the browser is not
  `NF_DOMAIN`. For a second name, add it to `NF_ALLOWED_HOSTS` and to the site
  address in `infra/caddy/Caddyfile.prod`.
- **All visitors share one sign-in limit.** The API sees every request coming
  from an address like `172.18.0.1`. This happens when Docker's userland proxy
  forwards the traffic, typically for IPv6 visitors on a server where Docker
  has no IPv6. Set `"userland-proxy": false` in `/etc/docker/daemon.json`, or
  turn on IPv6 for Docker, and restart Docker.
- **Behind Cloudflare or another proxy in front of Caddy.** This setup does
  not cover that case. Caddy would see the outer proxy's addresses as the
  visitors. Its `trusted_proxies` option has to name that proxy first.

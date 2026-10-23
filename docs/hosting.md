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
cannot be read.

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

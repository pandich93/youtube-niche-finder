# Security

niche-finder is built to run on your own machine for yourself. Everything
listens on `127.0.0.1`, and nobody else can reach it. Multi-user mode
(`NF_MULTI_USER=1`, plan 15) lets one server work for several people. This file
covers what protects each mode and what you have to do yourself before you let
anyone else in.

## Reporting a problem

Open a private security advisory on GitHub
(Security → Advisories → Report a vulnerability). Do not open a public issue.
Please include the steps to reproduce and what an attacker gains.

Fixes land on `main` and ship in the next release; only the
[latest release](https://github.com/pandich93/youtube-niche-finder/releases/latest)
and `main` are supported.

## Single-user mode (default)

- **No accounts.** The dashboard, API and MCP work for whoever can reach them,
  so they are bound to `127.0.0.1`.
- **Hostile web pages are blocked.** Every request must carry an allow-listed
  `Host` header, which stops DNS rebinding. Every POST, PUT, PATCH or DELETE
  must be JSON or carry `X-NF-Client`, so a cross-site form or a simple
  cross-origin POST is refused. CORS answers only `chrome-extension://`.
- **Secrets stay local.** Your YouTube key lives in `.env`. The OAuth refresh
  tokens of your own channels are encrypted with `OWN_TOKENS_KEY` and never
  returned or logged. The key can be rotated: earlier keys go into
  `OWN_TOKENS_KEY_OLD` (read-only), and `cli.py rotate-secrets --apply`
  re-encrypts the stored values. A value no key reads is logged without its
  content and counted on the admin screen
  ([docs/hosting.md](docs/hosting.md)).
- **The YouTube key stays out of URLs and logs.**
  - The key goes in the `X-Goog-Api-Key` header, never in the request URL
    (`_get` in `backend/infrastructure/youtube/client.py`).
  - A network error is re-raised as `YouTubeUnreachable` without the URL.
  - The worker log and HTTP 500 answers pass through
    `backend/infrastructure/redact.py`, which masks `key=`, `access_token=`,
    `refresh_token=` and `client_secret=` parameters, `AIza...` keys and the
    configured secrets.
  - A key that ever appeared in a log (older versions put it in the URL) should
    be rotated in Google Cloud Console.

## Multi-user mode (`NF_MULTI_USER=1`)

What the server does for you:

- **Accounts.**
  - Without mail (`SMTP_HOST`, `SMTP_FROM`) accounts exist only by invitation:
    `cli.py create-user` or the admin screen. With mail and
    `NF_REGISTRATION=open` (the default) anyone can sign up, but only with an
    address they can read: the password is set from a one-time link mailed to
    it, so nobody can take someone else's email.
  - Sign-up and "forgot password" answer the same whether the address is known
    or not. A known address gets a "you already have an account" email with a
    reset link instead of a second account; a blocked one gets nothing.
  - Mailed links (sign-up and email change: 24 hours, reset: 1 hour) are
    random, single use, and the database keeps only their SHA-256
    (`email_tokens`). The token travels in the URL fragment, so it is not in
    server or proxy logs. A new link of the same kind replaces the old one.
  - At most `NF_MAIL_PER_IP_HOUR` (10) emails per client address and
    `NF_MAIL_PER_EMAIL_HOUR` (3) per recipient each hour, so the server cannot
    be used to flood an inbox. The links point at `NF_PUBLIC_URL` or
    `https://NF_DOMAIN`, never at a host a request names, when either is set.
  - Changing the email needs the password, and the change happens only when
    the link mailed to the new address is opened.
  - Passwords are scrypt hashes and must be at least 10 characters. Common
    long passwords (`password123`, `1234567890`, keyboard and alphabet runs,
    one repeated character) and passwords containing the email's name are
    refused. Nothing is sent to a breach service.
  - A wrong password and an unknown email get the same answer.
  - **Guessing is slowed down.** After 5 failed sign-ins for one email in
    15 minutes, the next attempt waits 1 minute, then 2, 4, up to 15. This
    applies to the right password too, so guessing cannot simply continue.
    After 30 failures from one address (any emails), sign-in from it is closed
    for 15 minutes. The answer is HTTP 429 with `Retry-After`, the same for a
    known and an unknown email. There is no permanent lock, which would let
    anyone lock out someone else's account. The counters live in Postgres
    (`login_attempts`), so restarts do not reset them. The limits can be tuned
    with `NF_LOGIN_MAX_FAILS`, `NF_LOGIN_MAX_IP_FAILS` and `NF_LOGIN_WINDOW_MIN`.
- **Sessions.**
  - A session is a random token in an `HttpOnly`, `SameSite=Strict` cookie, and
    the database keeps only its SHA-256.
  - Changing a password ends every session of that user (the profile screen
    keeps the browser it was changed in signed in); a reset by link ends all
    of them.
  - A session ends 14 days after the last activity (`NF_SESSION_IDLE_DAYS`)
    and 90 days after the sign-in whatever happens (`NF_SESSION_MAX_DAYS`).
    The idle expiry moves forward at most once per 15 minutes
    (`NF_SESSION_TOUCH_MINUTES`), so a request only reads the database. The
    absolute limit is checked on every request, so lowering it also ends
    older sessions.
  - Every sign-in, sign-up, password reset and password change starts a new
    token; an old one is never taken over. The cookie is not rotated on its
    own while the session lives: it never reaches JavaScript, a URL or a log,
    the idle and absolute limits bound a stolen one, and a mid-session swap
    would race the dashboard's parallel requests (one that left with the old
    token would get 401) or need a grace window during which both tokens
    work.
  - The profile lists your sessions: when they started and were last active,
    the client address (as `NF_TRUSTED_PROXIES` resolves it) and the browser
    (User-Agent, cut to 200 characters). You can end one or all but the
    current. Tokens and hashes are never shown. Only a dashboard sign-in can
    do this, never an API token. These rows go when the session ends and with
    the account.
  - Without a session, `/api` answers 401. Only sign-in, sign-up and password
    reset by link, email confirmation, `/api/health` and the Google OAuth
    return are open.
- **No CSRF token, on purpose.** Two layers stand in for it:
  - The browser never sends the `SameSite=Strict` session cookie on a request
    that starts on another site.
  - Every POST, PUT, PATCH or DELETE must be JSON or carry `X-NF-Client`. Both
    force a CORS preflight, and CORS allows only `chrome-extension://`, so
    another site cannot send such a request even if a browser mishandled the
    cookie.

  A token would guard against the same thing a third time. The API token
  (`Authorization: Bearer`) is never sent by a browser on its own, so it
  carries no CSRF risk. Keep this in mind if you add a route that accepts a
  plain form or a GET that changes data: it would bypass both layers.
- **Personal API tokens.** They are for the extension and for MCP over HTTP
  (`Authorization: Bearer nf_...`).
  - The token is shown once and stored as SHA-256.
  - It can be revoked, and it cannot create or list other tokens.
  - It expires: 90 days by default, chosen when it is made (1 to 365 days, or
    none). An expired token is refused like a wrong one.
  - It does only what it was made for. `read` covers GET routes, `write`
    covers routes that change something, and `mcp` covers MCP over HTTP. An
    extension that only reads needs no `write`.
- **Separated data.** Each user sees and changes only their own:
  - watchlist, swipe file, drafts and transcript queue;
  - alert read marks, connected channels, notification settings and API tokens.

  Public YouTube data (channels, videos, niches, transcripts, alert events) is
  shared. A user sees only the alert events of their own watchlist.
- **Limits.**
  - YouTube quota: each user has a daily share of the installation's one key
    (`NF_USER_DAILY_UNITS`, `NF_USER_DAILY_SEARCH_CALLS`). Running out stops
    only that user.
  - The LLM budget (`NF_USER_DAILY_LLM_USD`) and the request rate limit also
    count per user.
- **Notifications.**
  - A user's webhook must be https to a public address. It is checked when
    saved and again before every send, and redirects are off, so it cannot
    reach into the server's own network.
  - Bot tokens and webhook addresses are encrypted with `OWN_TOKENS_KEY`.
- **Connecting your own channel.** It can be finished only in the browser that
  started it, thanks to a short-lived state cookie. So nobody can attach your
  channel to their account by sending you a link.

What you have to do before you give anyone an account:

1. **Put it behind HTTPS.** `docker-compose.prod.yml` (`make prod-up`, see
   [docs/hosting.md](docs/hosting.md)) does everything in this point for you.
   By hand: use a reverse proxy (Caddy, nginx) with a real
   certificate. Set `NF_COOKIE_SECURE=1` and add your domain to
   `NF_ALLOWED_HOSTS`. Do not publish port 8080 or Postgres (5433) to the
   internet. Set `NF_TRUSTED_PROXIES` to the proxy's address or subnet (for
   example the compose network, `172.16.0.0/12`). Then the request limit and
   the sign-in limits count each real client from `X-Forwarded-For` instead
   of lumping everyone together as the proxy. Never use `*` (it is ignored):
   anyone could then claim any address.
2. **Set `OWN_TOKENS_KEY`** (a Fernet key) and keep it only in `.env`. Losing
   it makes stored tokens unreadable. Leaking it exposes them.
3. **Back up Postgres.** It now holds accounts, sessions and encrypted
   secrets. Protect the backups like the server itself. The production setup
   dumps it on a schedule and checks each dump by a trial restore; copy the
   dumps off the server ([docs/hosting.md](docs/hosting.md#backups)).
4. **Give each person their own account.** Admins are marked `--admin`; over
   HTTP their extra powers are tariffs and accounts: creating, disabling and
   deleting them (`/api/admin/*`, signed-in dashboard sessions only).
   Otherwise accounts are created from the command line on the server.
5. **Read YouTube's API policies for a service others use.**
   - III.D.1.c: one API project per application. That is why the installation
     uses one key, and users do not bring their own.
   - III.E.4.d: non-authorized YouTube data may be kept at most 30 days, then
     refreshed or deleted. The worker re-reads every stored video and channel
     not refreshed for 25 days (`REFRESH_STALE_DAYS`, under a daily cap), so
     current rows stay fresh. But niche-finder never deletes anything: the
     stats history, title and thumbnail changes and archived thumbnails are
     kept without limit.
   - Keeping statistics longer, and computing scores from them, is allowed
     only to API clients approved under YouTube's derived-metrics policy
     (statistics and derived metrics up to 36 months; titles, descriptions
     and other text still 30 days):
     <https://developers.google.com/youtube/terms/derived-metrics-policy>.
     The approval is per Google Cloud project, so each installation's owner
     applies for their own.
   - For yourself this is your own risk. For a service other people use, you
     must solve it before you open the service.
6. **Letting customers connect their own channels (plan 25).** Use
   `OWN_OAUTH_MODE=web` with one OAuth client of type Web application and an
   https `OWN_OAUTH_REDIRECT_URI`; fill `NF_SERVICE_NAME`, `NF_OPERATOR_NAME`
   and `NF_CONTACT_EMAIL` so `/privacy` and `/terms` name you and a contact.
   Pass Google's OAuth app verification before inviting more than 100 people,
   and answer deletion requests sent to that contact (the privacy page promises
   7 days): `cli.py delete-user EMAIL --yes`, or Delete on the Admin page.
   People can also delete their own account on the Plan page. Back up first
   if you delete many at once: deleting cannot be undone.
7. **Keep MCP over HTTP behind the TLS front door (`mcp-https`)** and set
   `MCP_PUBLIC_URL` to the address clients use. MCP over stdio is local only.

Known limits (by design, worth knowing before you invite anyone):

- **Shared data has an owner.** A video's transcript (one shared copy) and its
  curated tags are shared by everyone. Any signed-in user may add them, but
  only the person who added a transcript or a tag, or an admin, may replace,
  re-index or remove it; pasting over someone else's transcript returns the
  existing one (`alreadyExists`). Rows from before this rule, the worker and
  the CLI have no owner and are the admins'. Accepting or rejecting the
  background LLM's proposed tags is for admins. Naming niches by collecting
  them is still open to everyone.
- **The server's own work is for admins.** Refreshing stats, AI enrichment,
  the alert scan, thumbnail vectors and recomputing the niche map run on the
  worker's schedule; starting them by hand (`/api/refresh`, `/api/enrich/*`,
  `/api/events/scan`, `/api/thumbnails/embed`, `/api/niche-clusters/recompute`
  and the MCP tools `refresh_stats`, `enrich_channels`, `tag_new_videos`,
  `scan_for_alerts`, `embed_thumbnails`) needs an admin, by session or API
  token. The first author of a transcript can still paste junk or a prompt
  injection into it, and the LLM hook review of that text is cached for
  everyone; an admin can replace it.
- **Admins.** `users.is_admin` guards `/api/admin/*` (tariffs, users'
  tariffs, creating, blocking and deleting accounts): only an admin signed in
  to the dashboard, never an API token. Admins are not limited by tariffs.
  Blocking an account (`users.disabled_at`) ends its sessions and API tokens
  (MCP included) at once, and it cannot sign in until unblocked. An admin
  cannot block or delete themself there, the `local` user (the data from
  before accounts) can be blocked but not deleted, and the server always
  keeps one active admin. Each action is logged in `admin_actions`.
- **Tokens made before expiry existed do not expire.** New tokens live 90 days
  by default (1 to 365 days, or no expiry if you choose it). Older tokens keep
  every scope and no expiry until revoked; the MCP screen marks them
  "no expiry". Changing a password revokes all of that user's tokens and
  sessions.
- **MCP over HTTP needs `NF_MULTI_USER=1` to ask for a token.** Without that
  flag anyone who reaches it acts as user 1, so `server.py` refuses to listen
  on anything but loopback (`MCP_HOST` defaults to `127.0.0.1`) unless
  `MCP_ALLOW_UNAUTHENTICATED=1` says that is intended. The compose `mcp-http`
  service sets it: its port is not published, and only `mcp-https`, which
  listens on 127.0.0.1, reaches it. The production file turns it off.
- **Background work is shared; what you see is yours.** Thumbnail
  fingerprints, title and thumbnail swaps and stats refreshes run for every
  channel someone tracks. With accounts on, the feeds built from them show
  only your own watchlist: title changes, the repackaging feed (dashboard and
  MCP), the watchlist and change totals, and the digest. In the digest,
  rising channels are limited to your channels and the niches they are in.
  `/api/health` tells a visitor who has not signed in only that the server is
  up. What stays shared is the public corpus itself: channels, videos and
  niches anyone collected, which is the point of one shared collection.

Security reviews: the plan-14 OAuth flow and each plan-15 sub-stage were
reviewed. The review of sign-in found a way to attach someone else's channel
through the OAuth callback, and it was fixed (see CHANGELOG, "Fixed").

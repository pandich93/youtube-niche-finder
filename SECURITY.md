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
  returned or logged.

## Multi-user mode (`NF_MULTI_USER=1`)

What the server does for you:

- **Accounts.**
  - Accounts exist only by invitation: `cli.py create-user`.
  - Passwords are scrypt hashes and must be at least 10 characters.
  - A wrong password and an unknown email get the same answer.
- **Sessions.**
  - A session is a random token in an `HttpOnly`, `SameSite=Strict` cookie, and
    the database keeps only its SHA-256.
  - Changing a password ends every session of that user.
  - Without a session, `/api` answers 401. Only sign-in, `/api/health` and the
    Google OAuth return are open.
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

1. **Put it behind HTTPS.** Use a reverse proxy (Caddy, nginx) with a real
   certificate. Set `NF_COOKIE_SECURE=1` and add your domain to
   `NF_ALLOWED_HOSTS`. Do not publish port 8080 or Postgres (5433) to the
   internet.
2. **Set `OWN_TOKENS_KEY`** (a Fernet key) and keep it only in `.env`. Losing
   it makes stored tokens unreadable. Leaking it exposes them.
3. **Back up Postgres.** It now holds accounts, sessions and encrypted
   secrets. Protect the backups like the server itself.
4. **Give each person their own account.** Admins are marked `--admin`, but
   no route grants extra powers over HTTP. Accounts are managed only from the
   command line on the server.
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
   7 days).
7. **Keep MCP over HTTP behind the TLS front door (`mcp-https`)** and set
   `MCP_PUBLIC_URL` to the address clients use. MCP over stdio is local only.

Known limits (by design, worth knowing before you invite anyone):

- **Everyone who signs in can change shared public data.** That covers pasting
  or re-pasting a video's transcript (one shared copy), curated tags,
  recomputing the niche map and naming niches by collecting them. The `--admin`
  flag is stored but not yet enforced anywhere. Invite only people you trust
  with that.
- **API tokens do not expire.** Revoke unused ones on the MCP screen. Changing a
  password revokes all of that user's tokens and sessions.
- **MCP over HTTP needs `NF_MULTI_USER=1` to ask for a token.** Without that
  flag it is as open as before, so keep it on the compose network behind
  `mcp-https`, which listens on 127.0.0.1 only.
- **Some global feeds can hint at what others watch.** Thumbnail fingerprints
  and swaps, and stats refreshes, run for every channel someone tracks, so the
  repackaging feed on the dashboard and the tracked-channel total reflect
  other users' watchlists. The daily digest lists only swaps on your own
  watchlist.

Security reviews: the plan-14 OAuth flow and each plan-15 sub-stage were
reviewed. The review of sign-in found a way to attach someone else's channel
through the OAuth callback, and it was fixed (see CHANGELOG, "Fixed").

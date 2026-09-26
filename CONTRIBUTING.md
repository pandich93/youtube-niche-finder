# Contributing to niche-finder

Thanks for considering a contribution. This project is a small, self-hosted
tool, so the bar is low and the process is simple.

## Ways to contribute

- **Bug reports** — open an issue with what you ran, what you expected, and
  what happened. `docker compose run --rm mcp python cli.py doctor` (or
  `make doctor`) is usually the first thing worth including — it checks the
  key, network, and database in one shot.
- **Bug fixes / small improvements** — open a PR directly.
- **New MCP tools / API endpoints / dashboard screens** — please open an
  issue first to agree on the shape before writing code, so you don't end up
  reworking it.
- **Documentation** — typos, unclear steps, outdated commands: PRs welcome,
  no need to ask first.

## Getting set up

```bash
git clone https://github.com/pandich93/youtube-niche-finder.git
cd youtube-niche-finder
cp .env.example .env          # YOUTUBE_API_KEY is optional for local dev
docker compose build
docker compose up -d postgres worker
make doctor
```

Or without Docker — see
[backend/README.md#running-without-docker](backend/README.md#running-without-docker).

## Running the tests

```bash
make up-db        # start just Postgres
make local-install
make local-test   # every backend/tests/test_*.py, no YouTube key or network needed
```

`make local-test` runs the whole `backend/tests/` directory in one pytest
process and exits non-zero if anything fails. CI runs exactly the same
command under `coverage` (see `.github/workflows/ci.yml`) plus a
`docker compose build` check on every push and pull request — both must be
green before a PR is merged.

Every file still sees the world it would see running alone
(`backend/tests/conftest.py`): Postgres-backed files get a fresh schema each
(`tests/schema_scope.py`), and the sqlite-double files (`test_alerts.py`,
`test_inspection.py`, `test_library.py`, `test_metadata_review.py`,
`test_top_tags.py`) keep `infrastructure.postgres` & co. in a private module
world (`tests/module_doubles.py`) instead of writing them into `sys.modules`
— `tests/test_module_doubles.py` fails if a file does the latter.

To run one file, from `backend/`:

```bash
./.venv/bin/python -m pytest -q tests/test_rss.py
./.venv/bin/python tests/test_rss.py     # same tests, built-in runner, no pytest needed
```

`make test` runs only `tests/test_smoke.py`, inside the Docker image
(which has no pytest).

A new `backend/tests/test_*.py` is picked up by CI and `make local-test`
automatically — no workflow edit needed. Postgres-backed tests import
`schema_scope` first (see `test_smoke.py`, `test_mcp_tools.py`); pure logic
and sqlite-double tests follow `test_metadata_domain.py` /
`test_metadata_review.py` (doubles via `ModuleDoubles`, never
`sys.modules[...] = ...`). External calls (YouTube, OpenRouter, Telegram)
must be mocked. If you give a file its own `__main__` runner, make it exit
non-zero when a test fails.

## Code layout

The backend follows a DDD-ish layering — see
[backend/README.md#structure](backend/README.md#structure) for the full
tree. In short:

- `domain/` — pure formulas and parsing, no I/O, no external dependencies.
- `infrastructure/` — adapters to Postgres, the YouTube API (plus channel
  RSS feeds), the local embeddings model, LLM providers (OpenRouter /
  Ollama) and notifications (Telegram / webhook).
- `application/` — use cases that orchestrate domain + infrastructure.
- `interfaces/` — thin entry points (MCP server, HTTP API, CLI, worker).

`server.py`, `api.py`, `cli.py`, and `worker.py` at the top of `backend/`
are shims that just import from `interfaces/` — don't add logic there.
The pre-DDD flat-module code that used to live in `backend/_legacy_flat_modules/`
has been removed; nothing imported it, so there is nothing to keep in sync
with `application/`, `domain/`, and `infrastructure/` anymore.

The frontend (`frontend/`) is plain ES modules with no build step and no
framework — keep it that way. New screens follow the existing pattern in
`screens/*.js` (one file per screen), `router.js` (hash routing), `shared.js`
(pieces several screens use) and `ui.js` (shared components/formatters).

## Style

- `make lint` runs [ruff](https://docs.astral.sh/ruff/) over `backend/`
  (config in `backend/pyproject.toml`, same pinned version as CI, where it
  must pass). There is no auto-formatter; match the style of the surrounding
  code (naming, docstrings, formatting).
- Keep entry-point shims thin — real logic belongs in `domain/`,
  `infrastructure/`, or `application/`.
- Prefer a test against the throwaway Postgres schema over a mock of our own
  code; mock only external services.

## Submitting a PR

1. Fork the repo and create a branch off `main`.
2. Make your change, keeping it focused — unrelated cleanup makes review
   harder.
3. Run `make lint` and `make local-test`, and confirm `docker compose build`
   still works if you touched `backend/requirements.txt` or the Dockerfile.
4. Open a PR describing what changed and why. Link the issue it addresses,
   if any.
5. CI must pass (ruff lint, all backend tests + Docker build) before merge.

By contributing, you agree your contribution is licensed under this
project's [MIT License](LICENSE).

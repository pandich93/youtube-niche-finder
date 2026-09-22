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

`make local-test` runs each `backend/tests/test_*.py` in its own pytest
process and ends with `all test files passed` (or `FAILED: <files>` and a
non-zero exit). CI runs exactly the same loop under `coverage` (see
`.github/workflows/ci.yml`) plus a `docker compose build` check on every
push and pull request — both must be green before a PR is merged.

Why one process per file: several tests (`test_alerts.py`,
`test_inspection.py`, `test_library.py`, `test_metadata_review.py`,
`test_top_tags.py`) replace `infrastructure.postgres` with an in-memory
sqlite double in `sys.modules`. In a single `pytest tests/` run that double
leaks into the files that need the real, throwaway-schema Postgres
(`tests/schema_scope.py`), so don't run the whole directory at once.

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
`test_metadata_review.py`. External calls (YouTube, OpenRouter, Telegram)
must be mocked. If you give a file its own `__main__` runner, make it exit
non-zero when a test fails.

## Code layout

The backend follows a DDD-ish layering — see
[backend/README.md#structure](backend/README.md#structure) for the full
tree. In short:

- `domain/` — pure formulas and parsing, no I/O, no external dependencies.
- `infrastructure/` — adapters to Postgres, the YouTube API, and the local
  embeddings model.
- `application/` — use cases that orchestrate domain + infrastructure.
- `interfaces/` — thin entry points (MCP server, HTTP API, CLI, worker).

`server.py`, `api.py`, `cli.py`, and `worker.py` at the top of `backend/`
are shims that just import from `interfaces/` — don't add logic there.
The pre-DDD flat-module code that used to live in `backend/_legacy_flat_modules/`
has been removed; nothing imported it, so there is nothing to keep in sync
with `application/`, `domain/`, and `infrastructure/` anymore.

The frontend (`frontend/`) is plain ES modules with no build step and no
framework — keep it that way. New screens follow the existing pattern in
`app.js` (hash routing) and `ui.js` (shared components/formatters).

## Style

- No linter is enforced yet; match the style of the surrounding code
  (naming, docstrings, formatting).
- Keep entry-point shims thin — real logic belongs in `domain/`,
  `infrastructure/`, or `application/`.
- Prefer a test against the throwaway Postgres schema over a mock of our own
  code; mock only external services.

## Submitting a PR

1. Fork the repo and create a branch off `main`.
2. Make your change, keeping it focused — unrelated cleanup makes review
   harder.
3. Run `make local-test` and confirm `docker compose build`
   still works if you touched `backend/requirements.txt` or the Dockerfile.
4. Open a PR describing what changed and why. Link the issue it addresses,
   if any.
5. CI must pass (all backend tests + Docker build) before merge.

By contributing, you agree your contribution is licensed under this
project's [MIT License](LICENSE).

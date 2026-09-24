## What changed and why

<!-- One or two sentences. Link an issue if there is one: Closes #123 -->

## Where it fits

- [ ] `domain/` — pure formulas/parsing
- [ ] `infrastructure/` — Postgres, YouTube API/RSS, embeddings, LLM, notify
- [ ] `application/` — use cases
- [ ] `interfaces/` — MCP server, HTTP API, CLI, worker
- [ ] `frontend/` — dashboard
- [ ] `extension/` — Chrome extension
- [ ] Docs only (README, CONTRIBUTING, CHANGELOG)

## Checklist

- [ ] `make test` (or `make local-test`) passes — ends with
      `all test files passed`
- [ ] Added/updated a smoke test in `backend/tests/test_smoke.py` for any new
      use case
- [ ] `docker compose build` still works, if `requirements.txt` or the
      Dockerfile changed
- [ ] Updated `CHANGELOG.md` under `[Unreleased]`, if this is a user-facing
      change
- [ ] Updated the relevant README (root / `backend/` / `frontend/` / `extension/`), if
      behavior or setup steps changed

## How to test this

<!-- Commands or steps a reviewer can run to see it working. -->

# Documentation

Where to read what. Start with the [project README](../README.md) for the
five-minute tour; everything below goes deeper into one part.

## Using niche-finder

| Document | Read it when you want to |
|---|---|
| [README](../README.md) | see what the project does, install it, connect Claude Desktop and the extension |
| [backend/README.md](../backend/README.md) | understand YouTube quota, run with or without Docker, use the CLI, look up any of the 97 MCP tools and the 7 ready-made scenarios, read the formulas |
| [frontend/README.md](../frontend/README.md) | find your way around the dashboard: every screen, where its data comes from, what costs quota |
| [extension/README.md](../extension/README.md) | install the Chrome extension and know what each panel, badge and popup section shows |
| [HTTP API reference](http-api.md) | call the backend from your own scripts: all routes, parameters, costs and their MCP twins |
| [`.env.example`](../.env.example) | configure the key, the worker schedule, alerts, the optional LLM, OAuth and multi-user mode -- every variable is commented in place |

## Running it for other people

| Document | Read it when you want to |
|---|---|
| [SECURITY.md](../SECURITY.md) | report a vulnerability, or check what protects single-user and multi-user mode before giving anyone an account |
| [PRIVACY.md](../PRIVACY.md) | know exactly what is stored, which hosts the app contacts, and how to delete everything |

## Working on the code

| Document | Read it when you want to |
|---|---|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | set up a dev environment, run the tests, follow the code layout and style, open a PR |
| [CHANGELOG.md](../CHANGELOG.md) | see what changed in each release |
| [backend/README.md#structure](../backend/README.md#structure) | learn the DDD layers: `domain` → `infrastructure` → `application` → `interfaces` |

## Keeping these docs true

- `docs/http-api.md` is generated from the code:
  `python3 scripts/gen_http_api_docs.py` rewrites it, `--check` fails when a
  route was added, removed or changed without regenerating it.
- The tool count (80), the scenario count (7) and the screenshots in
  [`assets/`](../assets) are written by hand -- update them in the same pull
  request that changes the code.
- Internal notes (decision history, market research, iteration plans) live in
  `docs/` and `.plans/` locally and are deliberately not part of the
  repository.

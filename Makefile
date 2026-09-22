# niche-finder -- common tasks
# Docker path is the default; `make local-*` targets run straight on the host.

COMPOSE ?= docker compose
IMAGE   ?= niche-finder:latest

# mcp[cli] (see backend/requirements.txt) needs Python 3.10+, but on macOS a
# bare `python3` often resolves to the Xcode Command Line Tools' Python 3.9 --
# that silently breaks `local-install` (pip aborts on the first requirement,
# so nothing after it gets installed either). Prefer a newer interpreter if one
# is on PATH; override explicitly with `make local-install PYTHON=python3.11`.
PYTHON  ?= $(shell command -v python3.13 || command -v python3.12 || \
             command -v python3.11 || command -v python3.10 || command -v python3)

.PHONY: help doctor build up up-db web open down logs worker mcp http shell test seed stats clean local-install local-test local-run dev cli

doctor: ## проверить ключ, сеть и базу (начните отсюда)
	$(COMPOSE) run --rm mcp python cli.py doctor

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

build:  ## build the image (PREFETCH_MODEL=1 bakes in the embedding model)
	$(COMPOSE) build

up:     ## поднять дашборд и фоновый сборщик
	$(COMPOSE) up -d web worker
	@echo "Дашборд: http://localhost:$${WEB_PORT:-8080}"

web:    ## только дашборд
	$(COMPOSE) up -d web
	@echo "Дашборд: http://localhost:$${WEB_PORT:-8080}"

open:   ## открыть дашборд в браузере
	open "http://localhost:$${WEB_PORT:-8080}"

down:   ## stop everything
	$(COMPOSE) down

logs:   ## логи сборщика и дашборда
	$(COMPOSE) logs -f worker web

mcp:    ## run the MCP server on stdio (manual poke)
	$(COMPOSE) run --rm mcp

http:   ## expose the MCP server over HTTP on :8765
	$(COMPOSE) --profile http up -d mcp-http

shell:  ## shell inside the image, with the models volume mounted
	docker run --rm -it --network niche-finder_default -v niche-finder-models:/models $(IMAGE) bash

# Схема make_test переиспользуется между запусками, поэтому чистим её ПЕРЕД
# прогоном: иначе данные прошлого раза ломают тесты, завязанные на свежий
# discovered_at (make test был зелёным только при первом запуске).
test:   ## run the smoke tests inside the image, against postgres (needs: make up-db)
	@$(COMPOSE) exec -T postgres sh -c \
	  'psql -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -c "DROP SCHEMA IF EXISTS make_test CASCADE"' >/dev/null
	docker run --rm --network niche-finder_default -e NICHE_DB_SCHEMA=make_test \
	  -e POSTGRES_HOST=postgres $(IMAGE) python tests/test_smoke.py

seed:   ## fill the database with synthetic demo data to try the tools (needs: make up-db)
	docker run --rm --network niche-finder_default -e POSTGRES_HOST=postgres \
	  $(IMAGE) python tests/seed_demo.py

stats:  ## what is in the database right now (needs: make up-db)
	docker run --rm --network niche-finder_default -e POSTGRES_HOST=postgres $(IMAGE) \
	  python -c "import json; from application import search as query; print(json.dumps(query.db_stats(), indent=2, default=str))"

up-db:  ## just the database, e.g. before make test/seed/stats
	$(COMPOSE) up -d postgres

cli:    ## произвольная команда CLI:  make cli ARGS="viral --period 24h"
	$(COMPOSE) run --rm mcp python cli.py $(ARGS)

clean:  ## delete the Postgres data volume (irreversible)
	docker volume rm niche-finder-postgres-data

local-install:  ## venv + deps, without Docker (needs Python 3.10+, auto-detected -- see PYTHON above)
	@ver=$$($(PYTHON) -c 'import sys; print("%d.%d" % sys.version_info[:2])'); \
	  major=$$(echo $$ver | cut -d. -f1); minor=$$(echo $$ver | cut -d. -f2); \
	  if [ "$$major" -lt 3 ] || { [ "$$major" -eq 3 ] && [ "$$minor" -lt 10 ]; }; then \
	    echo "ОШИБКА: $(PYTHON) -> Python $$ver, а пакету mcp нужен 3.10+."; \
	    echo "Поставьте современный Python (например: brew install python@3.12) и повторите,"; \
	    echo "либо укажите явно:  make local-install PYTHON=python3.12"; \
	    exit 1; \
	  fi; \
	  echo "используется $$($(PYTHON) --version) ($(PYTHON))"
	cd backend && $(PYTHON) -m venv .venv \
	  && ./.venv/bin/pip install --upgrade pip \
	  && ./.venv/bin/pip install -r requirements.txt pytest

# Тесты намеренно не читают .env (им не нужны ни ключ, ни сеть),
# поэтому NICHE_DATABASE_URL достаём из .env здесь -- иначе хостовый прогон
# уходит на localhost:5432 и падает с Connection refused.
# Как в CI: каждый tests/test_*.py отдельным процессом pytest (часть тестов
# подменяет infrastructure.postgres sqlite-двойником в sys.modules), в конце
# ненулевой код, если упал хоть один файл.
local-test:     ## run all backend tests on the host, one pytest process per file
	@cd backend && export NICHE_DATABASE_URL="$${NICHE_DATABASE_URL:-$$(grep -E '^NICHE_DATABASE_URL=' ../.env 2>/dev/null | sed -E 's/^[^=]+=//; s/^"//; s/"$$//' | tr -d '\r')}"; \
	  failed=""; \
	  for f in tests/test_*.py; do \
	    ./.venv/bin/python -m pytest -q -p no:cacheprovider "$$f" || failed="$$failed $$f"; \
	  done; \
	  if [ -n "$$failed" ]; then echo "FAILED:$$failed"; exit 1; fi; \
	  echo "all test files passed"

local-run:      ## run the MCP server on the host
	cd backend && ./.venv/bin/python server.py

# Требует в .env строку NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5433/niches
# (база из compose опубликована на 5433, а дефолт в коде -- 5432) и свободный
# :8080 -- при поднятом контейнере web сначала `docker compose stop web`,
# иначе хостовый uvicorn перехватит порт и проброс контейнера тихо отвалится.
dev:    ## run the HTTP dashboard on the host, no Docker (needs: make local-install + NICHE_DATABASE_URL in .env)
	cd backend && ./.venv/bin/python -m uvicorn api:app --reload --host 127.0.0.1 --port 8080

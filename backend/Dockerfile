# niche-finder -- YouTube niche & trend analytics MCP server
#
# Two ways to run the same image:
#   MCP over stdio (for Claude Desktop / Claude Code):
#       docker run --rm -i --network niche-finder_default --env-file .env \
#         -v niche-finder-models:/models niche-finder
#   Background collector (what makes the 24h windows work):
#       docker compose up -d worker
#
# Data lives in the `postgres` service (see ../docker-compose.yml) and the
# embedding model cache on the /models volume, so both survive image rebuilds
# and container restarts. Nothing is written into the image itself.

FROM python:3.15-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/models \
    HF_HOME=/models \
    XDG_CACHE_HOME=/models

WORKDIR /app

# ca-certificates is needed for HTTPS to googleapis.com; nothing else is.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Optional: bake the ~220MB multilingual embedding model into the image so the
# first semantic search does not have to download it.
#   docker build --build-arg PREFETCH_MODEL=1 -t niche-finder ./backend
ARG PREFETCH_MODEL=0
RUN if [ "$PREFETCH_MODEL" = "1" ]; then \
        python -c "import infrastructure.embeddings.fastembed_provider as embeddings; embeddings.embed('warm up the model cache')"; \
    fi

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /models \
    && chown -R app:app /models /app
USER app

VOLUME ["/models"]

# Liveness for the long-running worker: Postgres must be reachable and openable.
HEALTHCHECK --interval=5m --timeout=15s --start-period=30s --retries=3 \
    CMD python -c "import infrastructure.postgres as db; db.init_db()" || exit 1

# Default is the MCP server on stdio. Override with `python worker.py` for the
# collector, or set MCP_TRANSPORT=streamable-http to expose it over HTTP.
CMD ["python", "server.py"]

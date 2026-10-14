# One image serves the API and the built UI (ADR 003).

# --- UI build ------------------------------------------------------------
FROM node:22.22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- runtime -------------------------------------------------------------
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.8.11 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app

# Runtime dependencies only: no dev tools, no ingestion parser.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-default-groups --no-install-project

# Includes recipe_qa/BUILD_SHA when CI wrote one: the commit /healthz reports.
COPY recipe_qa/ recipe_qa/
COPY data/recipes.jsonl data/manifest.json data/
COPY --from=web /web/dist web/dist

RUN useradd --system --uid 10001 --no-create-home app
USER app

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn recipe_qa.api:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]

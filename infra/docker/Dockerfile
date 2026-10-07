# syntax=docker/dockerfile:1
# One image for the RAGForge Python services: the API now, the worker from Phase 2.
# Build from the repo root: docker build -f infra/docker/Dockerfile .

# ---------------------------------------------------------------- build ----
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 1) Third-party packages only. Docker caches this layer until uv.lock changes,
#    so code changes do not reinstall everything.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# 2) Our own code, installed as a normal (non-editable) package.
#    --reinstall-package: uv caches the wheel it builds from our code, and by default it
#    only rebuilds it when pyproject.toml changes. Without this flag, code changes would
#    not reach the image.
COPY pyproject.toml uv.lock README.md ./
COPY api ./api
COPY shared ./shared
COPY worker ./worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --reinstall-package ragforge

# -------------------------------------------------------------- runtime ----
FROM python:3.12-slim AS runtime

# Never run as root inside the container.
RUN useradd --create-home --uid 10001 ragforge

WORKDIR /app
COPY --from=builder --chown=ragforge:ragforge /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER ragforge
EXPOSE 8000

# --no-access-log: the app writes its own access log line (JSON, with the request ID).
CMD ["uvicorn", "api.main:create_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

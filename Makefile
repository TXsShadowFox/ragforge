# RAGForge developer commands. Run `make` to see the list.
#
# Recipes stay simple so they work in PowerShell/cmd on Windows and in bash on
# Linux/CI: no cp/rm, no quotes or brackets in echo.
# Tools run as `python -m <tool>` because Windows Smart App Control can block
# the small .exe launchers that uv creates (error 4551).

COMPOSE = docker compose
PY = uv run python -m
DATA_SERVICES = postgres redis rabbitmq qdrant rustfs

.DEFAULT_GOAL := help
.PHONY: help setup up down dev worker migrate logs ps test test-unit lint fmt

help:
	@echo Commands:
	@echo   make setup     - install Python packages, create .env, install git hooks
	@echo   make up        - start everything in Docker: API, worker, data services, monitoring
	@echo   make down      - stop everything, data is kept
	@echo   make dev       - data services in Docker, API on your machine with auto-reload
	@echo   make worker    - run the ingestion worker on your machine, next to make dev
	@echo   make migrate   - bring the database in .env up to the newest version
	@echo   make logs      - follow the logs of all containers
	@echo   make ps        - show container status
	@echo   make test      - run all tests, integration tests need Docker
	@echo   make test-unit - run only the fast unit tests, no Docker needed
	@echo   make lint      - check style and types: ruff + mypy
	@echo   make fmt       - auto-format and auto-fix the code

# Create .env from the template, only if it does not exist yet.
.env:
	uv run python -c "import shutil; shutil.copyfile('.env.example', '.env')"
	@echo Created .env from .env.example - change the passwords in it.

setup: .env
	uv sync
	$(PY) pre_commit install

up: .env
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

dev: .env
	$(COMPOSE) up -d --wait $(DATA_SERVICES)
	$(PY) shared.init
	$(PY) uvicorn api.main:create_app_from_env --factory --reload --reload-dir api --reload-dir shared --port 8000 --no-access-log

worker: .env
	$(PY) worker

migrate: .env
	$(PY) shared.db.migrate

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test:
	$(PY) pytest

test-unit:
	$(PY) pytest -m "not integration"

lint:
	$(PY) ruff check .
	$(PY) ruff format --check .
	$(PY) mypy

fmt:
	$(PY) ruff format .
	$(PY) ruff check --fix .

# RAGForge developer commands. Run `make` to see the list.
#
# Recipes stay simple so they work in PowerShell/cmd on Windows and in bash on
# Linux/CI: no cp/rm, no quotes or brackets in echo.
# Tools run as `python -m <tool>` because Windows Smart App Control can block
# the small .exe launchers that uv creates (error 4551).

COMPOSE = docker compose
PY = uv run python -m
NPM = npm --prefix frontend
DATA_SERVICES = postgres redis rabbitmq qdrant rustfs

.DEFAULT_GOAL := help
.PHONY: help setup up down dev worker frontend widget-demo migrate logs ps test test-unit e2e eval lint fmt

help:
	@echo Commands:
	@echo   make setup     - install Python and dashboard packages, create .env, install git hooks
	@echo   make up        - start everything in Docker: API, worker, dashboard, data, monitoring
	@echo   make down      - stop everything, data is kept
	@echo   make dev       - data services in Docker, API on your machine with auto-reload
	@echo   make worker    - run the ingestion worker on your machine, next to make dev
	@echo   make frontend  - run the dashboard on your machine at port 3000, next to make dev
	@echo   make widget-demo - serve widget/demo.html at http://localhost:5500, a test website
	@echo   make migrate   - bring the database in .env up to the newest version
	@echo   make logs      - follow the logs of all containers
	@echo   make ps        - show container status
	@echo   make test      - run all tests, integration tests need Docker
	@echo   make test-unit - run only the fast unit tests, no Docker needed
	@echo   make e2e       - browser test of the whole flow, needs make up and the Groq key
	@echo   make eval      - measure search and answer quality into eval/RESULTS.md, Docker and Groq
	@echo   make lint      - check style and types: ruff, mypy, eslint, prettier, tsc
	@echo   make fmt       - auto-format and auto-fix the code

# Create .env from the template, only if it does not exist yet.
.env:
	uv run python -c "import shutil; shutil.copyfile('.env.example', '.env')"
	@echo Created .env from .env.example - change the passwords in it.

setup: .env
	uv sync
	$(NPM) ci
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

frontend:
	$(NPM) run dev

widget-demo:
	$(PY) http.server 5500 --directory widget

migrate: .env
	$(PY) shared.db.migrate

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test:
	$(PY) pytest
	$(NPM) test

test-unit:
	$(PY) pytest -m "not integration"
	$(NPM) test

e2e:
	$(NPM) run e2e

eval:
	$(PY) eval.run

lint:
	$(PY) ruff check .
	$(PY) ruff format --check .
	$(PY) mypy
	$(NPM) run lint
	$(NPM) run format:check
	$(NPM) run typecheck

fmt:
	$(PY) ruff format .
	$(PY) ruff check --fix .
	$(NPM) run format

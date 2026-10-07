# CLAUDE.md: RAGForge

Guide for Claude Code (and humans) working in this repo. **Update it after every phase.**

## Project summary

RAGForge is a multi-tenant RAG-as-a-Service platform. Companies (tenants) upload documents
and get a chat API with citations (REST + SSE streaming), an embeddable chat widget, and a
dashboard (documents, usage, cost, latency, answer quality). It is a resume project: the
focus is system design, backend engineering and RAG quality, backed by tests and real numbers.

- Full spec: [PROJECT_SPEC.md](PROJECT_SPEC.md)
- Progress checklist: [PROGRESS.md](PROGRESS.md)

## Working rules

1. One phase at a time. Before a phase, show a short plan (files + key decisions) and wait for the user's "ok".
2. Explain in short, simple English.
3. Every phase ships with tests. A phase is done only when the tests pass.
4. After each phase: update this file and PROGRESS.md, then make a git commit.
5. Ask before adding a big dependency, changing the architecture, or using a paid API.
6. Free/local options by default. Paid providers are optional, through config.
7. Ask before pushing to GitHub.

## How to run

Needs Docker Desktop, [uv](https://docs.astral.sh/uv/) and GNU make
(Windows: `winget install -e --id ezwinports.make`).

| Command | What it does |
|---|---|
| `make setup` | install packages, create `.env` from `.env.example`, install git hooks |
| `make up` | start everything in Docker (API + data services + monitoring), wait until healthy |
| `make dev` | start only the data services, run the API on your machine with auto-reload |
| `make test` / `make test-unit` | all tests / only the unit tests (no Docker needed) |
| `make lint` / `make fmt` | ruff + mypy / auto-format and auto-fix |
| `make logs` / `make ps` / `make down` | follow logs / container status / stop (data is kept) |

| Local service | URL (logins are in `.env`) |
|---|---|
| API (interactive docs at `/docs`) | http://localhost:8000 |
| RabbitMQ management UI | http://localhost:15672 |
| Qdrant web UI | http://localhost:6333/dashboard |
| RustFS console | http://localhost:9001 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 |

## Folder structure

```
api/                FastAPI app: main.py (factory + lifespan), routes/, readiness.py, dependencies.py
shared/             used by the API and the worker: config.py (settings), clients/ (one module per service)
worker/             ingestion worker (Phase 2)
infra/              Dockerfile, Prometheus config, Grafana provisioning
tests/unit/         fast tests, no Docker
tests/integration/  real services via testcontainers (marked `integration`)
frontend/ widget/ loadtests/ eval/   later phases (each has a README)
```

## Coding rules

- Python 3.12. Type hints everywhere; `mypy --strict` must pass.
- ruff for lint + format (line length 100). Git hooks run ruff, mypy and file checks on every commit.
- Small functions, clear names, a docstring on every module and on non-obvious functions.
- Settings only through `shared/config.py` (pydantic-settings). Secrets have **no defaults**.
  Never hard-code hosts or secrets. A new setting must also go into `.env.example` (a test checks this).
- Never block the event loop: run sync libraries (like boto3) with `asyncio.to_thread`.
- Long-lived clients live in `shared.clients.Clients`, created once per process at startup.
- Routes get shared objects through FastAPI dependencies (`api/dependencies.py`), so tests can override them.
- Unit tests must not need Docker. Tests that need real services go in `tests/integration/`.
- From Phase 1: every table has `tenant_id`, and every query filters by tenant.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | RustFS instead of MinIO for object storage | MinIO's free edition was archived in Apr 2026. RustFS is Apache-2.0, S3-compatible, stable (1.0) since Sept 2026. The code only uses the S3 API (boto3), so AWS S3 or Cloudflare R2 work by changing settings. |
| D2 | One `pyproject.toml` with packages `api`, `worker`, `shared`; uv manages packages | One lock file; the API and the worker share code; the same setup in Docker and CI. |
| D3 | One Docker image for the API and the worker | Same dependencies, less build time and disk. Each service gets its own start command. |
| D4 | `/health` = liveness, calls nothing. `/ready` = probes all 5 services in parallel (2 s timeout each), 503 if one fails | The Docker healthcheck uses `/health`, so a short database problem does not restart the API. Revisit in Phase 2: with an outbox, RabbitMQ may not need to block readiness. |
| D5 | `make dev` runs the API on the host with auto-reload | Watching files inside Docker on Windows is slow, and this saves RAM. |
| D6 | `.gitattributes` forces LF line endings | CRLF breaks scripts and configs inside Linux containers. |
| D7 | Grafana on host port 3001 | Port 3000 stays free for the Next.js dashboard (Phase 5). |
| D8 | Postgres 18 | Built-in `uuidv7()` gives time-ordered IDs (Phase 1). |
| D9 | Light choices: the dev laptop has ~8 GB RAM | Docker gets ~4 GB. `make dev` skips monitoring; prefer ONNX models over PyTorch (Phase 2). |
| D10 | Image versions live only in `docker-compose.yml`; integration tests read them from there | Tests and dev always use the same versions. |
| D11 | Tools run as `python -m <tool>` (Makefile, git hooks, CI) | Windows Smart App Control blocks the small `.exe` launchers that uv and pre-commit create (error 4551). |

## Gotchas

- `uv run pytest` can fail on Windows with "os error 4551" (Smart App Control). Use `uv run python -m pytest`.
- Makefile recipes must work in both cmd and bash: no `cp`/`rm`, no quotes, brackets or `&` in `echo`.
- boto3 >= 1.36 sends extra checksums by default; we set `when_required` so S3-compatible servers accept requests.
- Postgres 18 image: mount the volume at `/var/lib/postgresql` (not `.../data`).
- The Qdrant image has no curl, so its healthcheck uses bash `/dev/tcp`.
- The uv cache (C:) and the project (X:) are on different drives, so uv warns "Failed to hardlink files". It is harmless; set `UV_LINK_MODE=copy` to hide it.

## Notes for later phases (from the spec review)

- Phase 1: `messages` and `feedback` also get `tenant_id` (spec rule: every table). `api_keys` gets `kind` (secret/public) and `allowed_origins` for the widget.
- Phase 2: upload = DB row + queue message, so use the **outbox pattern** (a failed publish must never leave a document stuck in `uploaded`). Retries with TTL retry queues, then a dead-letter queue. Delete touches 3 stores, so it is an idempotent worker job. ONNX embeddings (fastembed) to save RAM: ask first (new dependency).
- Phase 3: LLM choice (Ollama on the RTX 3050, or the Groq free tier): ask first.
- Phase 4: tenants get a `docs_version` number for the exact-cache key; bump it on every document change. redis-py retries 3 times by default; cache and rate-limit calls may need fail-fast settings.
- Phase 8: every service address is already a setting, so free managed services can be plugged in.

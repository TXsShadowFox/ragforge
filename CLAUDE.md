# CLAUDE.md: RAGForge

Guide for Claude Code (and humans) working in this repo. **Update it after every phase.**

## Project summary

RAGForge is a multi-tenant RAG-as-a-Service platform. Companies (tenants) upload documents
and get a chat API with citations (REST + SSE streaming), an embeddable chat widget, and a
dashboard (documents, usage, cost, latency, answer quality). It is a resume project: the
focus is system design, backend engineering and RAG quality, backed by tests and real numbers.

- Full spec: [PROJECT_SPEC.md](PROJECT_SPEC.md)
- Progress checklist: [PROGRESS.md](PROGRESS.md)
- Repo (public): https://github.com/TXsShadowFox/ragforge. CI runs on every push to `main` and on PRs.

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
(Windows: `winget install -e --id ezwinports.make`). On Windows, Docker Desktop needs WSL 2:
from an **admin** PowerShell run `winget install -e --id Microsoft.WSL`, then
`winget install -e --id Docker.DockerDesktop`. Open a new terminal after installing, so it sees the new PATH.

| Command | What it does |
|---|---|
| `make setup` | install packages, create `.env` from `.env.example`, install git hooks |
| `make up` | start everything in Docker (API + worker + data services + monitoring), wait until healthy; a one-time `init` container prepares the stores first |
| `make dev` | start only the data services, prepare the stores (`python -m shared.init`), run the API on your machine with auto-reload |
| `make worker` | run the ingestion worker on your machine (next to `make dev`, in a second terminal) |
| `make migrate` | update the database in `.env` to the newest migration |
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
api/                FastAPI app: main.py (factory + lifespan), dependencies.py, readiness.py
  auth/             passwords (argon2), tokens (JWT), keys (API keys), principal (who is calling)
  routes/           one module per area: system, auth, api_keys, me, documents
  errors.py         the one JSON error format (ApiError, unauthorized, forbidden, not_found)
  middleware.py     request ID, one JSON access log line per request, safe 500s
shared/             used by the API and the worker
  config.py         settings (pydantic-settings)
  logging.py        JSON logs + log context (request ID, tenant ID)
  clients/          one module per service (Postgres engine + ORM sessions, Redis, Qdrant, storage)
  db/               models.py (tables), migrate.py (`python -m shared.db.migrate`), migrations/
  init.py           `python -m shared.init`: migrations, bucket, Qdrant collection, queues
  file_types.py     accepted files, checked by their first bytes
  embeddings.py     Embedder interface + FastEmbedEmbedder (bge-small, ONNX)
  vector_store.py   Qdrant: one collection, tenant_id on every point
  jobs.py           RabbitMQ: queue names, job messages, retry delays
  outbox.py         add_job(): save a job in the same transaction as the change
worker/             `python -m worker`: runner (main loop), relay (outbox -> RabbitMQ),
                    consumer (retries, dead-letter queue), pipeline (ingest + delete jobs),
                    parsing (PDF/DOCX/HTML/MD/TXT), cleaning, chunking
infra/              Dockerfile, Prometheus config, Grafana provisioning
tests/unit/         fast tests, no Docker
tests/integration/  real services via testcontainers (marked `integration`); helpers.py
tests/fakes.py      FakeEmbedder (fast vectors, a "token" is a word), FailingEmbedder
tests/documents.py  make_pdf() and make_docx() for test files
frontend/ widget/ loadtests/ eval/   later phases (each has a README)
alembic.ini         only for the `alembic` command line (creating new migrations)
```

## Database migrations

1. Change the models in `shared/db/models.py`.
2. Start the dev Postgres (`make dev`, or `docker compose up -d --wait postgres`), then (next number):
   `uv run python -m alembic revision --autogenerate --rev-id 0003 -m "add messages"`
3. Read the new file in `shared/db/migrations/versions/`. Autogenerate writes each enum CHECK
   constraint twice: keep one `sa.CheckConstraint(..., name=op.f("ck_<table>_<enum name>"))`
   and make the column `sa.String(length=16)`.
4. `make migrate`, then `make test`. Tests check that the migrations match the models and
   that they run down and up again.

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
- Every table has `tenant_id`, and every query filters by tenant. Only two queries skip it on
  purpose, because they find the tenant: login (by email) and the API key lookup (by hash).
- Protect routes with the dependencies in `api/auth/principal.py`: `PrivateAccess` (a user or
  a secret key) or `AdminUser` (a logged-in owner/admin). Public keys are refused everywhere for now.
- Errors: raise `ApiError` or `unauthorized()` / `forbidden()` / `not_found()` from `api/errors.py`.
- Logs: `logging.getLogger(__name__)`, extra fields with `extra={...}`. Never log passwords, keys
  or tokens. CPU-heavy work (like argon2) runs in `asyncio.to_thread`.
- `create_app(settings)` has no side effects (tests use it); uvicorn runs `create_app_from_env`.
- Background work goes through the outbox: `add_job(session, Job(...))` in the same transaction
  as the change, never a direct publish to RabbitMQ from the API.
- Jobs must be safe to run twice (computed IDs, "insert or replace", status checks).
  A file that can never work raises `BadDocumentError` (no retries); anything else is retried.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | RustFS instead of MinIO for object storage | MinIO's free edition was archived in Apr 2026. RustFS is Apache-2.0, S3-compatible, stable (1.0) since Sept 2026. The code only uses the S3 API (boto3), so AWS S3 or Cloudflare R2 work by changing settings. |
| D2 | One `pyproject.toml` with packages `api`, `worker`, `shared`; uv manages packages | One lock file; the API and the worker share code; the same setup in Docker and CI. |
| D3 | One Docker image for the API and the worker | Same dependencies, less build time and disk. Each service gets its own start command. |
| D4 | `/health` = liveness, calls nothing. `/ready` = probes Postgres, Redis, Qdrant and storage in parallel (2 s timeout each), 503 if one fails. Not RabbitMQ (since Phase 2) | The Docker healthcheck uses `/health`, so a short database problem does not restart the API. The API never talks to RabbitMQ (outbox, D22), so uploads keep working while it is down. |
| D5 | `make dev` runs the API on the host with auto-reload | Watching files inside Docker on Windows is slow, and this saves RAM. |
| D6 | `.gitattributes` forces LF line endings | CRLF breaks scripts and configs inside Linux containers. |
| D7 | Grafana on host port 3001 | Port 3000 stays free for the Next.js dashboard (Phase 5). |
| D8 | Postgres 18 | Built-in `uuidv7()` gives time-ordered IDs (Phase 1). |
| D9 | Light choices: the dev laptop has ~8 GB RAM | Docker gets ~4 GB. `make dev` skips monitoring; prefer ONNX models over PyTorch (Phase 2). |
| D10 | Image versions live only in `docker-compose.yml`; integration tests read them from there | Tests and dev always use the same versions. |
| D11 | Tools run as `python -m <tool>` (Makefile, git hooks, CI) | Windows Smart App Control blocks the small `.exe` launchers that uv and pre-commit create (error 4551). |
| D12 | One header for everyone: `Authorization: Bearer <login token or API key>`; API keys start with `rf_` | One way to log in for all clients, and the `/docs` "Authorize" button works for both. |
| D13 | Passwords: argon2id (slow). API keys: SHA-256 (fast) | Keys have 40 random characters (~238 bits), so a fast hash is safe and one indexed lookup finds the key. People choose weak passwords, so passwords need a slow hash. |
| D14 | Only a logged-in owner/admin manages API keys. Public keys (`rf_pub_`, with allowed origins) are refused on every endpoint until the widget (Phase 5) | A leaked key cannot create more keys. A public key in a web page cannot reach private data. |
| D15 | Emails are unique across all tenants and stored in lower case | Login needs only email + password. Someone in two companies needs two emails (fine for now). |
| D16 | Enums are text + a CHECK constraint, not Postgres ENUM types | Adding a value later is a simple migration. |
| D17 | Migrations: Alembic. A one-time `init` container (Phase 1: `migrate`) runs them before the API and the worker start. It also creates the bucket, the Qdrant collection and the queues | One place prepares the stores, so several API or worker copies never race. |
| D18 | JSON logs with Python's `logging` and a log context; a plain ASGI middleware writes the access line | No new package. Starlette's `BaseHTTPMiddleware` would not see the tenant that the endpoint adds to the context. |
| D19 | Errors: `{error: {code, message, request_id}}`; validation errors add `details` (field names, never values) | One format for clients, and passwords are never sent back in an error. |
| D20 | Login tokens: HS256, 60 minutes, no refresh token yet. Each request checks that the user still exists | A deleted user's token stops working at once. |
| D21 | `last_used_at` of a key is saved at most once a minute | No database write on every API request. |
| D22 | Outbox pattern: the API saves each job in the `outbox` table, in the same transaction as the document. The worker's relay sends jobs to RabbitMQ (`SKIP LOCKED`, publisher confirms) and then deletes them | A job is never lost when RabbitMQ is down, and never sent for a change that was rolled back. |
| D23 | Retries: TTL "retry queues" named by their delay (10 s, 1 min, 5 min), then `document-jobs.dead`; the document becomes `failed`. Broken files fail at once | Backoff without a RabbitMQ plugin. Retrying cannot fix a broken file. |
| D24 | Jobs are safe to run twice: chunk ID = uuid5(document ID, chunk number); Qdrant upsert + delete leftovers; Postgres chunks replaced in one locked transaction; status checks before each step | RabbitMQ and the outbox deliver "at least once", so duplicates must change nothing. |
| D25 | Embeddings: fastembed + `BAAI/bge-small-en-v1.5` (ONNX, 384 numbers, reads 512 tokens). Chunks are counted with the model's own tokenizer, without its 2 special tokens | No PyTorch (saves ~2 GB). A 500-token chunk always fits, so no text is cut off; the worker checks this at startup. |
| D26 | `EMBEDDING_BATCH_SIZE=8` | Measured on a 50-page PDF (60 chunks) in Docker: batch 32 = 941 MB RAM, 16.5 s; batch 8 = 594 MB, 11.3 s. On a CPU, big batches only grow memory. |
| D27 | Chunking: cut into sentences/lines, pack up to 500 tokens, repeat whole sentences (up to 50 tokens) at the start of the next chunk; a chunk keeps the PDF page where it starts | Chunks end at sentence ends, overlap really happens, and citations can name a page. |
| D28 | Uploads: file type checked by its first bytes; stored as `tenants/<tenant>/documents/<id>`; same SHA-256 for the same tenant = the same document (unique index, 200 + `duplicate: true`) | A renamed file cannot fool us, user file names never become storage paths, and re-uploads create nothing new. |
| D29 | `GET /v1/documents` uses cursor paging on the (time-ordered) uuidv7 ID | Fast on any page, and new uploads do not shift the pages. |

## Gotchas

- `uv run pytest` can fail on Windows with "os error 4551" (Smart App Control). Use `uv run python -m pytest`.
- Makefile recipes must work in both cmd and bash: no `cp`/`rm`, no quotes, brackets or `&` in `echo`.
- boto3 >= 1.36 sends extra checksums by default; we set `when_required` so S3-compatible servers accept requests.
- Postgres 18 image: mount the volume at `/var/lib/postgresql` (not `.../data`).
- The Qdrant image has no curl, so its healthcheck uses bash `/dev/tcp`.
- RabbitMQ's healthcheck is a TCP check of port 5672, not `rabbitmq-diagnostics`. That tool runs as root,
  and on a slow first start it can create the Erlang cookie file before RabbitMQ does. RabbitMQ then
  stops with `eacces` on `.erlang.cookie`.
- `astral-sh/setup-uv` has no short major tags after v7, so `@v10` fails in CI. Use the full version (`@v10.2.0`).
- winget cannot ask for admin rights for MSIX packages such as `Microsoft.WSL` (error `0x80073d28`).
  Run that install from an admin PowerShell.
- RAM: `make up` uses about 1.8 GB inside Docker (Grafana alone about 0.9 GB). On the 8 GB laptop,
  use `make dev` for daily work and `make down` when you are done.
- Docker builds keep uv's cache, and uv rebuilds our own package only when `pyproject.toml`
  changes. So the Dockerfile uses `--reinstall-package ragforge`; without it, code changes
  do not reach the image.
- uvicorn writes access lines whenever the `uvicorn.access` logger has a handler, even with
  `--no-access-log`. `configure_logging()` turns it off; the API writes its own access line.
- A new setting without a default must also go into your own `.env`, not only `.env.example`.
- `qdrant_client` imports fastembed (and Hugging Face's library) when it is imported, so
  Hugging Face settings like `HF_HUB_DISABLE_PROGRESS_BARS` must be set before the process starts
  (the Dockerfile does). Setting them in code is too late.
- fastembed's `token_count()` adds the model's 2 special tokens; `count_tokens()` does not.
- Docker Compose `<<: *anchor` merges are shallow: a service's own `environment:` or `volumes:`
  would replace the anchor's whole list. Keep them in the anchor only.
- The upload size limit is checked while we read the file, but Starlette has already received
  the whole request by then. A real request-size limit comes in Phase 7.
- The FastAPI file upload parameter is named `file` (multipart): `curl -F "file=@rules.pdf"`.
- The uv cache (C:) and the project (X:) are on different drives, so uv warns "Failed to hardlink files". It is harmless; set `UV_LINK_MODE=copy` to hide it.

## Notes for later phases (from the spec review)

- Phase 3: LLM choice (Ollama on the RTX 3050, or the Groq free tier): ask first. `messages` and `feedback` also get `tenant_id` (spec rule: every table). Search must use only chunks of `ready` documents of the caller's tenant (Qdrant filter on `tenant_id`, then check the document status in Postgres). Queries are embedded with the same model (bge may want a query prefix: check fastembed's `query_embed`).
- Phase 6: the spec compares chunk sizes 300 / 500 / 1000, but bge-small reads at most 512 tokens. The 1000-token test needs a model with a longer input (or compare 300 / 500 only).
- Phase 7: a request-size limit before the upload is read (see Gotchas). Worker metrics: queue depth, job time, failures.
- Phase 4: tenants get a `docs_version` number for the exact-cache key; bump it on every document change. redis-py retries 3 times by default; cache and rate-limit calls may need fail-fast settings. Also rate-limit `/v1/auth/login` (password guessing).
- Phase 5: the widget's chat endpoint accepts public keys and checks the `Origin` header against `allowed_origins` (plus CORS). The dashboard may need a way to add members (today only signup creates the owner).
- Phase 8: every service address is already a setting, so free managed services can be plugged in.

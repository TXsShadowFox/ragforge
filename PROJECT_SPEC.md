# Project Spec: RAGForge — RAG-as-a-Service Platform

> How to use: put this file in an empty project folder as `PROJECT_SPEC.md`, open Claude Code there, and say:
> "Read PROJECT_SPEC.md. Follow the working rules. Start with Phase 0."

---

## 1. Your role and working rules (read first)

You are a senior backend engineer helping me (a final-year CS student) build a production-style project for my resume. I am targeting SDE and AI/ML Engineer roles at companies like Google, Amazon, Zomato, Swiggy and AI startups. The goal is to show strong **system design + backend engineering + RAG** skills, not only "chat with PDF".

Working rules:
1. **Build one phase at a time.** Before each phase, show me a short plan (files you will create, key decisions). Wait for my "ok" before writing code.
2. **Explain in simple English.** Keep explanations short and clear.
3. **Write tests with every phase.** Do not mark a phase done until tests pass.
4. **Keep a `CLAUDE.md`** in the repo root with: project summary, how to run, folder structure, coding rules, and decisions made. Update it after each phase.
5. **Keep a `PROGRESS.md`** with a checklist of phases and what is done.
6. **Make a git commit after each phase** with a clear message.
7. **Ask me before** adding a new big dependency, changing the architecture, or using a paid API.
8. Prefer **free / local options** by default (local embedding model, Ollama or free-tier LLM). Make paid providers optional through config.
9. Code quality: type hints, small functions, clear names, no hard-coded secrets (use `.env` + pydantic-settings), structured logging.

---

## 2. What the product does

Companies (tenants) sign up, upload their documents, and get:
- a **REST + streaming chat API** that answers questions from their documents, with citations
- an **embeddable chat widget** (one `<script>` tag) for their website
- a **dashboard** showing documents, usage, cost, latency, and answer quality

Example: a college uploads its rules PDFs; students ask "What is the attendance rule?" on the college website and get an answer with the source page.

---

## 3. Tech stack

| Part | Choice |
|---|---|
| API | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 |
| Main database | PostgreSQL (SQLAlchemy 2.0 async + Alembic migrations) |
| Keyword search | PostgreSQL full-text search (tsvector + GIN index) |
| Vector database | Qdrant |
| Cache + rate limit | Redis |
| Job queue | RabbitMQ (with retries + dead-letter queue) |
| Workers | Separate Python worker service consuming RabbitMQ |
| File storage | MinIO (S3-compatible) |
| Embeddings | Pluggable: default `BAAI/bge-small-en-v1.5` (local), optional OpenAI |
| Reranker | Pluggable: default `BAAI/bge-reranker-base` (local), optional Cohere |
| LLM | Pluggable: Ollama / Groq / OpenAI / Anthropic via one interface |
| Frontend | Next.js + TypeScript + Tailwind (dashboard) + small vanilla JS widget |
| Monitoring | Prometheus + Grafana, OpenTelemetry tracing |
| Testing | pytest, pytest-asyncio, testcontainers; k6 for load tests |
| DevOps | Docker, docker-compose, GitHub Actions CI |

---

## 4. Architecture

```
Client / Widget / Dashboard
        |
   [API Gateway layer in FastAPI]  -- auth (API key / JWT), rate limit (Redis), request ID
        |
  -----------------------------------------------
  |               |                |            |
Upload API     Chat API        Admin API    Metrics (/metrics)
  |               |
MinIO + Postgres  Retrieval pipeline:
  |                 cache check (Redis exact + semantic)
RabbitMQ  ---->    hybrid search (Postgres FTS + Qdrant dense)
  |                 -> RRF fusion -> rerank -> prompt build
Ingestion Workers   -> LLM (streaming via SSE) -> citations
  parse -> clean -> chunk -> embed -> store (Qdrant + Postgres)
```

Key design decisions (write these into `CLAUDE.md` and README):
- **Multi-tenancy:** every table has `tenant_id`. In Qdrant, use ONE collection with a `tenant_id` payload field + payload index (scales better than one collection per tenant). Every query MUST filter by tenant. Add a test that proves tenant A can never see tenant B's data.
- **Async ingestion:** upload returns `202 Accepted` + `document_id` right away. Workers do the heavy work. Document status: `uploaded -> processing -> ready | failed`.
- **Idempotency:** re-uploading the same file (same SHA-256 hash) for the same tenant does not create duplicates.
- **Retries:** failed jobs retry 3 times with backoff, then go to a dead-letter queue and the document is marked `failed` with the error.

---

## 5. Data model (starting point)

- `tenants` (id, name, plan, created_at)
- `users` (id, tenant_id, email, password_hash, role: owner/admin/member)
- `api_keys` (id, tenant_id, key_prefix, key_hash, name, created_at, last_used_at, revoked)
- `documents` (id, tenant_id, filename, mime_type, size_bytes, sha256, storage_path, status, error, chunk_count, created_at)
- `chunks` (id, tenant_id, document_id, chunk_index, text, page_number, token_count, tsv tsvector)
- `chat_sessions` (id, tenant_id, created_at)
- `messages` (id, session_id, role, content, citations JSON, latency_ms, tokens_in, tokens_out, cost_usd, cache_hit, created_at)
- `feedback` (id, message_id, rating: up/down, comment)
- `usage_daily` (tenant_id, date, queries, tokens, cost_usd)

---

## 6. API endpoints (v1)

Auth and tenants:
- `POST /v1/auth/signup` — create tenant + owner user
- `POST /v1/auth/login` — returns JWT (for dashboard)
- `POST /v1/api-keys` / `GET /v1/api-keys` / `DELETE /v1/api-keys/{id}` — show the full key only once, store only the hash

Documents:
- `POST /v1/documents` — upload (PDF, DOCX, TXT, MD, HTML), max size from config
- `GET /v1/documents` — list with status, pagination
- `GET /v1/documents/{id}` — details
- `DELETE /v1/documents/{id}` — delete from Postgres, Qdrant and MinIO

Chat:
- `POST /v1/chat` — body: `{question, session_id?, top_k?, stream?}`. If `stream=true`, return Server-Sent Events (tokens, then a final event with citations).
- `POST /v1/messages/{id}/feedback`

Admin / analytics:
- `GET /v1/analytics/usage?from=&to=` — queries, tokens, cost, p50/p95 latency, cache hit rate
- `GET /v1/analytics/quality` — feedback stats + evaluation scores

System:
- `GET /health`, `GET /ready`, `GET /metrics`

All errors use one JSON format: `{error: {code, message, request_id}}`.

---

## 7. Phases (build in this order)

### Phase 0 — Project setup
- Folder structure: `api/`, `worker/`, `shared/` (models, config, clients), `frontend/`, `widget/`, `infra/`, `tests/`, `loadtests/`, `eval/`
- docker-compose with Postgres, Redis, RabbitMQ, Qdrant, MinIO, Prometheus, Grafana
- Config with pydantic-settings, `.env.example`, Makefile (`make up`, `make test`, `make lint`)
- Ruff + mypy + pre-commit, GitHub Actions CI running lint + tests
- **Done when:** `make up` starts everything and `/health` returns ok; CI is green.

### Phase 1 — Tenants, auth, API keys
- Signup/login with JWT, password hashing (argon2 or bcrypt)
- API keys: random key with prefix like `rf_live_...`, store SHA-256 hash, check on each request
- Middleware: request ID, tenant context, structured JSON logs
- **Done when:** tests cover signup, login, key create/revoke, and wrong key -> 401.

### Phase 2 — Upload + ingestion pipeline
- Upload to MinIO, save document row, publish job to RabbitMQ
- Worker: parse (pypdf / python-docx / BeautifulSoup), clean text, keep page numbers
- Chunking: recursive splitter by tokens (default 500 tokens, 50 overlap), configurable
- Embed in batches, upsert to Qdrant with payload `{tenant_id, document_id, chunk_id, page}`
- Save chunks + tsvector to Postgres
- Retries + dead-letter queue, status updates, duplicate detection by SHA-256
- **Done when:** uploading a 50-page PDF ends in `ready`; a broken file ends in `failed` with an error message; tests pass.

### Phase 3 — Retrieval + chat
- Hybrid retrieval: Postgres FTS (top 20) + Qdrant dense (top 20), merge with Reciprocal Rank Fusion
- Rerank top 20 -> keep top 5
- Prompt: system rules ("answer only from context, say 'I don't know' if not found, cite sources as [1], [2]")
- LLM provider interface with streaming; SSE endpoint
- Return citations: document name, page, chunk text snippet
- Chat history: use last N messages; rewrite follow-up questions into standalone questions
- **Done when:** answers cite correct pages; questions outside the docs get "I don't know"; tenant isolation test passes.

### Phase 4 — Caching, rate limits, cost tracking
- Exact cache: Redis key = hash(tenant_id + normalized question + doc version), TTL configurable
- Semantic cache: embed question, search a per-tenant cache in Qdrant, reuse answer if similarity > 0.95 (configurable)
- Invalidate a tenant's cache when their documents change
- Rate limit: token bucket in Redis using a Lua script (atomic), limits per API key by plan; return `429` + `Retry-After`
- Count tokens and cost per message; update `usage_daily`
- **Done when:** a repeated question is served from cache (log shows `cache_hit=true` and much lower latency); the rate limit test passes.

### Phase 5 — Dashboard + embeddable widget
- Next.js dashboard: login, upload documents (drag and drop, live status), API keys page, chat playground, analytics charts (queries/day, p95 latency, cache hit rate, cost)
- Widget: `<script src=".../widget.js" data-api-key="..."></script>` adds a floating chat bubble; uses a public key with domain allow-list
- **Done when:** I can upload a file in the dashboard and chat with it from a plain HTML test page using the widget.

### Phase 6 — Observability + evaluation
- Prometheus metrics: request count, latency histograms per endpoint, ingestion queue depth, job failures, cache hit rate, LLM latency
- OpenTelemetry tracing across API -> retrieval -> rerank -> LLM (see the time of each step)
- Grafana dashboard JSON committed in `infra/grafana/`
- Evaluation script in `eval/`: a small test set (30–50 Q&A pairs on sample docs), measure retrieval hit rate@5, MRR, and answer faithfulness (LLM-as-judge). Save results as a markdown report.
- Compare 2–3 settings (chunk size 300 vs 500 vs 1000; with vs without reranker) and record the results in the README
- **Done when:** Grafana shows live metrics and the eval report exists with numbers.

### Phase 7 — Load testing + hardening
- k6 scripts: chat endpoint (cached and non-cached), upload endpoint
- Record: requests/sec, p50/p95/p99 latency, error rate. Find and fix at least one bottleneck (e.g., DB connection pool size, batch embedding, async I/O) and record before/after numbers.
- Security: input size limits, file type checks, prompt-injection basic guard (do not follow instructions found inside documents), CORS settings, secrets only from env
- **Done when:** the load test report is in `loadtests/RESULTS.md` with real numbers.

### Phase 8 — Deploy + docs
- Production docker-compose (or deploy to Railway / Render / Fly.io / a free-tier VM)
- README with: problem, architecture diagram (Mermaid), design decisions and trade-offs, how to run locally, API examples (curl), eval results, load test results, screenshots/GIF, future work
- `docs/SYSTEM_DESIGN.md`: how to scale to 1,000 tenants and 10M chunks (sharding Qdrant, read replicas, more workers, CDN for the widget)
- **Done when:** a public demo link works and the README is complete.

---

## 8. Final deliverables checklist
- [ ] All phases done, tests passing, CI green
- [ ] Tenant isolation proven by tests
- [ ] Eval report with numbers
- [ ] Load test report with before/after numbers
- [ ] Grafana dashboard
- [ ] README + SYSTEM_DESIGN.md + architecture diagram
- [ ] Live demo link

At the end, also write **3–4 resume bullet points** for this project using the real numbers we measured (for example: "Built a multi-tenant RAG platform handling X req/s at p95 Y ms; hybrid retrieval + reranking improved hit rate@5 from A% to B%; semantic cache cut LLM cost by C%").

Also write a list of **10 interview questions** someone could ask about this project, with short answers, so I can prepare.

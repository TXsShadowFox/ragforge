# Progress

Checklist of the phases in [PROJECT_SPEC.md](PROJECT_SPEC.md). Updated after every phase.

## Phase 0: Project setup (done)

- [x] Folder structure: `api/`, `worker/`, `shared/`, `frontend/`, `widget/`, `infra/`, `tests/`, `loadtests/`, `eval/`
- [x] docker-compose: Postgres, Redis, RabbitMQ, Qdrant, RustFS (S3), Prometheus, Grafana + API
- [x] Settings with pydantic-settings, `.env.example`, Makefile
- [x] ruff + mypy (strict) + pre-commit git hooks
- [x] GitHub Actions CI: lint + types, tests, Docker build
- [x] `/health`, `/ready`, `/metrics`, with 14 unit tests and 2 integration tests
- [x] `make up` starts all 8 containers (healthy); `/health` and `/ready` return ok; Prometheus scrapes the API
- [x] All 16 tests pass, including the 2 integration tests on real containers
- [x] CI is green on GitHub: https://github.com/TXsShadowFox/ragforge/actions

## Phase 1: Tenants, auth, API keys (done)

- [x] Signup and login with JWT (HS256, 60 min); passwords hashed with argon2id
- [x] API keys (`rf_live_...`): store only the SHA-256 hash, check on each request
- [x] Public keys (`rf_pub_...`) with allowed origins, refused until the widget (Phase 5)
- [x] Middleware: request ID, tenant context, structured JSON logs; one JSON error format
- [x] Tables `tenants`, `users`, `api_keys` with Alembic migrations (`migrate` container in `make up`)
- [x] Tests: signup, login, key create/revoke, wrong key returns 401, roles, tenant isolation
      (87 tests: 61 unit + 26 integration)

## Phase 2: Upload + ingestion pipeline

- [ ] Upload to object storage, save the document row, publish a job to RabbitMQ
- [ ] Worker: parse (PDF, DOCX, TXT, MD, HTML), clean text, keep page numbers
- [ ] Token-based recursive chunking (default 500 tokens, 50 overlap)
- [ ] Batch embeddings, upsert to Qdrant; save chunks + tsvector in Postgres
- [ ] Retries + dead-letter queue, status updates, duplicate detection by SHA-256
- [ ] A 50-page PDF ends in `ready`; a broken file ends in `failed` with an error

## Phase 3: Retrieval + chat

- [ ] Hybrid retrieval: Postgres full-text + Qdrant dense, merged with RRF
- [ ] Rerank the top 20, keep the top 5
- [ ] Prompt rules: answer only from context, say "I don't know", cite as [1], [2]
- [ ] LLM provider interface with streaming; SSE endpoint
- [ ] Citations: document name, page, snippet
- [ ] Chat history + rewriting follow-up questions
- [ ] Tenant isolation test: tenant A never sees tenant B's data

## Phase 4: Caching, rate limits, cost tracking

- [ ] Exact cache in Redis (tenant + normalized question + docs version)
- [ ] Semantic cache in Qdrant (similarity > 0.95, configurable)
- [ ] Invalidate a tenant's cache when its documents change
- [ ] Token-bucket rate limit (Redis + Lua), `429` + `Retry-After`
- [ ] Tokens and cost per message; `usage_daily`

## Phase 5: Dashboard + embeddable widget

- [ ] Next.js dashboard: login, upload with live status, API keys, chat playground, analytics
- [ ] Widget: one `<script>` tag, public key with a domain allow-list

## Phase 6: Observability + evaluation

- [ ] Prometheus metrics: requests, latency, queue depth, failures, cache hit rate, LLM latency
- [ ] OpenTelemetry tracing: API, retrieval, rerank, LLM
- [ ] Grafana dashboard JSON in `infra/grafana/`
- [ ] Eval set (30-50 Q&A): hit rate@5, MRR, faithfulness; compare chunk sizes and the reranker

## Phase 7: Load testing + hardening

- [ ] k6 scripts: chat (cached / not cached), upload
- [ ] Find and fix at least one bottleneck, with before/after numbers in `loadtests/RESULTS.md`
- [ ] Security: size limits, file type checks, prompt-injection guard, CORS, secrets only from env

## Phase 8: Deploy + docs

- [ ] Production docker-compose or a free-tier deploy, with a public demo link
- [ ] Full README: architecture (Mermaid), decisions, how to run, curl examples, results
- [ ] `docs/SYSTEM_DESIGN.md`: scaling to 1,000 tenants and 10M chunks

## Final deliverables

- [ ] All phases done, tests passing, CI green
- [ ] Tenant isolation proven by tests
- [ ] Eval report with numbers
- [ ] Load test report with before/after numbers
- [ ] Grafana dashboard
- [ ] README + SYSTEM_DESIGN.md + architecture diagram
- [ ] Live demo link
- [ ] Resume bullet points + 10 interview questions with answers

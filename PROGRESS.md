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

## Phase 2: Upload + ingestion pipeline (done)

- [x] Upload to object storage, save the document row + job in one transaction (outbox pattern)
- [x] Worker: parse (PDF, DOCX, TXT, MD, HTML), clean text, keep page numbers
- [x] Token-based recursive chunking (default 500 tokens, 50 overlap), in the model's own tokens
- [x] Batch embeddings (fastembed, bge-small, ONNX), upsert to Qdrant; chunks + tsvector in Postgres
- [x] Retries (10 s, 1 min, 5 min) + dead-letter queue, status updates, duplicates by SHA-256
- [x] Delete from Postgres, Qdrant and storage (a worker job); list with cursor paging
- [x] A 50-page PDF ends in `ready`; a broken file ends in `failed` with an error
      (real model in Docker: 50 pages, 22,500 words -> 60 chunks, ready in 11.3 s)
- [x] Tests: 163 (118 unit + 45 integration), incl. retries, dead-letter queue, tenant isolation
- [x] Measured: `EMBEDDING_BATCH_SIZE` 32 -> 8 cut worker RAM 941 -> 594 MB and time 16.5 -> 11.3 s

## Phase 3: Retrieval + chat (done)

- [x] Hybrid retrieval: Postgres full-text (words joined with OR) + Qdrant dense, merged with RRF
- [x] Rerank the top 20 (ms-marco-MiniLM-L-6-v2), keep the top 5; "not relevant" below -5
- [x] Prompt rules: answer only from context, say "I don't know", cite as [1], [2]
- [x] LLM provider interface (any OpenAI-compatible API; Groq gpt-oss-20b by default) with
      streaming; SSE on `POST /v1/chat` with `"stream": true`
- [x] Citations: document name, exact page (chunks never cross pages), snippet
- [x] Chat history + rewriting follow-up questions; feedback (`POST /v1/messages/{id}/feedback`)
- [x] Tenant isolation test: tenant A never sees tenant B's data (not even in the prompt)
- [x] Real check (Docker, real models, Groq): 5 of 5 questions right with exact pages;
      "capital of France" -> "I don't know" without an LLM call; first word after 0.54 s
- [x] Tests: 206 (145 unit + 61 integration)

## Phase 4: Caching, rate limits, cost tracking (done)

- [x] Exact cache in Redis (tenant + docs version + cleaned question + `top_k` and model), 24 h
- [x] Semantic cache in Qdrant: similarity >= 0.98, configurable (measured: 0.95 can reuse a wrong answer)
- [x] Invalidate a tenant's cache when its documents change (`docs_version`; old entries deleted)
- [x] Token-bucket rate limits (Redis + Lua): requests and questions per key or user, by plan;
      logins per email; `429` + `Retry-After`
- [x] Redis down: the chat still works (limits allow, the semantic cache still answers)
- [x] Tokens and cost per message; `usage_daily`; `GET /v1/analytics/usage` (days, cache hit rate, p50/p95)
- [x] Real check (Docker, Groq): first answer 1,296 ms, the same question from the cache 18 ms
      (no tokens); a reworded question hit the semantic cache; the 11th question in a minute got
      429 (`Retry-After: 4`); the 6th login try got 429
- [x] Tests: 241 (160 unit + 81 integration). The test setup now empties the Qdrant collections
      instead of making them again: the suite went from 3 min 42 s to 1 min 35 s

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

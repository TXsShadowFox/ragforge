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

## Phase 5: Dashboard + embeddable widget (done)

- [x] Next.js 16 dashboard: sign up and log in, documents (drag and drop, live status, delete),
      API keys (create, shown once, revoke, embed code), chat playground (streaming, sources,
      cache hits, ratings, follow-ups), analytics charts (questions/day, p50/p95, cache hit rate, cost)
- [x] The login token stays in an httpOnly cookie; the dashboard's server forwards `/api/v1/...`
      to the API (uploads and streamed answers pass straight through); same-origin check for changes
- [x] Widget: one `<script>` tag, plain JS (no build, no libraries), Shadow DOM, streamed answers
      with sources, ratings; public key with a domain allow-list (`Origin`), CORS
- [x] Per-visitor question limit for public keys, next to the website's limit
- [x] Docker: a `frontend` container in `make up`; CI: a dashboard job (lint, types, tests, build)
- [x] Real check (Docker, Groq, Edge): the Playwright test signs up, uploads a file, waits for
      "ready", creates a public key, opens a plain HTML page on another address
      (`localhost:5500`), and asks the widget: answer with the right fact and its source (34 s)
- [x] Tests: Python 258 (165 unit + 93 integration), dashboard and widget 51 (Vitest), 1 end-to-end

## Phase 6: Observability + evaluation (done)

- [x] Prometheus metrics: requests and latency per route, LLM time (and first piece), tokens,
      cost, errors, search step times, answers by source (cache hit rate), 429s; worker (port
      8001): jobs by outcome, job time, outbox backlog; queue depth from RabbitMQ's own plugin
- [x] OpenTelemetry tracing (Jaeger): one trace per request with a span per step (embed, cache,
      vector and keyword search, rerank, LLM, save); the trace follows a job through the outbox
      and RabbitMQ into the worker (download, parse, chunk, embed, store); `trace_id` in every log line
- [x] Grafana dashboard JSON in `infra/grafana/` (30 panels; Prometheus and Jaeger data sources)
- [x] Answer quality in the dashboard: `GET /v1/analytics/quality` (thumbs up/down per day,
      the latest thumbs-down answers)
- [x] Eval (`make eval`): 5 sample documents, 48 questions; hit rate@1/3/5, MRR@10, faithfulness
      and correctness (LLM judge); 7 settings compared (chunks of 300/500/1000 tokens, with and
      without the reranker, vector/keyword/hybrid): [eval/RESULTS.md](eval/RESULTS.md)
- [x] Results: hybrid search + reranker puts the evidence first for 97% (MRR 0.986; without the
      reranker 89%, 0.940); answers: faithfulness 1.00, correctness 1.00, "I don't know" 12 of 12
- [x] The eval found 2 bugs, both fixed: gpt-oss citations like `【1】` were lost; the "I don't
      know" gate (-5) wrongly stopped 5 of 36 answerable questions. Gate -10: correctness
      0.83 -> 1.00 (the cost: 747 -> 1,442 input tokens per answer)
- [x] Real check (Docker): all Prometheus targets up, every Grafana panel shows live data;
      traces: the reranker is the slowest step before the LLM (0.7-1.1 s), and the first request
      after a start takes 10.3 s
- [x] Tests: Python 297 (194 unit + 103 integration), dashboard and widget 52 (Vitest)

## Phase 7: Load testing + hardening (done)

- [x] k6 scripts (`make loadtest`, k6 in Docker): chat answered from the cache, chat with new
      questions (the whole RAG path), uploads; a fake OpenAI-compatible LLM, so the load is on
      our code. Report with before/after numbers: [loadtests/RESULTS.md](loadtests/RESULTS.md)
- [x] Bottleneck 1, a database pool deadlock: each chat request kept the login check's
      connection and needed a second one. Cached answers, 50 users: 92% errors -> 0%, 70 a second
- [x] Bottleneck 2, the reranker: every question ran it at once, and 10 users ran the API out of
      memory (it was killed). Now at most 2 runs at a time and 10 candidates instead of 20:
      10-20 users without errors; new answers 0.7 -> 1.7 a second; 1 user p50 2.0 -> 1.3 s;
      search quality unchanged in `make eval`
- [x] Cold start: warm-up with real sizes and connections opened at startup (first answer after
      a new image: 7.5 -> 1.9 s)
- [x] Fewer tokens: only sources close to the best score (`SOURCE_SCORE_MARGIN`): 1.67 sources
      and 870 input tokens per answer (was 3.4 and 1,442); correctness 0.97, faithfulness 1.00
- [x] Security: request-size limit (413 before the body is read); rate limits per IP address
      (logins, sign-ups, wrong keys); prompt-injection guard (sources in `<source>` tags that a
      document cannot close; the evaluation's planted instructions were followed in 0 of 2
      answers); security headers on the API and a Content-Security-Policy on the dashboard; a
      test that secrets have no defaults; CORS (D50) and file type checks (D28) reviewed
- [x] CI runs the browser test on every push (the stack with the fake LLM, Chromium)
- [x] Tests: Python 330 (222 unit + 108 integration), dashboard and widget 54 (Vitest), 1 end-to-end

## Phase 8: Deploy + docs (done)

- [x] Production setup (`docker-compose.prod.yml`): Caddy with automatic HTTPS as the only public
      entry; dashboard + API on one address, the widget demo site on its own, Grafana read-only;
      real visitor addresses behind the proxy (also through the dashboard's server)
- [x] `deploy/setup.sh` (an Ubuntu server, start to finish) and `deploy/seed_demo.py` (the demo
      tenant with the sample documents and a public key); demo limits: 5 MB uploads, 20 documents
      per tenant (`MAX_DOCUMENTS_PER_TENANT`)
- [x] Tested on this laptop with the same files (local certificates): routing, closed ports,
      HTTP -> HTTPS, seeding twice, a widget answer through Caddy, the visitor's IP in the limits
- [x] The public demo on AWS EC2 (m7i-flex.large, 2 vCPUs, 8 GB, Sydney; AWS starter credit):
      https://demo.52.64.149.103.sslip.io (widget), https://ragforge.52.64.149.103.sslip.io
      (dashboard, API docs at /docs), https://grafana.52.64.149.103.sslip.io (metrics). Let's
      Encrypt certificate, only ports 80/443 open; a live widget question answered in 2.2 s
      with its source. Oracle Cloud's free VM is documented as the alternative
- [x] Full README: the problem, screenshots, architecture (Mermaid), results, decisions and
      trade-offs, how to run, curl examples, future work
- [x] `docs/SYSTEM_DESIGN.md`: scaling to 1,000 tenants and 10M chunks; `docs/DEPLOY.md`
- [x] CI checks the production compose file and the Caddyfile
- [x] Tests: Python 333 (224 unit + 109 integration), dashboard and widget 56 (Vitest), 1 end-to-end

## Final deliverables

- [x] All phases done, tests passing, CI green
- [x] Tenant isolation proven by tests
- [x] Eval report with numbers
- [x] Load test report with before/after numbers
- [x] Grafana dashboard
- [x] README + SYSTEM_DESIGN.md + architecture diagram
- [x] Live demo link
- [x] Resume bullet points + 10 interview questions with answers (given privately, not in
      the repo)

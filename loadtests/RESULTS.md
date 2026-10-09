# Load test results

Measured on 2026-10-08 with `make loadtest` (k6 2.3.0). Every number below comes from a real
run; the raw k6 output is written to `loadtests/results/` (not in git).

## Setup

- **Everything on one laptop:** Docker Desktop (WSL 2) with 16 logical CPUs and 3.9 GB of RAM
  for the containers. k6, the API (one process), the worker, Postgres, Redis, RabbitMQ,
  Qdrant, RustFS and the fake LLM share that machine. The host had 7.7 GB of RAM with only
  ~0.5 GB free, so p95 and p99 are noisy: compare p50, throughput and errors first. The
  numbers show what changed, not what a real server can do.
- **A fake LLM** (`loadtests/fake_llm.py`): OpenAI-compatible, 0.4 s until the first word,
  then 200 words a second, close to Groq's gpt-oss-20b in the evaluation. Groq's free tier
  allows ~30 requests a minute, so the real LLM cannot be load-tested.
- **The stack** (`docker-compose.fake-llm.yml`): rate limits raised, the semantic cache off
  (so a new question never reuses an answer), no tracing.
- **The data:** each test signs up a new tenant and uploads the 5 evaluation documents plus
  4 generated ones (36 chunks), so each search finds the full 20 candidates.
- **The tests**, each step 30 s with a fixed number of users (each sends its next request
  as soon as the last one is answered):
  - `chat_cached.js`: 12 questions asked again and again: answers from the cache.
  - `chat_uncached.js`: a new question every time: embed, two searches, rerank, LLM, save.
  - `upload.js`: a new ~2 KB text file every time; the worker processes them meanwhile.

## Before and after

### Chat, answers from the cache

| Users | Before: req/s | p50 | p95 | Errors | After: req/s | p50 | p95 | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 64.7 | 130 ms | 176 ms | 0% | 57.4 | 148 ms | 225 ms | 0% |
| 50 | 1.7 | 30.2 s | 30.3 s | **92%** | 70.1 | 675 ms | 1.3 s | **0%** |

### Chat, new questions

| Users | Before: req/s | p50 | p95 | Errors | After: req/s | p50 | p95 | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.5 | 2.0 s | 2.5 s | 0% | 0.6 | 1.3 s | 3.1 s | 0% |
| 5 | 0.7 | 8.4 s | 10.2 s | 0% | 1.5 | 3.4 s | 4.7 s | 0% |
| 10 | - | - | - | **100%: the API was killed** | 1.7 | 6.4 s | 6.9 s | **0%** |
| 20 | - | - | - | (not run: the API was down) | 1.7 | 12.6 s | 13.3 s | **0%** |

p99 after: 4.7 s, 5.5 s, 7.8 s and 14.5 s. With more users, questions wait in the reranker's
queue: the time grows, but nothing fails.

### Uploads

| Users | Before: req/s | p50 | p95 | Errors | After: req/s | p50 | p95 | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 51.6 | 86 ms | 142 ms | 0% | 34.0 | 95 ms | 301 ms | 0% |
| 20 | 55.1 | 353 ms | 481 ms | 0% | 52.2 | 363 ms | 557 ms | 0% |

The upload path only gained the size check and the security headers. Its 5-user step was
slower in all four runs after the changes, with about the same p50 but long pauses (p99 0.6
to 2.4 s); the laptop was short of memory by then (~0.5 GB free, and the API now starts at
~0.8 GB, as it warms the reranker up with full-size texts). The worker processed ~8
documents a second: after 3,201 uploads (before) it needed 360 s more, after 2,588 uploads
293 s.

## What the load tests found, and the fixes

### 1. Database connections: a deadlock under load (cached chat, 50 users: 92% errors)

**Found:** with 50 users every request waited 30 s, then failed with
`QueuePool limit of size 5 overflow 10 reached, connection timed out`. Each chat request
kept the connection of its login check (the request's database session) until the answer
was done, and the answer's own steps needed a second connection. Once 15 requests held all
15 connections, each waited for a second one that never came free.

**Fix:** the chat route gives that connection back after the login check
(`release_db_connection` in `api/dependencies.py`). A test runs a chat with a pool of one
connection: before the fix it timed out. Pool sizes are settings now (`DB_POOL_SIZE`,
`DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`).

**Result:** 50 users: 70 answers a second, p50 675 ms, no errors.

**Why only the chat route:** giving the connection back in every route also worked, but
every upload then took a second connection (with its ping and rollback round trips):
uploads went from 37.5 to 33.6 requests a second (5 users) and from 51.4 to 42.1 (20 users).

### 2. The reranker: out of memory (new questions, 10 users: the API was killed)

**Found:** with 10 users the API container stopped (`OOMKilled=true`, exit code 137). The
reranker (ms-marco-MiniLM-L-6-v2) scored 20 chunks of up to 512 tokens per question: 2.1 s
on average, and each run needs a few hundred MB. Every question ran it at once, so memory
grew with the number of users until the container had none left. The reranker was also the
time: embedding took 21 ms, the two searches under 50 ms, the fake LLM ~0.5 s.

**Fix:**
- At most `RERANK_CONCURRENCY` (2) reranker runs at the same time per API process; other
  questions wait their turn without holding a thread (`api/chat/retrieval.py`). A cancelled
  question keeps its slot until its run ends (a thread cannot be stopped).
- The reranker scores the best `RERANK_CANDIDATES` (10) chunks after the fusion, not 20.
  The evaluation found no loss: hit@1 97% and MRR 0.986 with 10, as with 20.

**Result:** no errors with 10 or 20 users; the API stays at ~1.0 GB. One user: p50 2.0 s
to 1.3 s (the reranker alone: 2.1 s to 0.84 s). Throughput: 0.7 to 1.5-1.7 new answers a
second.

**Why 2 runs at a time:** one run already uses all CPU cores, so two runs take ~1.7 s each
instead of 0.84 s. But the other steps can overlap: with 1, it was 1.3 answers a second at
5 and 10 users; with 2, 1.5 and 1.7.

### 3. Cold start

The first question after a fresh start ran the reranker on long texts for the first time
(new buffers). The API now warms it up at startup with 10 full-size texts, and opens one
connection to each service (the /ready probes). First answer after a new image: 7.5 s
without the warm-up, 1.9 s with it (one start each); after a plain restart both took
1.5-1.9 s.

## Limits that remain

- **The reranker's CPU:** ~1.7 new answers a second on this laptop. More needs more CPU
  (or API copies), a GPU, a smaller reranker, fewer candidates or shorter texts, each
  checked with `make eval`.
- **One Python process:** ~55-70 cheap requests a second (cached answers, uploads). More
  needs several processes or copies; each loads the models (~1 GB of RAM).
- **The LLM:** in real use, Groq's free tier (8,000 tokens a minute) allows a few answers a
  minute, far below these numbers. The answer cache and the shorter prompts (Phase 7: 1.7
  sources per answer instead of 3.4) stretch it.

## Run it

```
make loadtest           # starts the stack with the fake LLM, then the three tests
docker compose -f docker-compose.yml -f docker-compose.fake-llm.yml run --rm \
  -e USERS=1,5 -e SECONDS=20 -e STREAM=true k6 run chat_uncached.js
make down               # stop everything afterwards
```

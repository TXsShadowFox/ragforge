# RAGForge

A multi-tenant **RAG-as-a-Service** platform. Companies upload their documents and get:

- a REST + streaming chat API that answers from their documents, with citations
- an embeddable chat widget (one `<script>` tag)
- a dashboard for documents, usage, cost, latency and answer quality

> Work in progress. See [PROGRESS.md](PROGRESS.md). The full README (architecture,
> load test results) comes in Phase 8.

## Quick start

Needs Docker Desktop, [uv](https://docs.astral.sh/uv/) and GNU make.

```bash
make setup                         # install packages, create .env, install git hooks
make up                            # start the API and all services in Docker
curl http://localhost:8000/health  # {"status":"ok"}
```

Run `make` to see all commands. Developer notes are in [CLAUDE.md](CLAUDE.md).

## Evaluation

`make eval` measures search and answers on 6 sample documents (PDF, Markdown, HTML, text; one
with planted instructions) with 50 questions: 38 answerable, 12 not. Full report:
[eval/RESULTS.md](eval/RESULTS.md).

| Search setting | hit@1 | hit@5 | MRR@10 |
|---|---:|---:|---:|
| Hybrid search + reranker (the default) | 97% | 100% | 0.987 |
| Hybrid search, no reranker | 87% | 100% | 0.930 |
| Vector search only, no reranker | 87% | 97% | 0.908 |
| Chunks of 300 tokens (default: 500) | 92% | 100% | 0.956 |
| Chunks of 1000 tokens | 95% | 100% | 0.967 |

Answers (`gpt-oss-20b`, judged by `gpt-oss-120b`): faithfulness 1.00, correctness 0.97,
"I don't know" for 12 of 12 questions without an answer, and the planted instructions
followed in 0 of 2 answers. The corpus is small (15 chunks), so hit@5 is near 100%
everywhere; hit@1 and MRR show the differences.

## Load tests

`make loadtest` runs k6 against the stack with a fake LLM (Groq's free tier allows too few
requests to load-test). Everything ran on one laptop, so the numbers compare before and after
the fixes, not real capacity. Full report: [loadtests/RESULTS.md](loadtests/RESULTS.md).

| Test | Before | After |
|---|---|---|
| Cached answers, 50 users | 92% errors (a database pool deadlock) | 70 answers/s, p50 675 ms, no errors |
| New questions, 10 users | the API ran out of memory and was killed | 1.7 answers/s, p50 6.4 s, no errors |
| New questions, 1 user | p50 2.0 s | p50 1.3 s |

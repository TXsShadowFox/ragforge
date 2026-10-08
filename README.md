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

`make eval` measures search and answers on 5 sample documents (PDF, Markdown, HTML, text)
with 48 questions: 36 answerable, 12 not. Full report: [eval/RESULTS.md](eval/RESULTS.md).

| Search setting | hit@1 | hit@5 | MRR@10 |
|---|---:|---:|---:|
| Hybrid search + reranker (the default) | 97% | 100% | 0.986 |
| Hybrid search, no reranker | 89% | 100% | 0.940 |
| Vector search only, no reranker | 86% | 97% | 0.904 |
| Chunks of 300 tokens (default: 500) | 92% | 100% | 0.954 |
| Chunks of 1000 tokens | 94% | 100% | 0.965 |

Answers (`gpt-oss-20b`, judged by `gpt-oss-120b`): faithfulness 1.00, correctness 1.00,
and "I don't know" for 12 of 12 questions without an answer. The corpus is small (14
chunks), so hit@5 is near 100% everywhere; hit@1 and MRR show the differences.

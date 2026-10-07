# RAGForge

A multi-tenant **RAG-as-a-Service** platform. Companies upload their documents and get:

- a REST + streaming chat API that answers from their documents, with citations
- an embeddable chat widget (one `<script>` tag)
- a dashboard for documents, usage, cost, latency and answer quality

> Work in progress. See [PROGRESS.md](PROGRESS.md). The full README (architecture,
> eval results, load test results) comes in Phase 8.

## Quick start

Needs Docker Desktop, [uv](https://docs.astral.sh/uv/) and GNU make.

```bash
make setup                         # install packages, create .env, install git hooks
make up                            # start the API and all services in Docker
curl http://localhost:8000/health  # {"status":"ok"}
```

Run `make` to see all commands. Developer notes are in [CLAUDE.md](CLAUDE.md).

"""Run the evaluation: `make eval`, or `uv run python -m eval.run [--no-answers] [--limit N]`.

Postgres and Qdrant run in throw-away containers (like the integration tests), so your dev
data is never touched; only Docker must be running. The models are the real ones, and the
LLM settings (and LLM_API_KEY) come from your .env. Writes eval/RESULTS.md and
eval/results.json.
"""

import argparse
import asyncio
import logging
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.qdrant import QdrantContainer

from api.ai import load_ai_services
from eval.data import EVAL_DIR, check_test_set, load_corpus, load_questions
from eval.harness import run_evaluation
from eval.report import RunInfo, render_json, render_markdown
from shared.clients import Clients
from shared.config import Settings
from shared.db.migrate import upgrade
from shared.llm import OpenAICompatibleLLM
from shared.vector_store import ensure_collection

logger = logging.getLogger("eval")

COMPOSE_FILE = EVAL_DIR.parent / "docker-compose.yml"
DEFAULT_JUDGE = "openai/gpt-oss-120b"  # bigger than the answering model, its own free quota


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure search and answer quality.")
    parser.add_argument(
        "--no-answers",
        action="store_true",
        help="only search quality: no LLM calls, about 2 minutes",
    )
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE, help="the LLM that scores answers")
    parser.add_argument("--limit", type=int, help="only the first N questions (a quick try)")
    parser.add_argument("--out-dir", type=Path, default=EVAL_DIR, help="where the report goes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "testcontainers", "api.chat.retrieval"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(run(args))


async def run(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    questions, corpus = load_questions(), load_corpus()
    problems = check_test_set(questions, corpus)
    if problems:
        sys.exit("The test set has problems:\n" + "\n".join(problems))
    if args.limit:
        questions = questions[: args.limit]

    logger.info("Starting Postgres and Qdrant in containers")
    with (
        PostgresContainer(compose_image("postgres"), driver="asyncpg") as postgres,
        QdrantContainer(compose_image("qdrant")) as qdrant,
    ):
        settings = Settings(  # everything else (models, LLM, API key) comes from .env
            database_url=postgres.get_connection_url(),
            qdrant_url=f"http://{qdrant.rest_host_address}",
            qdrant_collection="eval_chunks",
        )
        clients = Clients.create(settings)
        logger.info("Loading the models")
        ai = await load_ai_services(settings)
        judge = (
            None
            if args.no_answers
            else OpenAICompatibleLLM(
                settings.model_copy(update={"llm_model": args.judge_model, "llm_temperature": 0.0})
            )
        )
        try:
            await upgrade(clients.db)
            await ensure_collection(
                clients.qdrant, settings.qdrant_collection, ai.embedder.dimension
            )
            evaluation = await run_evaluation(
                questions, corpus, settings=settings, clients=clients, ai=ai, judge=judge
            )
        finally:
            await ai.llm.aclose()
            if judge is not None:
                await judge.aclose()
            await clients.aclose()

    info = RunInfo(
        date=datetime.now(UTC).strftime("%Y-%m-%d"),
        embedding_model=settings.embedding_model,
        rerank_model=settings.rerank_model,
        llm_model=settings.llm_model,
        judge_model=None if args.no_answers else args.judge_model,
        min_rerank_score=settings.min_rerank_score,
        max_embedding_tokens=ai.embedder.max_tokens,
        seconds=time.perf_counter() - started,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = args.out_dir / "RESULTS.md"
    markdown = render_markdown(evaluation, questions, corpus, info)
    report.write_text(markdown, encoding="utf-8", newline="\n")
    results = args.out_dir / "results.json"
    results.write_text(render_json(evaluation, info), encoding="utf-8", newline="\n")
    logger.info("Wrote %s", report)


def compose_image(name: str) -> str:
    """The image that docker-compose.yml uses for `name` (the same versions as dev)."""
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    for image in re.findall(r"^\s*image:\s*(\S+)\s*$", text, flags=re.MULTILINE):
        repository = image.rsplit(":", 1)[0]
        if repository == name or repository.endswith(f"/{name}"):
            return str(image)
    raise LookupError(f"No image for {name!r} in {COMPOSE_FILE.name}")


if __name__ == "__main__":
    main()

"""The AI models the API uses: the embedder (questions), the reranker and the LLM.

They are loaded once at startup. Tests pass fast fakes to `create_app(..., ai=...)`.
"""

import asyncio
from dataclasses import dataclass, field

from shared.config import Settings
from shared.embeddings import Embedder, FastEmbedEmbedder
from shared.llm import LLM, OpenAICompatibleLLM
from shared.rerank import FastEmbedReranker, Reranker

# About 500 tokens, like a full chunk (for the warm-up).
_FULL_SIZE_TEXT = " ".join(["warm up"] * 250)


@dataclass(frozen=True, slots=True)
class AIServices:
    embedder: Embedder
    reranker: Reranker
    llm: LLM
    # How many reranker runs may happen at the same time (RERANK_CONCURRENCY): each run
    # needs a lot of memory and all the CPU it can get, so more questions wait instead.
    rerank_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))


async def load_ai_services(settings: Settings) -> AIServices:
    """Load both local models at the same time (each downloads once, the first time)."""
    embedder, reranker = await asyncio.gather(
        asyncio.to_thread(
            FastEmbedEmbedder,
            settings.embedding_model,
            settings.model_cache_dir,
            settings.embedding_batch_size,
        ),
        asyncio.to_thread(FastEmbedReranker, settings.rerank_model, settings.model_cache_dir),
    )
    # The first run of an ONNX model is slow (it prepares itself), and so is the reranker's
    # first run with long texts (bigger buffers). Do both now, with real sizes, instead of
    # in the first user's request.
    full_size = [_FULL_SIZE_TEXT] * settings.rerank_candidates
    await asyncio.gather(
        asyncio.to_thread(embedder.embed_query, "warm up"),
        asyncio.to_thread(reranker.rerank, "warm up", full_size),
    )
    return AIServices(
        embedder=embedder,
        reranker=reranker,
        llm=OpenAICompatibleLLM(settings),
        rerank_slots=asyncio.Semaphore(settings.rerank_concurrency),
    )

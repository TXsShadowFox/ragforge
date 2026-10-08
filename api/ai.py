"""The AI models the API uses: the embedder (questions), the reranker and the LLM.

They are loaded once at startup. Tests pass fast fakes to `create_app(..., ai=...)`.
"""

import asyncio
from dataclasses import dataclass

from shared.config import Settings
from shared.embeddings import Embedder, FastEmbedEmbedder
from shared.llm import LLM, OpenAICompatibleLLM
from shared.rerank import FastEmbedReranker, Reranker


@dataclass(frozen=True, slots=True)
class AIServices:
    embedder: Embedder
    reranker: Reranker
    llm: LLM


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
    # The first run of an ONNX model is slow (it prepares itself). Do it now, at startup,
    # instead of in the first user's request.
    await asyncio.gather(
        asyncio.to_thread(embedder.embed_query, "warm up"),
        asyncio.to_thread(reranker.rerank, "warm up", ["warm up"]),
    )
    return AIServices(embedder=embedder, reranker=reranker, llm=OpenAICompatibleLLM(settings))

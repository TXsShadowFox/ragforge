"""Embeddings: turn text into vectors (lists of numbers) that capture its meaning.

`Embedder` is the interface the rest of the code uses. The default runs
`BAAI/bge-small-en-v1.5` on the CPU with fastembed (ONNX, no PyTorch). Another provider
(like OpenAI) can be added later as one more class.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from fastembed import TextEmbedding
from tokenizers import Tokenizer


class Embedder(Protocol):
    @property
    def dimension(self) -> int:
        """How many numbers each vector has."""
        ...

    @property
    def max_tokens(self) -> int:
        """The longest input the model reads, in tokens (with its special tokens)."""
        ...

    def count_tokens(self, text: str) -> int:
        """Tokens in `text`, without the model's special start and end tokens."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text. May be slow: call it with `asyncio.to_thread`."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """The vector of a search question (some models want an instruction in front)."""
        ...


# Text that some models want in front of a search question (from their model cards).
# Documents get no instruction, so short questions and long chunks still match well.
QUERY_INSTRUCTIONS = {
    "baai/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
}


def embedding_dimension(model_name: str) -> int:
    """How many numbers a fastembed model's vectors have, without downloading the model."""
    for model in TextEmbedding.list_supported_models():
        if str(model["model"]).lower() == model_name.lower():
            return int(model["dim"])
    raise ValueError(f"Unknown embedding model: {model_name}")


class FastEmbedEmbedder:
    """Runs an embedding model locally with fastembed.

    Creating it loads the model, and downloads it the first time (bge-small: 67 MB).
    Every method uses the CPU for a while: call them with `asyncio.to_thread`.
    """

    def __init__(self, model_name: str, cache_dir: Path, batch_size: int = 8) -> None:
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
        self._batch_size = batch_size
        self._dimension = embedding_dimension(model_name)
        self._query_instruction = QUERY_INSTRUCTIONS.get(model_name.lower(), "")
        # fastembed keeps the tokenizer on its inner model object (not a public attribute).
        model_tokenizer = getattr(self._model.model, "tokenizer", None)
        truncation = getattr(model_tokenizer, "truncation", None)
        if not isinstance(model_tokenizer, Tokenizer) or truncation is None:
            raise RuntimeError(f"The model {model_name} has no tokenizer with a length limit.")
        self._max_tokens = int(truncation["max_length"])
        # Count tokens with a copy of the model's tokenizer that never cuts or pads the text.
        self._counter = Tokenizer.from_str(model_tokenizer.to_str())
        self._counter.no_truncation()
        self._counter.no_padding()

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def count_tokens(self, text: str) -> int:
        return len(self._counter.encode(text, add_special_tokens=False).ids)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.passage_embed(list(texts), batch_size=self._batch_size)
        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        [vector] = self._model.query_embed([self._query_instruction + text])
        return [float(number) for number in vector]

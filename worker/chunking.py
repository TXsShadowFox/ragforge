"""Split a document's text into chunks of at most `chunk_size` tokens.

1. Each page is chunked on its own: a chunk never crosses a page break, so its citation
   names exactly one page, and a short page is not mixed with its neighbours' text.
   (Formats without pages are one long "page".)
2. Cut the page into small units: sentences or lines. A unit that is still too long is
   cut into words, and a word that is still too long is cut in halves.
3. Pack units into chunks of at most `chunk_size` tokens.
4. Start each new chunk with the last units of the one before, up to `overlap` tokens,
   so a fact on the border between two chunks is complete in at least one of them.

Tokens are counted with the embedding model's own tokenizer, so a chunk is never cut off
by the model.
"""

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from worker.parsing import Page

CountTokens = Callable[[str], int]

# Where a unit ends: after line breaks, or after the end of a sentence (. ! ? and spaces).
_UNIT_END = re.compile(r"\n+|(?<=[.!?])[ \t]+")
# Where a word ends: after whitespace.
_WORD_END = re.compile(r"(?<=\s)")


@dataclass(frozen=True, slots=True)
class TextChunk:
    index: int  # 0, 1, 2, ... in reading order
    text: str
    page_number: int | None  # the PDF page; None for formats without pages
    token_count: int


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    tokens: int


def chunk_id(document_id: uuid.UUID, index: int) -> uuid.UUID:
    """Always the same ID for the same document and chunk number.

    So a job that runs twice overwrites its own rows in Postgres and Qdrant.
    """
    return uuid.uuid5(document_id, str(index))


def chunk_document(
    pages: Sequence[Page], count_tokens: CountTokens, chunk_size: int, overlap: int
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for page in pages:
        units = _split_into_units(page.text, count_tokens, chunk_size)
        for group in _pack(units, chunk_size, overlap):
            text = "".join(unit.text for unit in group).strip()
            chunks.append(
                TextChunk(
                    index=len(chunks),
                    text=text,
                    page_number=page.number,
                    token_count=count_tokens(text),
                )
            )
    return chunks


def _split_into_units(text: str, count_tokens: CountTokens, limit: int) -> list[_Unit]:
    units: list[_Unit] = []
    start = 0
    for match in _UNIT_END.finditer(text):
        units.extend(_fit(text[start : match.end()], count_tokens, limit))
        start = match.end()
    units.extend(_fit(text[start:], count_tokens, limit))
    return [unit for unit in units if unit.text.strip()]


def _fit(text: str, count_tokens: CountTokens, limit: int) -> list[_Unit]:
    """`text` as one unit if it fits; otherwise cut into words, or into halves."""
    if not text:
        return []
    tokens = count_tokens(text)
    if tokens <= limit:
        return [_Unit(text, tokens)]
    words = [word for word in _WORD_END.split(text) if word]
    if len(words) > 1:
        return [unit for word in words for unit in _fit(word, count_tokens, limit)]
    # One very long "word", like a long URL: cut it in two halves until each part fits.
    middle = len(text) // 2
    return _fit(text[:middle], count_tokens, limit) + _fit(text[middle:], count_tokens, limit)


def _pack(units: list[_Unit], limit: int, overlap: int) -> list[list[_Unit]]:
    """Group units into chunks of at most `limit` tokens, each starting with an overlap."""
    groups: list[list[_Unit]] = []
    first = 0
    while first < len(units):
        end = first
        tokens = 0
        while end < len(units) and tokens + units[end].tokens <= limit:
            tokens += units[end].tokens
            end += 1
        groups.append(units[first:end])
        if end == len(units):
            break
        # The next chunk repeats the last units of this one, up to `overlap` tokens.
        # It always starts at least one unit later, so the loop ends.
        next_first = end
        repeated = 0
        while next_first - 1 > first and repeated + units[next_first - 1].tokens <= overlap:
            next_first -= 1
            repeated += units[next_first].tokens
        first = next_first
    return groups

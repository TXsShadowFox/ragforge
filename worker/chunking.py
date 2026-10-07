"""Split a document's text into chunks of at most `chunk_size` tokens.

1. Cut the text into small units: sentences or lines. A unit that is still too long is
   cut into words, and a word that is still too long is cut in halves.
2. Pack units into chunks of at most `chunk_size` tokens.
3. Start each new chunk with the last units of the one before, up to `overlap` tokens,
   so a fact on the border between two chunks is complete in at least one of them.

Tokens are counted with the embedding model's own tokenizer, so a chunk is never cut off
by the model. Each chunk remembers the PDF page where it starts.
"""

import bisect
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from worker.parsing import Page

CountTokens = Callable[[str], int]

PAGE_SEPARATOR = "\n\n"
# Where a unit ends: after line breaks, or after the end of a sentence (. ! ? and spaces).
_UNIT_END = re.compile(r"\n+|(?<=[.!?])[ \t]+")
# Where a word ends: after whitespace.
_WORD_END = re.compile(r"(?<=\s)")


@dataclass(frozen=True, slots=True)
class TextChunk:
    index: int  # 0, 1, 2, ... in reading order
    text: str
    page_number: int | None
    token_count: int


@dataclass(frozen=True, slots=True)
class _Unit:
    start: int  # where the unit starts in the joined text of the document
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
    text, page_starts, page_numbers = _join_pages(pages)
    units = _split_into_units(text, count_tokens, chunk_size)
    chunks: list[TextChunk] = []
    for group in _pack(units, chunk_size, overlap):
        chunk_text = "".join(unit.text for unit in group).strip()
        first = group[0]
        start = first.start + len(first.text) - len(first.text.lstrip())
        chunks.append(
            TextChunk(
                index=len(chunks),
                text=chunk_text,
                page_number=_page_at(start, page_starts, page_numbers),
                token_count=count_tokens(chunk_text),
            )
        )
    return chunks


def _join_pages(pages: Sequence[Page]) -> tuple[str, list[int], list[int | None]]:
    """All page texts in one string, and where each page starts in it."""
    parts: list[str] = []
    starts: list[int] = []
    numbers: list[int | None] = []
    offset = 0
    for page in pages:
        if not page.text:
            continue
        if parts:
            parts.append(PAGE_SEPARATOR)
            offset += len(PAGE_SEPARATOR)
        starts.append(offset)
        numbers.append(page.number)
        parts.append(page.text)
        offset += len(page.text)
    return "".join(parts), starts, numbers


def _page_at(offset: int, starts: list[int], numbers: list[int | None]) -> int | None:
    position = bisect.bisect_right(starts, offset) - 1
    return numbers[position] if position >= 0 else None


def _split_into_units(text: str, count_tokens: CountTokens, limit: int) -> list[_Unit]:
    units: list[_Unit] = []
    start = 0
    for match in _UNIT_END.finditer(text):
        units.extend(_fit(text[start : match.end()], start, count_tokens, limit))
        start = match.end()
    units.extend(_fit(text[start:], start, count_tokens, limit))
    return [unit for unit in units if unit.text.strip()]


def _fit(text: str, start: int, count_tokens: CountTokens, limit: int) -> list[_Unit]:
    """`text` as one unit if it fits; otherwise cut into words, or into halves."""
    if not text:
        return []
    tokens = count_tokens(text)
    if tokens <= limit:
        return [_Unit(start, text, tokens)]
    words = [word for word in _WORD_END.split(text) if word]
    if len(words) > 1:
        units: list[_Unit] = []
        offset = start
        for word in words:
            units.extend(_fit(word, offset, count_tokens, limit))
            offset += len(word)
        return units
    # One very long "word", like a long URL: cut it in two halves until each part fits.
    middle = len(text) // 2
    return _fit(text[:middle], start, count_tokens, limit) + _fit(
        text[middle:], start + middle, count_tokens, limit
    )


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

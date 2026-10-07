"""Tests for splitting text into chunks. Here a "token" is one word (FakeEmbedder)."""

import itertools
import uuid

from tests.fakes import FakeEmbedder
from worker.chunking import chunk_document, chunk_id
from worker.parsing import Page

count_words = FakeEmbedder().count_tokens


def _sentences(count: int, words_each: int = 10, prefix: str = "s") -> str:
    """`count` sentences of `words_each` words, each word naming its sentence: "s3w1"."""
    return " ".join(
        " ".join(f"{prefix}{n}w{w}" for w in range(words_each - 1)) + f" {prefix}{n}end."
        for n in range(count)
    )


def test_a_short_text_is_one_chunk() -> None:
    [chunk] = chunk_document([Page(None, "Just one short sentence.")], count_words, 50, 10)

    assert chunk.text == "Just one short sentence."
    assert (chunk.index, chunk.page_number, chunk.token_count) == (0, None, 4)


def test_no_text_means_no_chunks() -> None:
    assert chunk_document([Page(1, ""), Page(2, "   ")], count_words, 50, 10) == []


def test_chunks_are_never_longer_than_the_limit() -> None:
    chunks = chunk_document([Page(None, _sentences(40))], count_words, 50, 10)

    assert len(chunks) > 1
    assert all(chunk.token_count <= 50 for chunk in chunks)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_chunks_end_at_sentence_ends() -> None:
    chunks = chunk_document([Page(None, _sentences(40))], count_words, 50, 10)

    assert all(chunk.text.endswith(".") for chunk in chunks)


def test_each_chunk_starts_with_the_end_of_the_one_before() -> None:
    chunks = chunk_document([Page(None, _sentences(40))], count_words, 50, 10)

    for before, after in itertools.pairwise(chunks):
        last_sentence = before.text.rsplit(". ", 1)[-1]
        assert after.text.startswith(last_sentence)  # one 10-word sentence fits the overlap


def test_no_overlap_when_it_is_zero() -> None:
    chunks = chunk_document([Page(None, _sentences(40))], count_words, 50, 0)

    words_in_chunks = sum(chunk.token_count for chunk in chunks)
    assert words_in_chunks == 400  # every word exactly once


def test_every_word_is_in_some_chunk() -> None:
    text = _sentences(40)

    chunks = chunk_document([Page(None, text)], count_words, 50, 10)

    words_in_chunks = {word for chunk in chunks for word in chunk.text.split()}
    assert words_in_chunks == set(text.split())


def test_a_very_long_sentence_is_split_into_words() -> None:
    long_sentence = " ".join(f"word{n}" for n in range(120)) + "."

    chunks = chunk_document([Page(None, long_sentence)], count_words, 50, 0)

    assert [chunk.token_count for chunk in chunks] == [50, 50, 20]


def test_a_very_long_word_is_cut_into_parts() -> None:
    def count_characters(text: str) -> int:  # here a token is one character
        return len(text)

    [first, second, *rest] = chunk_document([Page(None, "x" * 100)], count_characters, 40, 0)

    assert "".join(chunk.text for chunk in [first, second, *rest]) == "x" * 100
    assert all(chunk.token_count <= 40 for chunk in [first, second, *rest])


def test_chunks_know_the_page_where_they_start() -> None:
    pages = [Page(1, _sentences(5, prefix="a")), Page(2, _sentences(5, prefix="b"))]

    chunks = chunk_document(pages, count_words, 30, 0)

    for chunk in chunks:
        first_word = chunk.text.split()[0]
        assert chunk.page_number == (1 if first_word.startswith("a") else 2)
    assert {chunk.page_number for chunk in chunks} == {1, 2}


def test_chunk_ids_are_the_same_every_time() -> None:
    document_id = uuid.uuid4()

    assert chunk_id(document_id, 3) == chunk_id(document_id, 3)
    assert chunk_id(document_id, 3) != chunk_id(document_id, 4)
    assert chunk_id(document_id, 3) != chunk_id(uuid.uuid4(), 3)

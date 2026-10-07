"""Tests for cleaning extracted text."""

import pytest

from worker.cleaning import clean_text


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("ﬁnal ofﬁce", "final office"),  # PDF ligatures
        ("infor-\nmation", "information"),  # a word broken at the end of a line
        ("too    many   spaces\t here", "too many spaces here"),
        ("line one\r\nline two\rline three", "line one\nline two\nline three"),
        ("one\n\n\n\n\ntwo", "one\n\ntwo"),  # at most one empty line
        ("one\x00two\x07three", "one two three"),  # control characters become a space
        ("   \n  trimmed  \n  ", "trimmed"),
    ],
)
def test_clean_text(raw: str, clean: str) -> None:
    assert clean_text(raw) == clean

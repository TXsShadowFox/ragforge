"""Tests for recognizing file types from the name and the first bytes."""

import codecs

import pytest

from shared.file_types import FileType, UnsupportedFileError, clean_filename, detect_file_type
from tests.documents import make_docx, make_pdf


@pytest.mark.parametrize(
    ("filename", "head", "expected"),
    [
        ("rules.pdf", make_pdf(["Hello"])[:2048], FileType.PDF),
        ("RULES.PDF", make_pdf(["Hello"])[:2048], FileType.PDF),
        ("notes.docx", make_docx(["Hello"])[:2048], FileType.DOCX),
        ("page.html", b"<html><body>Hello</body></html>", FileType.HTML),
        ("page.htm", b"<p>Hello</p>", FileType.HTML),
        ("readme.md", b"# Title\n\nHello", FileType.MARKDOWN),
        ("notes.txt", "Hello, wörld".encode(), FileType.TEXT),
        ("windows.txt", codecs.BOM_UTF16_LE + "Hi".encode("utf-16-le"), FileType.TEXT),
    ],
)
def test_accepted_files(filename: str, head: bytes, expected: FileType) -> None:
    assert detect_file_type(filename, head) is expected


@pytest.mark.parametrize(
    ("filename", "head", "message"),
    [
        ("virus.exe", b"MZ\x90\x00", "not supported"),
        ("no-extension", b"Hello", "not supported"),
        ("fake.pdf", b"<html>not a pdf</html>", "not a valid PDF"),
        ("fake.docx", b"%PDF-1.4", "not a valid DOCX"),
        ("image.txt", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "does not look like text"),
    ],
)
def test_rejected_files(filename: str, head: bytes, message: str) -> None:
    with pytest.raises(UnsupportedFileError, match=message):
        detect_file_type(filename, head)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("rules.pdf", "rules.pdf"),
        ("C:\\fakepath\\rules.pdf", "rules.pdf"),  # some browsers send a full path
        ("../../etc/passwd.txt", "passwd.txt"),
        ("", "unnamed"),
        ("a" * 300 + ".pdf", ("a" * 300 + ".pdf")[-255:]),
    ],
)
def test_file_names_are_cleaned(filename: str, expected: str) -> None:
    assert clean_filename(filename) == expected

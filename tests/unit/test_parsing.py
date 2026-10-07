"""Tests for reading text out of PDF, DOCX, HTML, Markdown and text files."""

import codecs
import io
import zipfile

import pytest

from shared.file_types import FileType
from tests.documents import make_docx, make_pdf
from worker.parsing import BadDocumentError, Page, decode_text, parse_document


def test_pdf_text_is_read_page_by_page() -> None:
    pdf = make_pdf(["Attendance rule\nStudents must attend 75%.", "Exams\nThey start in May."])

    pages = parse_document(pdf, FileType.PDF)

    assert [page.number for page in pages] == [1, 2]
    assert "Students must attend 75%." in pages[0].text
    assert "They start in May." in pages[1].text


def test_a_broken_pdf_raises_a_clear_error() -> None:
    broken = make_pdf(["Hello"])[:200]  # cut off: no page data, no cross-reference table

    with pytest.raises(BadDocumentError, match="This PDF is broken"):
        parse_document(broken, FileType.PDF)


def test_not_a_pdf_at_all_raises_a_clear_error() -> None:
    with pytest.raises(BadDocumentError, match="This PDF is broken"):
        parse_document(b"%PDF-1.4 and then nothing useful", FileType.PDF)


def test_docx_paragraphs_and_tables_are_read_in_order() -> None:
    data = make_docx(["Fees", "Pay before the 10th."], table=[["Course", "Fee"], ["CS", "500"]])

    [page] = parse_document(data, FileType.DOCX)

    assert page.number is None
    assert page.text.split("\n") == ["Fees", "Pay before the 10th.", "Course | Fee", "CS | 500"]


def test_a_broken_docx_raises_a_clear_error() -> None:
    not_a_word_file = io.BytesIO()
    with zipfile.ZipFile(not_a_word_file, "w") as archive:
        archive.writestr("hello.txt", "a zip file, but not a Word file")

    with pytest.raises(BadDocumentError, match="This DOCX file is broken"):
        parse_document(not_a_word_file.getvalue(), FileType.DOCX)


def test_html_keeps_the_text_and_drops_scripts_and_styles() -> None:
    html = b"""<html><head><title>Rules</title><style>p {color: red}</style></head>
    <body><h1>Library</h1><p>Open 9 to 5.</p><script>alert('hi')</script></body></html>"""

    [page] = parse_document(html, FileType.HTML)

    assert "Library" in page.text
    assert "Open 9 to 5." in page.text
    assert "alert" not in page.text
    assert "color" not in page.text


@pytest.mark.parametrize("file_type", [FileType.MARKDOWN, FileType.TEXT])
def test_text_files_are_read_as_they_are(file_type: FileType) -> None:
    assert parse_document(b"# Title\n\nSome text.", file_type) == [
        Page(None, "# Title\n\nSome text.")
    ]


@pytest.mark.parametrize(
    "data",
    [
        "café".encode(),  # UTF-8
        codecs.BOM_UTF8 + "café".encode(),  # UTF-8 with a BOM
        codecs.BOM_UTF16_LE + "café".encode("utf-16-le"),  # UTF-16, e.g. from Windows Notepad
        "café".encode("cp1252"),  # old Windows text
    ],
)
def test_text_in_different_encodings_is_decoded(data: bytes) -> None:
    assert decode_text(data) == "café"

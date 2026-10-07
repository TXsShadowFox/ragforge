"""Read the text out of a file: page by page for PDFs, as one block for other formats."""

import codecs
import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

import docx
from bs4 import BeautifulSoup
from docx.table import Table
from pypdf import PasswordType, PdfReader

from shared.file_types import FileType

MAX_UNZIPPED_DOCX_BYTES = 200 * 1024 * 1024  # a DOCX is a zip file: refuse "zip bombs"
# HTML parts that hold no readable text.
_HTML_NOISE = ["script", "style", "noscript", "template", "svg"]


class BadDocumentError(Exception):
    """The file cannot be read: broken, protected, empty, or not what its name says.

    Retrying does not help, so the job fails at once. The message is shown to users.
    """


@dataclass(frozen=True, slots=True)
class Page:
    number: int | None  # 1, 2, 3, ... for PDF pages; None for formats without pages
    text: str


def parse_document(data: bytes, file_type: FileType) -> list[Page]:
    parsers: dict[FileType, Callable[[bytes], list[Page]]] = {
        FileType.PDF: _parse_pdf,
        FileType.DOCX: _parse_docx,
        FileType.HTML: _parse_html,
        FileType.MARKDOWN: _parse_text,
        FileType.TEXT: _parse_text,
    }
    return parsers[file_type](data)


def decode_text(data: bytes) -> str:
    """Text files can be UTF-8 (with or without a BOM), UTF-16 (with a BOM) or old Windows text."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _parse_pdf(data: bytes) -> list[Page]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise BadDocumentError("This PDF is protected with a password.")
        return [
            Page(number, page.extract_text() or "")
            for number, page in enumerate(reader.pages, start=1)
        ]
    except BadDocumentError:
        raise
    except Exception as exc:  # pypdf raises many different errors for broken files
        raise BadDocumentError(f"This PDF is broken and cannot be read ({_name(exc)}).") from exc


def _parse_docx(data: bytes) -> list[Page]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(member.file_size for member in archive.infolist()) > MAX_UNZIPPED_DOCX_BYTES:
                raise BadDocumentError("This DOCX file is too big when unpacked.")
        document = docx.Document(io.BytesIO(data))
        lines: list[str] = []
        for block in document.iter_inner_content():  # paragraphs and tables, in reading order
            if isinstance(block, Table):
                lines.extend(_table_rows(block))
            else:
                lines.append(block.text)
    except BadDocumentError:
        raise
    except Exception as exc:
        raise BadDocumentError(
            f"This DOCX file is broken and cannot be read ({_name(exc)})."
        ) from exc
    return [Page(None, "\n".join(lines))]


def _table_rows(table: Table) -> list[str]:
    """One line per row: "cell | cell | cell". Merged cells appear once."""
    rows = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            text = cell.text.strip()
            if text and (not cells or cells[-1] != text):
                cells.append(text)
        rows.append(" | ".join(cells))
    return rows


def _parse_html(data: bytes) -> list[Page]:
    soup = BeautifulSoup(data, "lxml")  # finds the text encoding by itself
    for tag in soup(_HTML_NOISE):
        tag.decompose()
    return [Page(None, soup.get_text("\n"))]


def _parse_text(data: bytes) -> list[Page]:
    return [Page(None, decode_text(data))]


def _name(exc: Exception) -> str:
    return type(exc).__name__

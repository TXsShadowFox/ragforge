"""The file types we accept, and how we recognize them.

We trust a file's first bytes more than its name: a file called "notes.pdf" must really
start like a PDF.
"""

import codecs
from enum import StrEnum
from pathlib import PurePosixPath


class FileType(StrEnum):
    """Accepted file types. The value is the MIME type we store."""

    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    HTML = "text/html"
    MARKDOWN = "text/markdown"
    TEXT = "text/plain"


EXTENSIONS: dict[str, FileType] = {
    ".pdf": FileType.PDF,
    ".docx": FileType.DOCX,
    ".html": FileType.HTML,
    ".htm": FileType.HTML,
    ".md": FileType.MARKDOWN,
    ".markdown": FileType.MARKDOWN,
    ".txt": FileType.TEXT,
}
TEXT_TYPES = frozenset({FileType.HTML, FileType.MARKDOWN, FileType.TEXT})
SNIFF_BYTES = 2048  # how much of the start of a file we look at
MAX_FILENAME_LENGTH = 255


class UnsupportedFileError(ValueError):
    """The file type is not accepted, or the content does not match the file name."""


def detect_file_type(filename: str, head: bytes) -> FileType:
    """The file type from the name, checked against the first bytes of the content."""
    extension = PurePosixPath(clean_filename(filename)).suffix.lower()
    file_type = EXTENSIONS.get(extension)
    if file_type is None:
        allowed = ", ".join(sorted(EXTENSIONS))
        raise UnsupportedFileError(f"This file type is not supported. Use one of: {allowed}.")
    if file_type is FileType.PDF and b"%PDF-" not in head[:1024]:
        raise UnsupportedFileError("This file is not a valid PDF.")
    if file_type is FileType.DOCX and not head.startswith(b"PK\x03\x04"):
        raise UnsupportedFileError("This file is not a valid DOCX (Word) file.")
    if file_type in TEXT_TYPES and not looks_like_text(head):
        raise UnsupportedFileError("This file does not look like text.")
    return file_type


def looks_like_text(head: bytes) -> bool:
    """Text has no NUL bytes; nearly all binary files do. UTF-16 text (with a BOM) is fine."""
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return True
    return b"\x00" not in head


def clean_filename(filename: str) -> str:
    """Only the file name (browsers sometimes send a full path), at most 255 characters."""
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    return name[-MAX_FILENAME_LENGTH:] or "unnamed"

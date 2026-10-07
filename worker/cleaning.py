"""Clean extracted text: the same characters for the same words, and no layout noise."""

import re
import unicodedata

# A word broken at the end of a line: "infor-\nmation" -> "information".
_HYPHEN_LINE_BREAK = re.compile(r"(\w)-\n(\w)")
# Control characters, except tab and newline.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACES = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)  # e.g. the PDF ligature "ﬁ" becomes "fi"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARACTERS.sub(" ", text)
    text = _HYPHEN_LINE_BREAK.sub(r"\1\2", text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()

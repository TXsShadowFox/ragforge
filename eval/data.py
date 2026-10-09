"""The test set: the sample documents (eval/corpus) and the questions (eval/questions.json).

Each question with an answer names its document and an "evidence" phrase: a short exact
piece of that document's text that holds the answer. A search result is relevant if it
contains the evidence, so the same test works for any chunk size.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.file_types import SNIFF_BYTES, FileType, detect_file_type
from worker.cleaning import clean_text
from worker.parsing import Page, parse_document

EVAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = EVAL_DIR / "corpus"
QUESTIONS_FILE = EVAL_DIR / "questions.json"


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    kind: str  # lookup, paraphrase, exact, near-miss, injection (its document has planted
    # instructions); without an answer: not-in-documents (on topic) or off-topic
    question: str
    answer: str | None = None  # the reference answer, for the judge
    document: str | None = None  # the file that holds the answer
    evidence: str | None = None  # an exact phrase of that file with the answer
    # Text from instructions planted in the document (prompt injection): an answer that
    # contains it followed them.
    forbidden: str | None = None

    @property
    def answerable(self) -> bool:
        return self.evidence is not None


@dataclass(frozen=True, slots=True)
class CorpusFile:
    filename: str
    data: bytes

    @property
    def file_type(self) -> FileType:
        return detect_file_type(self.filename, self.data[:SNIFF_BYTES])

    def pages(self) -> list[Page]:
        """The text, read and cleaned as the worker does it."""
        return [
            Page(page.number, clean_text(page.text))
            for page in parse_document(self.data, self.file_type)
        ]


def normalize(text: str) -> str:
    """Lower case and single spaces: line breaks in a PDF must not hide a match."""
    return " ".join(text.lower().split())


def load_questions(path: Path = QUESTIONS_FILE) -> list[Question]:
    raw: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return [Question(**item) for item in raw]


def load_corpus(directory: Path = CORPUS_DIR) -> list[CorpusFile]:
    return [
        CorpusFile(path.name, path.read_bytes())
        for path in sorted(directory.iterdir())
        if path.is_file()
    ]


def check_test_set(questions: list[Question], corpus: list[CorpusFile]) -> list[str]:
    """Problems in the test set (empty if it is fine): every evidence phrase must be in its
    document, IDs must be unique, and questions without an answer have no evidence."""
    problems: list[str] = []
    texts = {file.filename: normalize(" ".join(p.text for p in file.pages())) for file in corpus}
    ids = [question.id for question in questions]
    if len(set(ids)) != len(ids):
        problems.append("Question IDs are not unique.")
    for question in questions:
        if not question.answerable:
            if question.answer or question.document:
                problems.append(f"{question.id}: has an answer but no evidence.")
            continue
        text = texts.get(question.document or "")
        if text is None:
            problems.append(f"{question.id}: no document named {question.document!r}.")
        elif normalize(question.evidence or "") not in text:
            problems.append(f"{question.id}: the evidence is not in {question.document}.")
        elif question.forbidden and normalize(question.forbidden) not in text:
            problems.append(f"{question.id}: the planted text is not in {question.document}.")
    return problems

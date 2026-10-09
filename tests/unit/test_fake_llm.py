"""The fake LLM of the load tests and the CI browser test: its answers, and that our real
LLM client understands it (whole answers and streamed ones)."""

import dataclasses
import uuid

import httpx
import pytest

from api.chat.prompts import NO_ANSWER, answer_messages, rewrite_messages
from api.chat.retrieval import Source
from loadtests import fake_llm
from shared.config import Settings
from shared.llm import ChatMessage, OpenAICompatibleLLM, Usage

RULES = Source(
    chunk_id=uuid.uuid4(),
    document_id=uuid.uuid4(),
    filename="college-rules.txt",
    page_number=None,
    text="Library rules. Books can be borrowed for two weeks. A late fee of 5 rupees per day is\n"
    "charged for each book returned after the due date.",
)
HOSTEL = dataclasses.replace(RULES, filename="hostel.txt", text="The hostel gates close at 10 pm.")


def _openai(messages: list[ChatMessage]) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in messages]


@pytest.fixture(autouse=True)
def _no_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fake_llm, "FIRST_TOKEN_SECONDS", 0.0)
    monkeypatch.setattr(fake_llm, "TOKENS_PER_SECOND", 1_000_000.0)


def test_it_answers_with_the_best_matching_sentence_and_its_source_number() -> None:
    messages = answer_messages("When do the hostel gates close?", [RULES, HOSTEL])

    reply = fake_llm.reply_to(_openai(messages))

    assert reply.text == "The hostel gates close at 10 pm. [2]"
    assert reply.prompt_tokens > 0
    assert "".join(reply.pieces) == reply.text


def test_a_sentence_split_over_lines_is_found_whole() -> None:
    messages = answer_messages("What is the late fee for library books?", [RULES])

    reply = fake_llm.reply_to(_openai(messages))

    assert reply.text == (
        "A late fee of 5 rupees per day is charged for each book returned after the due date. [1]"
    )


def test_without_a_matching_sentence_it_does_not_know() -> None:
    messages = answer_messages("Who is the principal?", [HOSTEL])

    assert fake_llm.reply_to(_openai(messages)).text == NO_ANSWER


def test_a_rewrite_request_gets_the_last_question_back() -> None:
    history = [ChatMessage("user", "What is the fee?"), ChatMessage("assistant", "500 [1].")]

    reply = fake_llm.reply_to(_openai(rewrite_messages("And for hostels?", history)))

    assert reply.text == "And for hostels?"


@pytest.fixture
def client(settings: Settings) -> OpenAICompatibleLLM:
    """Our real LLM client, talking to the fake LLM app in memory."""
    return OpenAICompatibleLLM(
        settings.model_copy(update={"llm_base_url": "http://fake-llm/v1"}),
        transport=httpx.ASGITransport(app=fake_llm.app),
    )


async def test_our_client_reads_a_whole_answer(client: OpenAICompatibleLLM) -> None:
    usage = Usage()

    answer = await client.complete(answer_messages("When do the gates close?", [HOSTEL]), usage)

    assert answer == "The hostel gates close at 10 pm. [1]"
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens == 8  # one "token" per word


async def test_our_client_reads_a_streamed_answer(client: OpenAICompatibleLLM) -> None:
    usage = Usage()
    messages = answer_messages("When do the gates close?", [HOSTEL])

    pieces = [piece async for piece in client.stream(messages, usage)]

    assert "".join(pieces) == "The hostel gates close at 10 pm. [1]"
    assert len(pieces) == 8
    assert usage.completion_tokens == 8

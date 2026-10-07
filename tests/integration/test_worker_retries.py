"""Retries and the dead-letter queue, with an embedder that fails on purpose.

The test settings retry twice, after 1 second each time.
"""

import json

import aio_pika
import pytest
from httpx import AsyncClient

from shared.config import Settings
from shared.jobs import ATTEMPT_HEADER, DEAD_QUEUE, ERROR_HEADER
from tests.fakes import FailingEmbedder
from tests.integration.helpers import running_worker, sign_up, upload, wait_for_status

pytestmark = pytest.mark.integration

TEXT = b"The canteen opens at 9 am and closes at 5 pm."


async def test_a_temporary_error_is_retried_and_the_job_then_succeeds(
    docs_api: AsyncClient, clean_stack: Settings
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    embedder = FailingEmbedder(failures=1)

    async with running_worker(clean_stack, embedder):
        response = await upload(docs_api, owner, "canteen.txt", TEXT)
        document = await wait_for_status(docs_api, owner, response.json()["document"]["id"])

    assert document["status"] == "ready"
    assert embedder.calls == 2  # failed once, then worked on the retry


async def test_a_job_that_keeps_failing_ends_in_the_dead_letter_queue(
    docs_api: AsyncClient, clean_stack: Settings
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    embedder = FailingEmbedder(failures=100)

    async with running_worker(clean_stack, embedder):
        response = await upload(docs_api, owner, "canteen.txt", TEXT)
        document = await wait_for_status(docs_api, owner, response.json()["document"]["id"])

    assert document["status"] == "failed"
    assert document["error"] == "Processing failed 3 times. Please try again later."
    assert embedder.calls == 3  # the first try and 2 retries
    connection = await aio_pika.connect(str(clean_stack.rabbitmq_url))
    async with connection:
        dead_queue = await (await connection.channel()).get_queue(DEAD_QUEUE)
        dead = await dead_queue.get(fail=False)
    assert dead is not None
    assert json.loads(dead.body)["document_id"] == document["id"]
    assert dead.headers[ATTEMPT_HEADER] == 3
    assert "the embedding service is down" in str(dead.headers[ERROR_HEADER])

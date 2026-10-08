"""Start real services in Docker for the integration tests (testcontainers).

Each container starts once per test run and is removed at the end.
Image versions are read from docker-compose.yml, so tests and dev use the same ones.
If Docker is not running, these tests are skipped on a laptop but fail in CI.
"""

import os
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import aio_pika
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.qdrant import QdrantContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.docker_client import DockerClient
from testcontainers.core.wait_strategies import HttpWaitStrategy, LogMessageWaitStrategy

from api.main import create_app
from shared.clients import Clients
from shared.clients.postgres import create_engine
from shared.config import Settings
from shared.db.migrate import upgrade
from shared.db.models import Base
from shared.init import init_stores
from shared.jobs import DEAD_QUEUE, JOBS_QUEUE, retry_queue
from shared.vector_store import ensure_collection
from tests.fakes import FakeEmbedder, fake_ai
from tests.integration.helpers import running_worker

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
STARTUP_TIMEOUT_SECONDS = 120

RABBITMQ_USER = "test"
RABBITMQ_PASSWORD = "test-password"
STORAGE_ACCESS_KEY = "test-access"
STORAGE_SECRET_KEY = "test-secret-key"
JWT_SECRET = "integration-test-jwt-secret-of-32-chars-or-more"
TEST_COLLECTION = "chunks_test"


def compose_image(name: str) -> str:
    """The image that docker-compose.yml uses for `name`, e.g. "postgres:18.6-alpine"."""
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    images: list[str] = re.findall(r"^\s*image:\s*(\S+)\s*$", text, flags=re.MULTILINE)
    for image in images:
        repository = image.rsplit(":", 1)[0]
        if repository == name or repository.endswith(f"/{name}"):
            return image
    raise LookupError(f"No image for {name!r} in {COMPOSE_FILE.name}")


def _docker_is_running() -> bool:
    try:
        DockerClient()  # raises if it cannot talk to Docker
    except Exception:
        return False
    return True


@pytest.fixture(scope="session")
def docker_available() -> None:
    if _docker_is_running():
        return
    if os.getenv("CI"):
        pytest.fail("Docker is required for the integration tests in CI.")
    pytest.skip("Docker is not running. Start Docker Desktop to run the integration tests.")


@pytest.fixture(scope="session")
def postgres_url(docker_available: None) -> Iterator[str]:
    with PostgresContainer(compose_image("postgres"), driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def redis_url(docker_available: None) -> Iterator[str]:
    with RedisContainer(compose_image("redis")) as container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def rabbitmq_url(docker_available: None) -> Iterator[str]:
    container = (
        DockerContainer(compose_image("rabbitmq"))
        .with_env("RABBITMQ_DEFAULT_USER", RABBITMQ_USER)
        .with_env("RABBITMQ_DEFAULT_PASS", RABBITMQ_PASSWORD)
        .with_exposed_ports(5672)
        .waiting_for(
            LogMessageWaitStrategy("Server startup complete").with_startup_timeout(
                STARTUP_TIMEOUT_SECONDS
            )
        )
    )
    with container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(5672)
        yield f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}@{host}:{port}/"


@pytest.fixture(scope="session")
def qdrant_url(docker_available: None) -> Iterator[str]:
    with QdrantContainer(compose_image("qdrant")) as container:
        yield f"http://{container.rest_host_address}"


@pytest.fixture(scope="session")
def storage_url(docker_available: None) -> Iterator[str]:
    container = (
        DockerContainer(compose_image("rustfs"))
        .with_env("RUSTFS_ACCESS_KEY", STORAGE_ACCESS_KEY)
        .with_env("RUSTFS_SECRET_KEY", STORAGE_SECRET_KEY)
        .with_exposed_ports(9000)
        .waiting_for(
            HttpWaitStrategy(9000, "/health")
            .for_status_code(200)
            .with_startup_timeout(STARTUP_TIMEOUT_SECONDS)
        )
    )
    with container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(9000)
        yield f"http://{host}:{port}"


@pytest.fixture(scope="session")
def live_settings(
    postgres_url: str, redis_url: str, rabbitmq_url: str, qdrant_url: str, storage_url: str
) -> Settings:
    """Settings that point at the real containers."""
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=postgres_url,
        redis_url=redis_url,
        rabbitmq_url=rabbitmq_url,
        qdrant_url=qdrant_url,
        s3_endpoint_url=storage_url,
        s3_access_key=STORAGE_ACCESS_KEY,
        s3_secret_key=STORAGE_SECRET_KEY,
        jwt_secret=JWT_SECRET,
    )


@pytest.fixture(scope="session")
def db_settings(postgres_url: str) -> Settings:
    """A real Postgres. The other services get addresses that these tests never use."""
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=postgres_url,
        redis_url="redis://localhost:6379/0",
        rabbitmq_url="amqp://unused:unused@localhost:5672/",
        qdrant_url="http://localhost:6333",
        s3_endpoint_url="http://localhost:9000",
        s3_access_key="unused",
        s3_secret_key="unused",
        jwt_secret=JWT_SECRET,
    )


@pytest.fixture(scope="session")
async def db_engine(db_settings: Settings) -> AsyncIterator[AsyncEngine]:
    """The test database, migrated to the newest version once per test run."""
    engine = create_engine(db_settings)
    await upgrade(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def api(db_settings: Settings, db_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """An HTTP client for the app (with its startup hooks), on an empty database."""
    await empty_all_tables(db_engine)
    app = create_app(db_settings, ai=fake_ai())
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


async def empty_all_tables(engine: AsyncEngine) -> None:
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        # Safe: the table names come from our own models, not from users.
        await connection.execute(text(f"TRUNCATE {tables} CASCADE"))


# --------------------------------------------------- the whole stack (Phase 2) ----


@pytest.fixture(scope="session")
def stack_settings(
    postgres_url: str, rabbitmq_url: str, qdrant_url: str, storage_url: str
) -> Settings:
    """Real Postgres, RabbitMQ, Qdrant and storage (Redis is not used yet).

    Short retry delays and a fast outbox poll, so the tests do not wait long. With the
    fake embedder a "token" is a word, so chunks here are 100 words.
    """
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=postgres_url,
        redis_url="redis://localhost:6379/0",
        rabbitmq_url=rabbitmq_url,
        qdrant_url=qdrant_url,
        qdrant_collection=TEST_COLLECTION,
        s3_endpoint_url=storage_url,
        s3_access_key=STORAGE_ACCESS_KEY,
        s3_secret_key=STORAGE_SECRET_KEY,
        jwt_secret=JWT_SECRET,
        chunk_size_tokens=100,
        chunk_overlap_tokens=10,
        ingest_retry_delays_seconds=[1, 1],
        outbox_poll_seconds=0.1,
    )


@pytest.fixture(scope="session")
async def stack(stack_settings: Settings, db_engine: AsyncEngine) -> Settings:
    """Prepare every store once, like the `init` container does."""
    await init_stores(stack_settings, vector_dimension=FakeEmbedder.dimension)
    return stack_settings


@pytest.fixture
async def clean_stack(stack: Settings, db_engine: AsyncEngine) -> AsyncIterator[Settings]:
    """Every store empty at the start of the test: tables, queues and vectors."""
    await empty_all_tables(db_engine)
    connection = await aio_pika.connect(str(stack.rabbitmq_url))
    async with connection:
        channel = await connection.channel()
        for name in [JOBS_QUEUE, DEAD_QUEUE, *map(retry_queue, stack.ingest_retry_delays_seconds)]:
            await (await channel.get_queue(name)).purge()
    clients = Clients.create(stack)
    try:
        await clients.qdrant.delete_collection(stack.qdrant_collection)
        await ensure_collection(clients.qdrant, stack.qdrant_collection, FakeEmbedder.dimension)
        yield stack
    finally:
        await clients.aclose()


@pytest.fixture
async def stack_clients(clean_stack: Settings) -> AsyncIterator[Clients]:
    """Clients for looking into the stores from a test."""
    clients = Clients.create(clean_stack)
    try:
        yield clients
    finally:
        await clients.aclose()


@pytest.fixture
async def docs_api(clean_stack: Settings) -> AsyncIterator[AsyncClient]:
    """An HTTP client for the app, using the whole stack."""
    app = create_app(clean_stack, ai=fake_ai())
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


@pytest.fixture
async def worker(clean_stack: Settings) -> AsyncIterator[None]:
    """A worker running in the background, with the fake embedder."""
    async with running_worker(clean_stack):
        yield

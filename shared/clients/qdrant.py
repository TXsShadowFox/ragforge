"""Qdrant connection: the vector database."""

from qdrant_client import AsyncQdrantClient

from shared.config import Settings


def create_qdrant(settings: Settings) -> AsyncQdrantClient:
    """Create the client without any network call.

    `check_compatibility=False` skips a blocking version request at startup.
    We pin the server image and the client to the same minor version instead.
    """
    api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    return AsyncQdrantClient(
        url=settings.qdrant_url, api_key=api_key, timeout=10, check_compatibility=False
    )


async def ping(client: AsyncQdrantClient) -> None:
    """List collections. Raises if Qdrant cannot be reached."""
    await client.get_collections()

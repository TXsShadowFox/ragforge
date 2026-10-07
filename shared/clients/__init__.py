"""Connections to the services RAGForge depends on.

`Clients` holds one long-lived client per service. A process (the API now,
the worker from Phase 2) creates it once at startup and closes it at shutdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shared.clients.postgres import create_engine, create_session_factory
from shared.clients.qdrant import create_qdrant
from shared.clients.redis import create_redis
from shared.clients.storage import create_s3_client
from shared.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from qdrant_client import AsyncQdrantClient
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


@dataclass(slots=True)
class Clients:
    """One client per service, shared by the whole process."""

    db: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]  # ORM sessions that use `db`
    redis: Redis
    qdrant: AsyncQdrantClient
    s3: S3Client

    @classmethod
    def create(cls, settings: Settings) -> Clients:
        """Create every client. Nothing connects yet, so this never fails on network errors."""
        db = create_engine(settings)
        return cls(
            db=db,
            sessions=create_session_factory(db),
            redis=create_redis(settings),
            qdrant=create_qdrant(settings),
            s3=create_s3_client(settings),
        )

    async def aclose(self) -> None:
        """Close every connection pool."""
        await self.db.dispose()
        await self.redis.aclose()
        await self.qdrant.close()
        self.s3.close()

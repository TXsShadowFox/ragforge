"""PostgreSQL connection: SQLAlchemy async engine with the asyncpg driver."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from shared.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the connection pool. It only connects when first used."""
    return create_async_engine(str(settings.database_url), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Make ORM sessions. `expire_on_commit=False`: objects stay readable after a commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> None:
    """Run `SELECT 1`. Raises if Postgres cannot be reached."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

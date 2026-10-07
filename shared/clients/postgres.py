"""PostgreSQL connection: SQLAlchemy async engine with the asyncpg driver."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shared.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the connection pool. It only connects when first used."""
    return create_async_engine(str(settings.database_url), pool_pre_ping=True)


async def ping(engine: AsyncEngine) -> None:
    """Run `SELECT 1`. Raises if Postgres cannot be reached."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

"""Run the database migrations (Alembic) from Python.

`python -m shared.db.migrate` brings the database in DATABASE_URL up to the newest version.
`make migrate`, `make dev` and the Docker `migrate` service run it; the tests use `upgrade()`.
"""

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from shared.clients.postgres import create_engine
from shared.config import get_settings
from shared.logging import configure_logging

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

logger = logging.getLogger(__name__)


def alembic_config() -> Config:
    """Alembic settings made in code, so no alembic.ini is needed (e.g. inside Docker)."""
    config = Config()
    # Config values use %-interpolation, so a literal % must be written as %%.
    config.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    return config


async def upgrade(engine: AsyncEngine, revision: str = "head") -> None:
    """Upgrade the database to `revision` (default: the newest), in one transaction."""
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade, revision)


async def downgrade(engine: AsyncEngine, revision: str) -> None:
    """Downgrade the database to `revision` ("base" removes everything), in one transaction."""
    async with engine.begin() as connection:
        await connection.run_sync(_downgrade, revision)


def _upgrade(connection: Connection, revision: str) -> None:
    command.upgrade(_config_with(connection), revision)


def _downgrade(connection: Connection, revision: str) -> None:
    command.downgrade(_config_with(connection), revision)


def _config_with(connection: Connection) -> Config:
    """Give migrations/env.py our open connection, so it does not make its own."""
    config = alembic_config()
    config.attributes["connection"] = connection
    return config


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    try:
        await upgrade(engine)
    finally:
        await engine.dispose()
    logger.info("The database is up to date")


if __name__ == "__main__":
    asyncio.run(main())

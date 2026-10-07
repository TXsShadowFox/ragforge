"""The migrations on a real Postgres: down, up, and in sync with the models."""

from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from shared.db.migrate import downgrade, upgrade
from shared.db.models import Base

pytestmark = pytest.mark.integration

APP_TABLES = {"tenants", "users", "api_keys"}


def _table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def _differences(connection: Connection) -> list[Any]:
    differences: list[Any] = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    return differences


async def test_downgrade_removes_the_tables_and_upgrade_brings_them_back(
    db_engine: AsyncEngine,
) -> None:
    try:
        await downgrade(db_engine, "base")
        async with db_engine.connect() as connection:
            assert await connection.run_sync(_table_names) == {"alembic_version"}
    finally:
        await upgrade(db_engine)  # leave the database ready for the other tests

    async with db_engine.connect() as connection:
        assert APP_TABLES.issubset(await connection.run_sync(_table_names))


async def test_the_migrations_match_the_models(db_engine: AsyncEngine) -> None:
    # If this fails, a model changed without a migration (see CLAUDE.md, "Database migrations").
    async with db_engine.connect() as connection:
        differences = await connection.run_sync(_differences)

    assert differences == []

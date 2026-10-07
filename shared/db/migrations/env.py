"""Alembic environment: connects the migrations to the database.

- `shared.db.migrate` (the app, Docker, tests) passes an open connection in
  `config.attributes["connection"]`.
- The `alembic` command line (e.g. `alembic revision --autogenerate`) passes none, so we
  connect to DATABASE_URL from the settings.
"""

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from shared.config import get_settings
from shared.db.models import Base


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_with_new_connection() -> None:
    engine = create_async_engine(str(get_settings().database_url), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) mode is not supported: run migrations on a database.")

shared_connection = context.config.attributes.get("connection")
if shared_connection is None:
    asyncio.run(run_migrations_with_new_connection())
else:
    run_migrations(shared_connection)

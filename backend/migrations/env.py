"""Alembic environment — async-aware, url taken from REGISTRY_DATABASE_URL."""
import asyncio
import os
import sys

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402
from app.db import Base              # noqa: E402
from app import models               # noqa: E402,F401  (registers all tables)

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=True)  # batch mode: ALTERs work on SQLite too
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    engine = async_engine_from_config(config.get_section(config.config_ini_section),
                                      prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

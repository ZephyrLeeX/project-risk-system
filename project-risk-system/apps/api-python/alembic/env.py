"""Alembic environment for the single PostgreSQL schema head."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from risk_platform.db import database_url
from risk_platform.models import metadata

config = context.config
if config.config_file_name is not None:
    # Alembic may run inside pytest or the application process; do not disable
    # security/application loggers that were configured by the caller.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        configure(supplied_connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

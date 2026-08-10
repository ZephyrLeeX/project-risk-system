from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from risk_platform.db import (
    DatabaseConfigurationError,
    create_database_engine,
    database_url,
    dispose_database_engine,
    transaction,
)


def test_database_url_accepts_only_psycopg_postgresql() -> None:
    assert database_url({"DATABASE_URL": "postgresql://u:p@db/app"}) == (
        "postgresql+psycopg://u:p@db/app"
    )
    with pytest.raises(DatabaseConfigurationError, match="PostgreSQL"):
        database_url({"DATABASE_URL": "sqlite:///tmp.db"})
    with pytest.raises(DatabaseConfigurationError, match="PostgreSQL"):
        database_url({})


def test_engine_rejects_non_postgresql_url() -> None:
    with pytest.raises(DatabaseConfigurationError, match="PostgreSQL"):
        create_database_engine("sqlite+aiosqlite://")


def test_transaction_commits_and_closes() -> None:
    session = AsyncMock(spec=AsyncSession)
    begin_context = AsyncMock()
    begin_context.__aenter__.return_value = None
    begin_context.__aexit__.return_value = None
    session.begin = MagicMock(return_value=begin_context)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    factory = MagicMock(spec=async_sessionmaker, return_value=session_context)

    async def run() -> None:
        async with transaction(factory) as yielded:
            assert yielded is session

    asyncio.run(run())
    begin_context.__aexit__.assert_awaited_once()
    session.rollback.assert_not_awaited()


def test_transaction_rolls_back_on_error() -> None:
    session = AsyncMock(spec=AsyncSession)
    begin_context = AsyncMock()
    begin_context.__aenter__.return_value = None
    begin_context.__aexit__.return_value = None
    session.begin = MagicMock(return_value=begin_context)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    factory = MagicMock(spec=async_sessionmaker, return_value=session_context)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            async with transaction(factory):
                raise RuntimeError("boom")

    asyncio.run(run())
    session.rollback.assert_awaited_once()


def test_engine_disposal() -> None:
    engine = AsyncMock(spec=AsyncEngine)
    asyncio.run(dispose_database_engine(engine))
    engine.dispose.assert_awaited_once()

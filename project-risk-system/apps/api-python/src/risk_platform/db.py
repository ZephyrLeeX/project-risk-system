"""Shared asynchronous PostgreSQL session and transaction infrastructure."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseConfigurationError(RuntimeError):
    """Raised without leaking credentials when DATABASE_URL is unusable."""


def database_url(environ: dict[str, str] | None = None) -> str:
    """Return a psycopg-backed PostgreSQL URL; PostgreSQL is the only supported DB."""

    source = os.environ if environ is None else environ
    value = source.get("DATABASE_URL", "")
    if value.startswith("postgresql://") or value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.split("://", 1)[1]
    if value.startswith("postgresql+psycopg://"):
        return value
    raise DatabaseConfigurationError("DATABASE_URL 必须使用 PostgreSQL psycopg 驱动")


def create_database_engine(url: str, *, pool_pre_ping: bool = True) -> AsyncEngine:
    """Create an async engine without connecting or creating schema at startup."""

    if not url.startswith("postgresql+psycopg://"):
        raise DatabaseConfigurationError("数据库驱动必须是 PostgreSQL psycopg")
    return create_async_engine(url, pool_pre_ping=pool_pre_ping)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create request/worker-safe sessions with explicit transaction ownership."""

    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Commit one unit of work or roll it back atomically on failure."""

    async with session_factory() as session:
        try:
            async with session.begin():
                yield session
        except BaseException:
            await session.rollback()
            raise


async def dispose_database_engine(engine: AsyncEngine) -> None:
    """Dispose process-owned pools during request or worker shutdown."""

    await engine.dispose()


__all__ = [
    "DatabaseConfigurationError",
    "create_database_engine",
    "create_session_factory",
    "database_url",
    "dispose_database_engine",
    "transaction",
]

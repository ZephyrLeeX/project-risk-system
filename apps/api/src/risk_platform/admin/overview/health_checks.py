"""Real, bounded runtime dependency checks used by the overview service."""

from __future__ import annotations

import asyncio
import os

import redis.asyncio as redis

from risk_platform.admin.overview.service import OverviewDependencyFailure
from risk_platform.reliability.celery_app import celery_app


async def redis_ping() -> None:
    """Issue Redis PING without retaining it as an application data store."""

    client = redis.from_url(  # type: ignore[no-untyped-call]
        os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    )
    try:
        if not await client.ping():
            raise OverviewDependencyFailure("UNREACHABLE")
    except OverviewDependencyFailure:
        raise
    except Exception as error:
        raise OverviewDependencyFailure("UNREACHABLE") from error
    finally:
        await client.aclose()


async def worker_ping() -> None:
    """Use Celery's control plane; at least one replying worker is required."""

    try:
        replies = await asyncio.to_thread(celery_app.control.inspect().ping)
    except Exception as error:
        raise OverviewDependencyFailure("NO_ACTIVE_WORKER") from error
    if not replies:
        raise OverviewDependencyFailure("NO_ACTIVE_WORKER")


__all__ = ["redis_ping", "worker_ping"]

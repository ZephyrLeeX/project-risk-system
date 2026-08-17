"""Production Celery worker composition entrypoint (owned by T040)."""

from __future__ import annotations

from risk_platform.composition import (
    CompositionError,
    build_ai_provider_client,
    build_tool_registry,
    import_storage_root,
    load_cipher,
    merge_worker_handlers,
)
from risk_platform.config import Settings
from risk_platform.db import create_database_engine, create_session_factory, database_url
from risk_platform.reliability.celery_app import celery_app
from risk_platform.reliability.dispatcher import register_executor

_registered = False


def register_production_worker(*, owner: str = "risk-platform-worker") -> None:
    """Register the shared executor exactly once per worker process.

    A missing or invalid encryption key fails worker startup explicitly; the
    worker must not consume messages without the required secret boundary.
    """

    global _registered
    if _registered:
        return
    sessions = create_session_factory(create_database_engine(database_url()))
    cipher = load_cipher()
    if cipher is None:
        raise CompositionError("DATA_ENCRYPTION_KEY 未配置或无效，无法启动 worker")  # noqa: RUF001
    settings = Settings.from_env()
    tool_registry = build_tool_registry(sessions)
    handlers = merge_worker_handlers(
        sessions,
        cipher,
        import_storage_root(),
        tool_registry,
        build_ai_provider_client(settings),
    )
    register_executor(celery_app, sessions, handlers, owner=owner)
    _registered = True


# Eager registration so ``celery -A risk_platform.worker worker`` discovers the executor.
register_production_worker()

__all__ = ["celery_app", "register_production_worker"]

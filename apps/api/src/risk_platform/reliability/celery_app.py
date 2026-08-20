"""Celery process configuration. Redis carries messages, never task facts."""

from __future__ import annotations

import os

from celery import Celery


def create_celery_app() -> Celery:
    app = Celery("risk_platform")
    app.conf.update(
        broker_url=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        result_backend=None,
        task_serializer="json",
        accept_content=["json"],
        task_time_limit=int(os.environ.get("CELERY_TASK_TIME_LIMIT", "900")),
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
    )
    return app


celery_app = create_celery_app()

__all__ = ["celery_app", "create_celery_app"]

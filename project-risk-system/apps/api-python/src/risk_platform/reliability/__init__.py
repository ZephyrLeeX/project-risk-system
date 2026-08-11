"""Durable Celery task infrastructure."""

from risk_platform.reliability.celery_app import celery_app

__all__ = ["celery_app"]

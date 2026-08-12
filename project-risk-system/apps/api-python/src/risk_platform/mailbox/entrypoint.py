"""Mailbox module router entry points for final application composition."""

from __future__ import annotations

from fastapi import APIRouter

from risk_platform.mailbox.api import candidate_router, router


def routers() -> tuple[APIRouter, APIRouter]:
    """Expose each mailbox-owned router exactly once to the composition root."""

    return (router, candidate_router)


__all__ = ["routers"]

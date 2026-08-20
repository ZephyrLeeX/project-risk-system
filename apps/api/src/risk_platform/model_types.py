"""Strict shared runtime types and Python-side ORM defaults."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]


def new_uuid() -> UUID:
    """Return a UUID for Prisma ``@default(uuid())`` columns before flush."""

    return uuid4()


def utc_now() -> datetime:
    """Return an aware UTC timestamp for Prisma ``@updatedAt`` columns."""

    return datetime.now(UTC)


__all__ = ["JSONScalar", "JSONValue", "new_uuid", "utc_now"]

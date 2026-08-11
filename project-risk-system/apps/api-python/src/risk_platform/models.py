"""ORM metadata aggregator; model definitions remain within domain modules."""

from importlib import import_module

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "%(table_name)s_%(column_0_N_name)s_idx",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s",
    "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}


class Base(DeclarativeBase):
    """Base for all modular-monolith ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


metadata = Base.metadata

_MODEL_MODULES = (
    "agent",
    "admin",
    "ai_providers",
    "audit",
    "auth",
    "imports",
    "mailbox",
    "projects",
    "rbac",
    "reliability",
    "risks",
    "system_config",
    "timeline",
    "todos",
    "weekly_reports",
)
for _module in _MODEL_MODULES:
    import_module(f"risk_platform.{_module}.models")

__all__ = ["NAMING_CONVENTION", "Base", "metadata"]

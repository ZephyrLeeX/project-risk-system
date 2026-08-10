"""Generated SQLAlchemy models for this bounded domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.models import Base

UUIDType = PG_UUID


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        Index("departments_parentId_idx", "parentId"),
        Index("departments_code_key", "code", unique=True),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parentId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    sortOrder: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    LOCKED = "LOCKED"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("users_departmentId_idx", "departmentId"),
        Index("users_status_idx", "status"),
        Index("users_username_lower_key", text('lower("username")'), unique=True),
        Index("users_username_key", "username", unique=True),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    passwordHash: Mapped[str] = mapped_column(String(255), nullable=False)
    displayName: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="UserStatus", native_enum=True),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )
    mustChangePassword: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    failedLoginCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    lockedUntil: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    departmentId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("departments.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )
    passwordChangedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    lastLoginAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )

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
    String,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.models import Base

UUIDType = PG_UUID


class DataScopeType(StrEnum):
    ALL = "ALL"
    OWNED = "OWNED"
    ASSIGNED = "ASSIGNED"
    OWNED_OR_ASSIGNED = "OWNED_OR_ASSIGNED"
    NONE = "NONE"


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        Index("roles_enabled_idx", "enabled"),
        Index("roles_code_key", "code", unique=True),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    isSystem: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    defaultDataScope: Mapped[DataScopeType] = mapped_column(
        Enum(DataScopeType, name="DataScopeType", native_enum=True),
        nullable=False,
        server_default=text("'ASSIGNED'"),
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (
        Index("permissions_module_idx", "module"),
        Index("permissions_code_key", "code", unique=True),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (Index("user_roles_roleId_idx", "roleId"),)
    userId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    roleId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT", onupdate="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    dataScope: Mapped[DataScopeType] = mapped_column(
        Enum(DataScopeType, name="DataScopeType", native_enum=True), nullable=False
    )
    assignedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (Index("role_permissions_permissionId_idx", "permissionId"),)
    roleId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    permissionId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("permissions.id", ondelete="RESTRICT", onupdate="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    grantedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ProjectScopeSource(StrEnum):
    ADMIN = "ADMIN"
    IMPORT = "IMPORT"


class UserProjectScope(Base):
    __tablename__ = "user_project_scopes"
    __table_args__ = (
        Index("user_project_scopes_userId_idx", "userId"),
        Index("user_project_scopes_assignedBy_idx", "assignedBy"),
    )
    projectId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    userId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    assignedBy: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    scopeSource: Mapped[ProjectScopeSource] = mapped_column(
        Enum(ProjectScopeSource, name="ProjectScopeSource", native_enum=True),
        nullable=False,
        server_default=text("'ADMIN'"),
    )
    assignedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

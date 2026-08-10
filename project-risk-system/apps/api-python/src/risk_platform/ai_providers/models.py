"""Generated SQLAlchemy models for this bounded domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.models import Base

UUIDType = PG_UUID


class AiConnectionStatus(StrEnum):
    UNTESTED = "UNTESTED"
    HEALTHY = "HEALTHY"
    FAILED = "FAILED"


class AiProviderConfig(Base):
    __tablename__ = "ai_provider_configs"
    __table_args__ = (
        Index("ai_provider_configs_enabled_isDefault_idx", "enabled", "isDefault"),
        Index("ai_provider_configs_lastTestStatus_lastTestAt_idx", "lastTestStatus", "lastTestAt"),
        Index("ai_provider_configs_expiresAt_idx", "expiresAt"),
        Index("ai_provider_configs_name_key", "name", unique=True),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    vendor: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    encryptedApiKey: Mapped[str] = mapped_column(Text, nullable=False)
    keyIv: Mapped[str] = mapped_column(String(64), nullable=False)
    keyAuthTag: Mapped[str] = mapped_column(String(64), nullable=False)
    keyLast4: Mapped[str] = mapped_column(String(16), nullable=False)
    expiresAt: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    timeoutSeconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("60"))
    retryCount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    isDefault: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    lastTestStatus: Mapped[AiConnectionStatus] = mapped_column(
        Enum(AiConnectionStatus, name="AiConnectionStatus", native_enum=True),
        nullable=False,
        server_default=text("'UNTESTED'"),
    )
    lastTestAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    lastTestLatencyMs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lastTestErrorCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    createdById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    updatedById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
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


class AiCallResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class AiCallScene(StrEnum):
    WEEKLY_REPORT = "WEEKLY_REPORT"
    AGENT_QUERY = "AGENT_QUERY"
    RISK_EXTRACTION = "RISK_EXTRACTION"
    CONNECTION_TEST = "CONNECTION_TEST"


class AiCallLog(Base):
    __tablename__ = "ai_call_logs"
    __table_args__ = (
        Index("ai_call_logs_providerId_createdAt_idx", "providerId", "createdAt"),
        Index("ai_call_logs_scene_createdAt_idx", "scene", "createdAt"),
        Index("ai_call_logs_result_createdAt_idx", "result", "createdAt"),
        Index("ai_call_logs_actorUserId_createdAt_idx", "actorUserId", "createdAt"),
        Index("ai_call_logs_traceId_key", "traceId", unique=True),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    traceId: Mapped[str] = mapped_column(String(64), nullable=False)
    providerId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("ai_provider_configs.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    providerNameSnapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    modelSnapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    scene: Mapped[AiCallScene] = mapped_column(
        Enum(AiCallScene, name="AiCallScene", native_enum=True), nullable=False
    )
    inputTokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    outputTokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    totalTokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    durationMs: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[AiCallResult] = mapped_column(
        Enum(AiCallResult, name="AiCallResult", native_enum=True), nullable=False
    )
    errorCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    errorSummary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actorUserId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

"""Materialized weekly-report aggregate persistence contract."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Date, Enum, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.model_types import JSONValue, new_uuid, utc_now
from risk_platform.models import Base
from risk_platform.risks.models import ProjectRiskLevel, RiskStatus
from risk_platform.todos.models import ActionItemStatus

UUIDType = PG_UUID


class WeeklyReportAggregate(Base):
    __tablename__ = "weekly_report_aggregates"
    __table_args__ = (
        Index(
            "weekly_report_aggregates_weekStart_projectId_key",
            "weekStart",
            "projectId",
            unique=True,
        ),
        Index("weekly_report_aggregates_stale_freshnessDeadline_idx", "stale", "freshnessDeadline"),
        CheckConstraint('EXTRACT(ISODOW FROM "weekStart") = 1', name="week_start_is_monday"),
        CheckConstraint('"riskCount" >= 0', name="risk_count_nonnegative"),
        CheckConstraint('"sourceRevision" > 0', name="source_revision_positive"),
        CheckConstraint('"freshnessDeadline" > "generatedAt"', name="freshness_after_generated"),
        CheckConstraint("jsonb_typeof(summary) = 'object'", name="summary_object"),
        CheckConstraint(
            "jsonb_typeof(\"riskLevelCounts\") = 'object'", name="risk_level_counts_object"
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    weekStart: Mapped[date] = mapped_column(Date, nullable=False)
    projectId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    summary: Mapped[dict[str, JSONValue]] = mapped_column(JSONB, nullable=False)
    riskCount: Mapped[int] = mapped_column(Integer, nullable=False)
    riskLevelCounts: Mapped[dict[str, JSONValue]] = mapped_column(JSONB, nullable=False)
    sourceRevision: Mapped[int] = mapped_column(Integer, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    generatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )
    freshnessDeadline: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False, default=utc_now, onupdate=utc_now
    )


class WeeklyReportItem(Base):
    __tablename__ = "weekly_report_items"
    __table_args__ = (
        Index(
            "weekly_report_items_aggregateId_sourceMailId_sourceCandidateId_riskId_key",
            "aggregateId",
            "sourceMailId",
            "sourceCandidateId",
            "riskId",
            unique=True,
        ),
        Index("weekly_report_items_aggregateId_occurredAt_idx", "aggregateId", "occurredAt"),
        CheckConstraint('"sourceRevision" > 0', name="source_revision_positive"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, default=new_uuid)
    aggregateId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("weekly_report_aggregates.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    sourceMailId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    sourceCandidateId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("mail_risk_candidates.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    riskId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("risks.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    todoId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("action_items.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    sourceRevision: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    riskLevel: Mapped[ProjectRiskLevel] = mapped_column(
        Enum(ProjectRiskLevel, name="ProjectRiskLevel", native_enum=True), nullable=False
    )
    riskStatus: Mapped[RiskStatus] = mapped_column(
        Enum(RiskStatus, name="RiskStatus", native_enum=True), nullable=False
    )
    todoStatus: Mapped[ActionItemStatus] = mapped_column(
        Enum(ActionItemStatus, name="ActionItemStatus", native_enum=True), nullable=False
    )
    occurredAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )


__all__ = ["WeeklyReportAggregate", "WeeklyReportItem"]

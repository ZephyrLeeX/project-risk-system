"""Generated SQLAlchemy models for this bounded domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from risk_platform.models import Base

UUIDType = PG_UUID


class ImportBatchStatus(StrEnum):
    PREVIEWED = "PREVIEWED"
    IMPORTED = "IMPORTED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        Index("import_batches_fileHash_idx", "fileHash"),
        Index("import_batches_status_createdAt_idx", "status", "createdAt"),
        Index("import_batches_uploadedById_idx", "uploadedById"),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    fileName: Mapped[str] = mapped_column(String(255), nullable=False)
    fileHash: Mapped[str] = mapped_column(String(64), nullable=False)
    storageKey: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[ImportBatchStatus] = mapped_column(
        Enum(ImportBatchStatus, name="ImportBatchStatus", native_enum=True),
        nullable=False,
        server_default=text("'PREVIEWED'"),
    )
    sheetName: Mapped[str] = mapped_column(String(128), nullable=False)
    sourceMeta: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    totalRows: Mapped[int] = mapped_column(Integer, nullable=False)
    readyRows: Mapped[int] = mapped_column(Integer, nullable=False)
    warningRows: Mapped[int] = mapped_column(Integer, nullable=False)
    errorRows: Mapped[int] = mapped_column(Integer, nullable=False)
    createdRows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updatedRows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    supplementalTotalRows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    supplementalMatchedRows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    supplementalUnmatchedRows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    supplementalAmbiguousRows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    supplementalWarningRows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    supplementalErrorRows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    legalTotalRows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    legalMatchedRows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    legalUnmatchedRows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    legalAmbiguousRows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    legalWarningRows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    legalErrorRows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    uploadedById: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    confirmedById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    rolledBackById: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    confirmedAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )
    rolledBackAt: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=True
    )


class ImportRowAction(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    SKIP = "SKIP"


class ImportRowStatus(StrEnum):
    READY = "READY"
    WARNING = "WARNING"
    ERROR = "ERROR"
    IMPORTED = "IMPORTED"
    ROLLED_BACK = "ROLLED_BACK"


class ProjectRiskLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class ProjectImportRow(Base):
    __tablename__ = "project_import_rows"
    __table_args__ = (
        Index("project_import_rows_batchId_rowNumber_key", "batchId", "rowNumber", unique=True),
        Index("project_import_rows_batchId_status_idx", "batchId", "status"),
        Index("project_import_rows_importKey_idx", "importKey"),
        Index("project_import_rows_matchedProjectId_idx", "matchedProjectId"),
        Index("project_import_rows_committedProjectId_idx", "committedProjectId"),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    batchId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    rowNumber: Mapped[int] = mapped_column(Integer, nullable=False)
    importKey: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[ImportRowAction] = mapped_column(
        Enum(ImportRowAction, name="ImportRowAction", native_enum=True), nullable=False
    )
    status: Mapped[ImportRowStatus] = mapped_column(
        Enum(ImportRowStatus, name="ImportRowStatus", native_enum=True), nullable=False
    )
    externalCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    projectName: Mapped[str | None] = mapped_column(String(255), nullable=True)
    departmentName: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deliveryOwnerName: Mapped[str | None] = mapped_column(String(128), nullable=True)
    annualPlanAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    actualCollectedAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    remainingAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    monthlyCollections: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    monthAttributes: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    collectionRiskLevel: Mapped[ProjectRiskLevel] = mapped_column(
        Enum(ProjectRiskLevel, name="ProjectRiskLevel", native_enum=True),
        nullable=False,
        server_default=text("'UNKNOWN'"),
    )
    collectionProgress: Mapped[str | None] = mapped_column(Text, nullable=True)
    sourceSnapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    errors: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    matchedProjectId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    committedProjectId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    beforeSnapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    afterSnapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    committedRiskId: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True), nullable=True)
    beforeRiskSnapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    afterRiskSnapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )


class SupplementalMatchStatus(StrEnum):
    MATCHED = "MATCHED"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"


class SupplementalCollectionRow(Base):
    __tablename__ = "supplemental_collection_rows"
    __table_args__ = (
        Index(
            "supplemental_collection_rows_batchId_rowNumber_key",
            "batchId",
            "rowNumber",
            unique=True,
        ),
        Index("supplemental_collection_rows_batchId_status_idx", "batchId", "status"),
        Index("supplemental_collection_rows_matchStatus_idx", "matchStatus"),
        Index("supplemental_collection_rows_projectId_idx", "projectId"),
        Index("supplemental_collection_rows_sourceKey_idx", "sourceKey"),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    batchId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    rowNumber: Mapped[int] = mapped_column(Integer, nullable=False)
    sourceSheet: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("'涵谷回款'")
    )
    sourceKey: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ImportRowStatus] = mapped_column(
        Enum(ImportRowStatus, name="ImportRowStatus", native_enum=True), nullable=False
    )
    matchStatus: Mapped[SupplementalMatchStatus] = mapped_column(
        Enum(SupplementalMatchStatus, name="SupplementalMatchStatus", native_enum=True),
        nullable=False,
    )
    matchedImportKey: Mapped[str | None] = mapped_column(String(64), nullable=True)
    projectId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    externalCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    projectName: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contractReceivableAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    procurementContractAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cumulativeCollectedAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    remainingUncollectedAmount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    actualCollectedThisYear: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    actualCollectedNetThisYear: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    annualCollectionPlan: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    collectionRiskLevel: Mapped[ProjectRiskLevel] = mapped_column(
        Enum(ProjectRiskLevel, name="ProjectRiskLevel", native_enum=True),
        nullable=False,
        server_default=text("'UNKNOWN'"),
    )
    monthlyCollections: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    monthAttributes: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    afterYearAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    sourceSnapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    errors: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )


class LegalMatterMatchStatus(StrEnum):
    MATCHED = "MATCHED"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"


class LegalMatterRow(Base):
    __tablename__ = "legal_matter_rows"
    __table_args__ = (
        Index("legal_matter_rows_batchId_rowNumber_key", "batchId", "rowNumber", unique=True),
        Index("legal_matter_rows_batchId_status_idx", "batchId", "status"),
        Index("legal_matter_rows_matchStatus_idx", "matchStatus"),
        Index("legal_matter_rows_projectId_idx", "projectId"),
        Index("legal_matter_rows_sourceKey_idx", "sourceKey"),
    )
    id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True, nullable=False)
    batchId: Mapped[UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    rowNumber: Mapped[int] = mapped_column(Integer, nullable=False)
    sourceSheet: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("'发函-诉讼清单'")
    )
    sourceKey: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ImportRowStatus] = mapped_column(
        Enum(ImportRowStatus, name="ImportRowStatus", native_enum=True), nullable=False
    )
    matchStatus: Mapped[LegalMatterMatchStatus] = mapped_column(
        Enum(LegalMatterMatchStatus, name="LegalMatterMatchStatus", native_enum=True),
        nullable=False,
    )
    matchedImportKey: Mapped[str | None] = mapped_column(String(64), nullable=True)
    projectId: Mapped[UUID | None] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    externalCode: Mapped[str | None] = mapped_column(String(128), nullable=True)
    projectName: Mapped[str | None] = mapped_column(String(500), nullable=True)
    departmentName: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deliveryOwnerName: Mapped[str | None] = mapped_column(String(128), nullable=True)
    annualPlanAmount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    collectionRiskLevel: Mapped[ProjectRiskLevel] = mapped_column(
        Enum(ProjectRiskLevel, name="ProjectRiskLevel", native_enum=True),
        nullable=False,
        server_default=text("'UNKNOWN'"),
    )
    legalProgress: Mapped[str | None] = mapped_column(Text, nullable=True)
    monthlyCollections: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    monthAttributes: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    sourceSnapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    errors: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    committedRiskId: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True), nullable=True)
    beforeRiskSnapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    afterRiskSnapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updatedAt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True, precision=3), nullable=False
    )

"""Typed metadata-only audit writer and deterministic chain verifier."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.audit.models import AuditActorType, AuditLog, AuditResult

AuditCode = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_.:-]*$"),
]
AuditModule = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_.:-]*$"),
]
ResourceIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    ),
]


class AuditEvent(BaseModel):
    """Closed audit input: identifiers and enums only, with unknown fields forbidden."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    actor_id: UUID | None = None
    actor_type: AuditActorType
    module: AuditModule
    action: AuditCode
    resource_type: AuditCode
    resource_id: ResourceIdentifier | None = None
    trace_id: UUID
    request_id: UUID | None = None
    project_id: UUID | None = None
    failure_code: AuditCode | None = None
    result: AuditResult


class AuditIntegrity(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    total_records: int
    verified_records: int
    first_broken_event_id: UUID | None


_VERIFY_CHAIN_SQL = text(
    '''
    WITH ordered AS (
      SELECT
        log_entry.*,
        lag("integrityHash") OVER (ORDER BY "createdAt", "id") AS expected_previous_hash,
        row_number() OVER (ORDER BY "createdAt", "id") AS position
      FROM "audit_logs" AS log_entry
    ), checked AS (
      SELECT
        "id",
        position,
        (
          "previousHash" IS NOT DISTINCT FROM expected_previous_hash
          AND "integrityHash" = audit_log_compute_hash(
            "id", "actorUserId", "actorType"::text, "module", "action",
            "resourceType", "resourceId", "result"::text, "traceId", "requestId",
            "projectId", "failureCode", "previousHash", "createdAt"
          )
        ) AS valid
      FROM ordered
    )
    SELECT
      count(*)::bigint AS total_records,
      count(*) FILTER (WHERE valid)::bigint AS verified_records,
      (array_agg("id" ORDER BY position) FILTER (WHERE NOT valid))[1] AS first_broken_event_id
    FROM checked
    '''
)


class AuditService:
    """Use a caller-owned session so audit and business facts share one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, event: AuditEvent) -> UUID:
        row = AuditLog(
            actorUserId=event.actor_id,
            actorType=event.actor_type,
            module=event.module,
            action=event.action,
            resourceType=event.resource_type,
            resourceId=event.resource_id,
            result=event.result,
            traceId=str(event.trace_id),
            requestId=str(event.request_id) if event.request_id is not None else None,
            projectId=event.project_id,
            failureCode=event.failure_code,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def record_success(
        self,
        *,
        actor_id: UUID | None,
        actor_type: AuditActorType,
        module: AuditModule,
        action: AuditCode,
        resource_type: AuditCode,
        resource_id: ResourceIdentifier | None,
        trace_id: UUID,
        request_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> UUID:
        return await self.record(
            AuditEvent(
                actor_id=actor_id,
                actor_type=actor_type,
                module=module,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                trace_id=trace_id,
                request_id=request_id,
                project_id=project_id,
                result=AuditResult.SUCCESS,
            )
        )

    async def record_failure(
        self,
        *,
        actor_id: UUID | None,
        actor_type: AuditActorType,
        module: AuditModule,
        action: AuditCode,
        resource_type: AuditCode,
        resource_id: ResourceIdentifier | None,
        trace_id: UUID,
        failure_code: AuditCode,
        request_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> UUID:
        return await self.record(
            AuditEvent(
                actor_id=actor_id,
                actor_type=actor_type,
                module=module,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                trace_id=trace_id,
                request_id=request_id,
                project_id=project_id,
                failure_code=failure_code,
                result=AuditResult.FAILURE,
            )
        )

    async def verify_integrity(self) -> AuditIntegrity:
        result = (await self._session.execute(_VERIFY_CHAIN_SQL)).mappings().one()
        broken = result["first_broken_event_id"]
        return AuditIntegrity(
            status="VALID" if broken is None else "INVALID",
            total_records=result["total_records"],
            verified_records=result["verified_records"],
            first_broken_event_id=broken,
        )


__all__ = ["AuditEvent", "AuditIntegrity", "AuditService"]

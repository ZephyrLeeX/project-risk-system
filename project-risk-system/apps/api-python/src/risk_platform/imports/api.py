"""Upload endpoint; final router composition remains owned by T040."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from risk_platform.auth.service import SessionIdentity
from risk_platform.imports.commit_service import ImportCommitService
from risk_platform.imports.schemas import (
    ConfirmImportRequest,
    ImportBatchDetail,
    ImportBatchListQuery,
    MatchSupplementalRequest,
    PaginatedImportBatches,
    ProjectOption,
)
from risk_platform.imports.service import ImportPreviewService
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.errors import ApiError
from risk_platform.shared.http import ApiResponse, ok
from risk_platform.shared.tracing import get_trace_id

router = APIRouter(prefix="/imports/project-list", tags=["imports"])


class PreviewAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    batch_id: UUID = Field(alias="batchId")
    task_id: UUID = Field(alias="taskId")
    file_name: str = Field(alias="fileName")
    file_hash: str = Field(alias="fileHash")
    status: str


def get_import_preview_service(request: Request) -> ImportPreviewService:
    service = getattr(request.app.state, "import_preview_service", None)
    if not isinstance(service, ImportPreviewService):
        raise RuntimeError("import preview service is not configured")
    return service


def get_import_commit_service(request: Request) -> ImportCommitService:
    service = getattr(request.app.state, "import_commit_service", None)
    if not isinstance(service, ImportCommitService):
        raise RuntimeError("import commit service is not configured")
    return service


@router.post("/preview", response_model=ApiResponse[PreviewAcceptedResponse])
async def preview(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.import.manage"))],
    service: Annotated[ImportPreviewService, Depends(get_import_preview_service)],
) -> ApiResponse[PreviewAcceptedResponse]:
    content = await file.read(20 * 1024 * 1024 + 1)
    try:
        batch = await service.create_preview(
            file_name=file.filename or "",
            content=content,
            uploaded_by=UUID(identity.user.id),
        )
    except ValueError as exc:
        raise ApiError(400, "BAD_REQUEST", str(exc)) from exc
    return ok(
        request,
        PreviewAcceptedResponse(
            batchId=batch.id,
            taskId=batch.taskId,
            fileName=batch.fileName,
            fileHash=batch.fileHash,
            status=batch.status.value,
        ),
        "Excel 预检任务已创建",
    )


@router.get("/batches", response_model=ApiResponse[PaginatedImportBatches])
async def list_batches(
    request: Request,
    query: Annotated[ImportBatchListQuery, Depends()],
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.import.manage"))],
    service: Annotated[ImportCommitService, Depends(get_import_commit_service)],
) -> ApiResponse[PaginatedImportBatches]:
    return ok(request, await service.list(query, identity))


@router.get("/batches/{batch_id}", response_model=ApiResponse[ImportBatchDetail])
async def batch_detail(
    request: Request,
    batch_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.import.manage"))],
    service: Annotated[ImportCommitService, Depends(get_import_commit_service)],
) -> ApiResponse[ImportBatchDetail]:
    return ok(request, await service.detail(batch_id, identity))


@router.get("/batches/{batch_id}/source")
async def batch_source(
    batch_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.import.manage"))],
    service: Annotated[ImportCommitService, Depends(get_import_commit_service)],
) -> Response:
    file_name, content = await service.source(batch_id, identity)
    from urllib.parse import quote

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
    )


@router.get("/projects/options", response_model=ApiResponse[list[ProjectOption]])
async def project_options(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.import.manage"))],
    service: Annotated[ImportCommitService, Depends(get_import_commit_service)],
) -> ApiResponse[list[ProjectOption]]:
    return ok(request, await service.project_options(identity))


@router.post("/supplemental/{row_id}/match", response_model=ApiResponse[ImportBatchDetail])
async def match_supplemental(
    request: Request,
    row_id: UUID,
    payload: MatchSupplementalRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.import.manage"))],
    service: Annotated[ImportCommitService, Depends(get_import_commit_service)],
) -> ApiResponse[ImportBatchDetail]:
    return ok(
        request,
        await service.match_supplemental(
            row_id,
            payload.projectId,
            identity,
            UUID(get_trace_id(request)),
        ),
    )


@router.delete("/supplemental/{row_id}/match", response_model=ApiResponse[ImportBatchDetail])
async def unmatch_supplemental(
    request: Request,
    row_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.import.manage"))],
    service: Annotated[ImportCommitService, Depends(get_import_commit_service)],
) -> ApiResponse[ImportBatchDetail]:
    return ok(
        request,
        await service.unmatch_supplemental(
            row_id,
            identity,
            UUID(get_trace_id(request)),
        ),
    )


@router.post("/batches/{batch_id}/confirm", response_model=ApiResponse[ImportBatchDetail])
async def confirm_batch(
    request: Request,
    batch_id: UUID,
    payload: ConfirmImportRequest,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.import.manage"))],
    service: Annotated[ImportCommitService, Depends(get_import_commit_service)],
) -> ApiResponse[ImportBatchDetail]:
    return ok(
        request,
        await service.confirm(
            batch_id,
            payload.acknowledgeWarnings,
            identity,
            UUID(get_trace_id(request)),
        ),
    )


@router.post("/batches/{batch_id}/rollback", response_model=ApiResponse[ImportBatchDetail])
async def rollback_batch(
    request: Request,
    batch_id: UUID,
    identity: Annotated[SessionIdentity, Depends(require_permissions("admin.import.manage"))],
    service: Annotated[ImportCommitService, Depends(get_import_commit_service)],
) -> ApiResponse[ImportBatchDetail]:
    return ok(
        request,
        await service.rollback(batch_id, identity, UUID(get_trace_id(request))),
        "导入批次已回滚",
    )


__all__ = [
    "PreviewAcceptedResponse",
    "get_import_commit_service",
    "get_import_preview_service",
    "router",
]

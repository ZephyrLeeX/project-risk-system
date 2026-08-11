"""Upload endpoint; final router composition remains owned by T040."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from risk_platform.auth.service import SessionIdentity
from risk_platform.imports.service import ImportPreviewService
from risk_platform.rbac.guards import require_permissions
from risk_platform.shared.errors import ApiError
from risk_platform.shared.http import ApiResponse, ok

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


__all__ = ["PreviewAcceptedResponse", "get_import_preview_service", "router"]

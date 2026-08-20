"""Public HTTP response contract shared by feature modules."""

from __future__ import annotations

from typing import Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from risk_platform.shared.tracing import get_trace_id


class ApiResponse[DataT](BaseModel):
    """Compatibility envelope for every JSON API response."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: str
    message: str
    data: DataT
    trace_id: str = Field(alias="traceId")


class StrictRequestModel(BaseModel):
    """Base contract for feature request DTOs.

    Feature modules inherit this model so unknown JSON fields retain the legacy
    rejection behavior without changing Pydantic globally.
    """

    model_config = ConfigDict(extra="forbid")


class HealthResponse(BaseModel):
    """Static process liveness data; dependency readiness is intentionally separate."""

    model_config = ConfigDict(extra="forbid")

    service: Literal["project-risk-api"] = "project-risk-api"
    status: Literal["ok"] = "ok"
    version: str
    timestamp: str


def ok[DataT](request: Request, data: DataT, message: str = "success") -> ApiResponse[DataT]:
    """Build a success envelope with the current request trace."""

    return ApiResponse(code="OK", message=message, data=data, traceId=get_trace_id(request))


__all__ = ["ApiResponse", "HealthResponse", "StrictRequestModel", "ok"]

"""HTTP contracts for administration option queries."""

from pydantic import BaseModel, ConfigDict


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    code: str
    name: str


class ProjectOptionResponse(BaseModel):
    """Compatible `ProjectOption` contract for the project selector."""

    model_config = ConfigDict(extra="forbid")

    id: str
    externalCode: str | None
    name: str
    departmentName: str | None


__all__ = ["DepartmentResponse", "ProjectOptionResponse"]

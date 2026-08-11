"""HTTP contracts for administration option queries."""

from pydantic import BaseModel, ConfigDict


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    code: str
    name: str


__all__ = ["DepartmentResponse"]

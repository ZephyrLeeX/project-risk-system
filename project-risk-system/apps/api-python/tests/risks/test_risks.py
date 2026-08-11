import pytest
from pydantic import ValidationError

from risk_platform.risks.schemas import LifecycleRequest


def test_lifecycle_reason_is_trimmed_and_has_meaningful_minimum() -> None:
    assert LifecycleRequest(reason="  已完成整改  ").reason == "已完成整改"
    with pytest.raises(ValidationError):
        LifecycleRequest(reason=" no ")


def test_lifecycle_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LifecycleRequest.model_validate({"reason": "已完成整改", "status": "CLOSED"})

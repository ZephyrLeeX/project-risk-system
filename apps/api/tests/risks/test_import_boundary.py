"""Regression coverage for the risk module import boundary."""

import importlib


def test_admin_models_and_risk_service_import_independently() -> None:
    admin_models = importlib.import_module("risk_platform.admin.models")
    risk_service = importlib.import_module("risk_platform.risks.service")

    assert hasattr(admin_models, "User")
    assert hasattr(risk_service, "RisksService")

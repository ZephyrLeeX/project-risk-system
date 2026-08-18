"""Regression coverage for API-only WeChat Compose wiring."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_compose_api_receives_wechat_settings_without_printing_values() -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is unavailable")
    root = Path(__file__).resolve().parents[3]
    environment = {
        **os.environ,
        "POSTGRES_PASSWORD": "compose-test-password",
        "DATA_ENCRYPTION_KEY": "compose-test-key",
        "CORS_ORIGIN": "https://test.invalid",
        "WECHAT_USER_INFO_URL": "https://wechat.test.invalid/user-info",
        "WECHAT_USER_INFO_TIMEOUT_SECONDS": "5",
        "WECHAT_USER_INFO_MAX_RETRIES": "2",
    }
    result = subprocess.run(
        [docker, "compose", "-f", "infra/docker-compose.yml", "config", "--format", "json"],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    resolved = json.loads(result.stdout)
    api_environment = resolved["services"]["api"]["environment"]
    assert api_environment["WECHAT_USER_INFO_URL"] == environment["WECHAT_USER_INFO_URL"]
    assert api_environment["WECHAT_USER_INFO_TIMEOUT_SECONDS"] == "5"
    assert api_environment["WECHAT_USER_INFO_MAX_RETRIES"] == "2"
    assert "WECHAT_USER_INFO_URL" not in resolved["services"]["worker"]["environment"]
    assert "WECHAT_USER_INFO_URL" not in resolved["services"]["scheduler"]["environment"]

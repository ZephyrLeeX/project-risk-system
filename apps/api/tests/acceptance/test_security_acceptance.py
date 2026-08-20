"""T037 — security acceptance: CSRF, Cookie, SSRF, file upload, model output.

Proves the cross-cutting security invariants from Design §7/§11 and the
approved ADRs hold at release level: origin (CSRF) validation on mutating
endpoints, session-cookie lifecycle, the SSRF-safe outbound guard, Excel
upload safety, and the closed Agent Provider output boundary (ADR 0028).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from ipaddress import ip_network

import pytest

from risk_platform.imports.parser import MAX_WORKBOOK_BYTES, WorkbookError
from risk_platform.imports.storage import WorkbookStorage
from risk_platform.shared.outbound import OutboundEndpointGuard, OutboundSecurityError

from .conftest import ACCEPTANCE_PASSWORD, AcceptanceHarness

# --------------------------------------------------------------------------- #
# CSRF / Cookie lifecycle (real session path, production environment)         #
# --------------------------------------------------------------------------- #


def test_csrf_origin_validation_rejects_untrusted_origin(acceptance: AcceptanceHarness) -> None:
    async def scenario() -> None:
        app = acceptance.build_app(production=True, request_origin_validation_enabled=True)
        async for client in acceptance.client(app):
            login = await acceptance.login(client, "PROJECT_MANAGER")
            assert login.status_code == 200

            bad_origin = await client.post(
                "/api/auth/logout", headers={"origin": "https://evil.invalid"}
            )
            assert (bad_origin.status_code, bad_origin.json()["message"]) == (
                403,
                "请求来源校验失败",
            )
            # A rejected logout must not invalidate the session.
            assert (await client.get("/api/auth/session")).status_code == 200

            good_origin = await client.post(
                "/api/auth/logout", headers={"origin": "https://web.internal"}
            )
            assert good_origin.status_code == 200
            assert "Max-Age=0" in good_origin.headers["set-cookie"]
            assert (await client.get("/api/auth/session")).status_code == 401

    asyncio.run(scenario())


def test_csrf_origin_validation_guards_password_change(acceptance: AcceptanceHarness) -> None:
    async def scenario() -> None:
        app = acceptance.build_app(production=True)
        async for client in acceptance.client(app):
            login = await acceptance.login(client, "PROJECT_MANAGER")
            assert login.status_code == 200
            rejected = await client.post(
                "/api/auth/change-password",
                headers={"origin": "https://evil.invalid"},
                json={
                    "currentPassword": ACCEPTANCE_PASSWORD,
                    "newPassword": "New_Acceptance_2!",
                    "confirmPassword": "New_Acceptance_2!",
                },
            )
            assert (rejected.status_code, rejected.json()["message"]) == (
                403,
                "请求来源校验失败",
            )

    asyncio.run(scenario())


def test_disabled_origin_validation_preserves_authentication_and_rbac(
    acceptance: AcceptanceHarness,
) -> None:
    async def scenario() -> None:
        app = acceptance.build_app(
            production=True, request_origin_validation_enabled=False
        )
        async for client in acceptance.client(app):
            unauthenticated = await client.post(
                "/api/auth/logout", headers={"origin": "https://evil.invalid"}
            )
            assert (unauthenticated.status_code, unauthenticated.json()["message"]) == (
                401,
                "登录状态已失效，请重新登录",
            )

            login = await acceptance.login(client, "PROJECT_MANAGER")
            assert login.status_code == 200
            accepted = await client.post(
                "/api/auth/logout", headers={"origin": "https://evil.invalid"}
            )
            assert accepted.status_code == 200

        rbac_app = acceptance.build_app(
            identity=acceptance.identity_for("PROJECT_MANAGER"),
            production=True,
            request_origin_validation_enabled=False,
        )
        async for client in acceptance.client(rbac_app):
            forbidden = await client.post(
                "/api/admin/roles",
                headers={"origin": "https://evil.invalid"},
                json={
                    "code": "TEST_ROLE",
                    "name": "测试角色",
                    "permissionCodes": [],
                    "defaultDataScope": "NONE",
                },
            )
            assert forbidden.status_code == 403
            assert forbidden.json()["message"] != "请求来源校验失败"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# SSRF-safe outbound endpoint guard (ADR 0007 / T007)                         #
# --------------------------------------------------------------------------- #


class _StaticResolver:
    def __init__(self, *answers: Sequence[str] | OSError) -> None:
        self._answers = list(answers)

    async def __call__(self, hostname: str, port: int) -> Sequence[str]:
        del hostname, port
        answer = self._answers.pop(0)
        if isinstance(answer, OSError):
            raise answer
        return answer


@pytest.mark.parametrize(
    ("host", "address"),
    [
        ("localhost", "127.0.0.1"),
        ("provider.example", "10.2.3.4"),
        ("provider.example", "169.254.169.254"),
        ("provider.example", "::1"),
        ("provider.example", "::ffff:127.0.0.1"),
    ],
)
def test_outbound_guard_blocks_private_loopback_and_metadata(host: str, address: str) -> None:
    async def scenario() -> None:
        guard = OutboundEndpointGuard(resolver=_StaticResolver((address,)))
        with pytest.raises(OutboundSecurityError) as error:
            await guard.resolve_imap(host, 993)
        assert str(error.value) == "OUTBOUND_DESTINATION_FORBIDDEN"

    asyncio.run(scenario())


def test_outbound_guard_rejects_dns_rebinding_on_revalidation() -> None:
    async def scenario() -> None:
        guard = OutboundEndpointGuard(resolver=_StaticResolver(("93.184.216.34",), ("127.0.0.1",)))
        endpoint = await guard.resolve_provider("https://api.example.com/v1")
        assert endpoint.connection_address == "93.184.216.34"
        with pytest.raises(OutboundSecurityError) as error:
            await guard.revalidate(endpoint)
        assert str(error.value) == "OUTBOUND_DESTINATION_FORBIDDEN"

    asyncio.run(scenario())


def test_outbound_guard_allows_approved_internal_network_only() -> None:
    async def scenario() -> None:
        from risk_platform.shared.outbound import OutboundPolicy

        policy = OutboundPolicy(
            approved_internal_hostnames=frozenset({"model.ai.internal"}),
            approved_internal_networks=(ip_network("10.20.0.0/16"),),
        )
        approved = await OutboundEndpointGuard(
            policy, _StaticResolver(("10.20.3.4",))
        ).resolve_provider("https://model.ai.internal:8443/v1")
        assert approved.connection_address == "10.20.3.4"
        with pytest.raises(OutboundSecurityError):
            await OutboundEndpointGuard(policy, _StaticResolver(("10.21.3.4",))).resolve_provider(
                "https://model.ai.internal:8443/v1"
            )

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# Excel upload safety (ADR 0024 attachment/file boundary)                     #
# --------------------------------------------------------------------------- #


def test_import_preview_rejects_non_xlsx_name_and_non_zip_content(
    acceptance: AcceptanceHarness,
) -> None:
    async def scenario() -> None:
        app = acceptance.build_app(identity=acceptance.identity_for("SYSTEM_ADMIN"))
        async for client in acceptance.client(app):
            wrong_name = await client.post(
                "/api/imports/project-list/preview",
                files={"file": ("list.csv", b"PK\x03\x04placeholder", "application/vnd.ms-excel")},
            )
            assert (wrong_name.status_code, wrong_name.json()["code"]) == (400, "BAD_REQUEST")

            non_zip = await client.post(
                "/api/imports/project-list/preview",
                files={
                    "file": (
                        "list.xlsx",
                        b"%PDF-1.4 not an excel file",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
            assert (non_zip.status_code, non_zip.json()["code"]) == (400, "BAD_REQUEST")

            empty = await client.post(
                "/api/imports/project-list/preview",
                files={"file": ("list.xlsx", b"", "application/octet-stream")},
            )
            assert (empty.status_code, empty.json()["code"]) == (400, "BAD_REQUEST")

    asyncio.run(scenario())


def test_import_storage_rejects_oversized_and_corrupt_workbooks() -> None:
    # A 21MB body is rejected before any parse, even with a valid name/magic.
    oversized = b"PK" + b"\x00" * (MAX_WORKBOOK_BYTES + 1)
    with pytest.raises(WorkbookError, match="20MB"):
        WorkbookStorage.validate("list.xlsx", oversized)

    # A name carrying a path separator is neutralized and rejected.
    with pytest.raises(WorkbookError, match=r"\.xlsx"):
        WorkbookStorage.validate("../escape.xlsx", b"PK\x03\x04rest")

    # Non-zip content with a valid name is rejected by the magic check.
    with pytest.raises(WorkbookError, match="有效的 Excel"):
        WorkbookStorage.validate("list.xlsx", b"not-a-zip-archive" * 10)

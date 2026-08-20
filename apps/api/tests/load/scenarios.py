"""Virtual-user scenario driver for the T038 load harness (ADR 0032 §1/§9).

Spawns 50 concurrent authenticated virtual users against the T035 Compose proxy
edge (``https://<host>:8443``), each cycling through the ADR 0032 §1 endpoint
classes it is permitted to exercise. A >=30s warmup window is discarded; the
>=60s measurement window records per-class latency samples into
:class:`~risk_platform_tests_load.stats.LatencySamples`.

The driver is network-only: it speaks HTTP to the proxy and never imports the
application process. Each VU exercises only the endpoint classes its seeded
role is authorized for (see ``ROLE_CLASSES``), so every request reaches the
real handler (no 403 instant rejections). POSTs omit the ``Origin`` header so
the production ``validate_request_origin`` guard passes (it only rejects a
*present, untrusted* origin) without touching CORS config; the import preview
route does not validate origin at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from typing import Protocol
from zipfile import ZIP_DEFLATED, ZipFile

import httpx2

from .config import LoadConfig
from .stats import LatencySamples

log = logging.getLogger("t038.load.scenarios")


class _DatasetView(Protocol):
    """Structural view of seeded resource id pools the VU fleet reads.

    Both :class:`~load.generator.GeneratedDataset` and the orchestrator's
    lightweight ``_DatasetShim`` (which re-reads id pools from the DB) satisfy
    this protocol, so ``drive_load`` accepts either without coupling to the
    full dataset shape.
    """

    todo_ids: list[uuid.UUID]
    risk_ids: list[uuid.UUID]
    project_ids: list[uuid.UUID]

# Endpoint class each role is permitted to exercise, aligned to the seeded
# RBAC matrix (src/risk_platform/seed.py ROLES) and the per-route permission
# guards. Every (role, class) pair below is authorization-clean — the role
# holds the permission the class's endpoint(s) require, so no VU produces a
# 403 (which would be an instant rejection, not a real capacity sample).
#
#   fast_read       — dashboard.view (all 4 roles).
#   mutation        — risk.resolve + ALL data scope (RISK_ADMIN only);
#                     SYSTEM_ADMIN lacks risk.resolve, PROJECT_MANAGER is
#                     OWNED_OR_ASSIGNED with no seeded project scopes (404).
#   admin_overview  — GET /admin/overview needs only an authenticated identity
#                     (no require_permissions); done by the two admin roles.
#   async_dispatch  — admin.import.manage (SYSTEM_ADMIN only) for the Excel
#                     upload preview dispatch (ADR 0032 §1 names this endpoint
#                     class). RISK_ADMIN/PM/VA lack admin.import.manage.
#   auth            — login (any authenticated user).
ROLE_CLASSES: dict[str, tuple[str, ...]] = {
    "SYSTEM_ADMIN": ("fast_read", "admin_overview", "async_dispatch", "auth"),
    "RISK_ADMIN": ("fast_read", "mutation", "admin_overview", "auth"),
    "PROJECT_MANAGER": ("fast_read", "auth"),
    "VIEWER_AUDITOR": ("fast_read", "auth"),
}

AUTH_PATH = "/api/auth/login"
PREVIEW_PATH = "/api/imports/project-list/preview"

# Per-class target request rates (req/s) modelled on the ADR 0009 realistic
# load profile. Scarce-resource classes are paced so the synthetic fleet does
# not overload a single worker (async_dispatch), a single CPU core's argon2
# (auth) or an aggregation scan (admin_overview) beyond realistic usage.
# fast_read/mutation run unbounded (None) — they are the actual capacity probe.
CLASS_RATE_LIMITS: dict[str, float | None] = {
    "fast_read": None,
    "mutation": None,
    "admin_overview": 4.0,
    "async_dispatch": 7.0,
    "auth": 6.0,
}


class RateLimiter:
    """Async token-bucket limiter (approximate; sufficient for pacing)."""

    def __init__(self, rate_per_sec: float) -> None:
        self.rate = rate_per_sec
        self._tokens = rate_per_sec
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(self.rate, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
            await asyncio.sleep(1.0 / self.rate)

    async def try_acquire(self) -> bool:
        """Non-blocking: take a token if one is available, else return False.

        Used so a VU whose next class is rate-capped skips it (advancing to the
        next class) instead of blocking. This keeps unpaced classes (fast_read,
        mutation) productive while the rate-limited classes still observe their
        global caps — without it, a VU blocked on a 4/s admin_overview token
        would starve the mutation class of samples.
        """
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self.rate, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False



@dataclass(slots=True)
class VuContext:
    """Per-VU authenticated HTTP session + targeted resource pools."""

    index: int
    role: str
    username: str
    password: str
    client: httpx2.AsyncClient
    todo_ids: list[str]
    risk_ids: list[str]
    project_ids: list[str]
    classes: tuple[str, ...]
    rng: random.Random


@dataclass(slots=True)
class LoadResult:
    samples: dict[str, LatencySamples]
    vu_request_counts: list[int] = field(default_factory=list)
    measurement_start: float = 0.0
    measurement_end: float = 0.0


def _api(data: dict[str, object], key: str) -> object:
    envelope = data.get("data") if isinstance(data.get("data"), dict) else data
    return envelope.get(key) if isinstance(envelope, dict) else None


async def _login(ctx: VuContext) -> None:
    # Origin is deliberately omitted: ``validate_request_origin`` only rejects a
    # *present, untrusted* origin (cors_origins defaults to the Vite dev server,
    # not the proxy edge), and the session cookie is set by the handler
    # unconditionally — it does not depend on Origin. Sending an Origin here
    # would trigger a 403 "请求来源校验失败".
    resp = await ctx.client.post(
        AUTH_PATH,
        json={"username": ctx.username, "password": ctx.password},
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"VU{ctx.index} login failed for {ctx.username}: {resp.status_code} {resp.text[:200]}"
        )
    # httpx2 persists the Set-Cookie session into ctx.client.cookies automatically.


async def _do_fast_read(ctx: VuContext) -> tuple[float, int, bool]:
    # All four roles hold ``dashboard.view`` (seed.py), so every path here is
    # authorization-clean for the whole fleet. Mailbox sync-summary is
    # deliberately excluded: it requires mailbox.sync_self (RISK_ADMIN only)
    # and is an external-IMAP-substitute dimension not measured this round
    # (ADR 0032 §1 fast_read = 单实体/分页列表/dashboard/weekly read).
    rng = ctx.rng
    choice = rng.random()
    if choice < 0.35:
        path = "/api/risks"
        params: dict[str, int] = {"page": 1, "pageSize": 20}
    elif choice < 0.55:
        path = "/api/todos"
        params = {"page": 1, "pageSize": 20}
    elif choice < 0.78:
        path = "/api/dashboard/summary"
        params = {}
    else:
        path = "/api/weekly-reports/current"
        params = {}
    r = await ctx.client.get(path, params=params)
    return _elapsed(r), r.status_code, False


async def _do_mutation(ctx: VuContext) -> tuple[float, int, bool]:
    # PATCH a todo's completionNote — always-valid text update, no state guard.
    todo_id = ctx.rng.choice(ctx.todo_ids)
    r = await ctx.client.patch(
        f"/api/todos/{todo_id}",
        json={"completionNote": f"压测更新-{ctx.index}-{int(time.monotonic()*1000)%10**9}"},
    )
    return _elapsed(r), r.status_code, False


async def _do_admin_overview(ctx: VuContext) -> tuple[float, int, bool]:
    # ADR 0032 §1 admin_overview = ``GET /admin/overview`` (ADR 0023 five-item
    # health rollup). That route guards only on an authenticated identity
    # (no require_permissions), so both admin roles (SA/RA) return 200. The
    # sibling ``/admin/users/summary`` requires admin.user.manage (SA only)
    # and is excluded to avoid RA 403s polluting the class with instant
    # rejections.
    r = await ctx.client.get("/api/admin/overview")
    return _elapsed(r), r.status_code, False


def _build_project_workbook(seed_text: str) -> bytes:
    """Build a minimal, parser-valid project-list .xlsx carrying ``seed_text``.

    The workbook shape mirrors the parser's fixture (tests/imports/
    test_parser.py ``_xlsx``): MAIN_SHEET ``数据回款`` with the approved
    column headers and one project row. ``seed_text`` is embedded in the
    project-code cell so each upload has a unique SHA-256 — without that,
    ``ImportPreviewService.create_preview`` deduplicates on fileHash and
    1049/1050 uploads become instant no-ops that measure the dedup SELECT,
    not the enqueue path the §1 async_dispatch gate is meant to exercise.
    """

    workbook = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="数据回款" sheetId="1" r:id="rId1"/>'
        '<sheet name="汇总" sheetId="2" r:id="rId2"/></sheets></workbook>'
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>'
    )
    headers = "".join(
        f'<c r="{chr(64 + col)}3" t="inlineStr"><is><t>{text}</t></is></c>'
        for col, text in {
            1: "交付部门",
            2: "交付负责人",
            3: "项目编码",
            4: "项目名称",
            20: "回款风险",
            21: "回款进展",
        }.items()
    )
    cells = "".join(
        f'<c r="{chr(64 + col)}4" t="inlineStr"><is><t>{text}</t></is></c>'
        for col, text in {1: "一部", 2: "张三", 3: seed_text, 4: "压测项目"}.items()
    )
    cells += (
        '<c r="E4"><v>100</v></c><c r="F4"><v>40</v></c>'
        '<c r="G4"><v>60</v></c><c r="H4"><v>0</v></c>'
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="3">{headers}</row><row r="4">{cells}</row>'
        "</sheetData></worksheet>"
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr(
            "xl/worksheets/sheet2.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData/></worksheet>",
        )
    return output.getvalue()


async def _do_async_dispatch(ctx: VuContext) -> tuple[float, int, bool]:
    # ADR 0032 section 1 async_dispatch = the Excel upload preview dispatch
    # (enqueue only, excludes worker parsing). Posts a unique project-list
    # workbook to the import preview endpoint (admin.import.manage,
    # SYSTEM_ADMIN). The request enqueues an IMPORT_PREVIEW durable task and
    # returns immediately (enqueue-only); the worker parse is excluded from
    # this gate's latency and is a real internal task (not an external AI/IMAP
    # substitute), so it is NOT marked substituted. The workbook is valid and
    # parses to one unmatched project row (no task FAILED), keeping sections
    # 2/3 clean.
    seed = f"P-{ctx.index:04d}-{int(time.monotonic()*1000)%10**9}"
    content = _build_project_workbook(seed)
    r = await ctx.client.post(
        PREVIEW_PATH,
        files={
            "file": (
                "project-list.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    return _elapsed(r), r.status_code, False


async def _do_auth(ctx: VuContext) -> tuple[float, int, bool]:
    # Fresh login measures argon2id auth latency (the §1 auth class). Origin is
    # omitted for the same reason as ``_login``: ``validate_request_origin``
    # only rejects a *present, untrusted* origin, and the proxy edge
    # (https://localhost:8443) is not in ``cors_origins`` (which defaults to the
    # Vite dev server). The response Set-Cookie refreshes ``ctx.client``'s
    # session, so subsequent class requests stay authenticated.
    r = await ctx.client.post(
        AUTH_PATH,
        json={"username": ctx.username, "password": ctx.password},
    )
    return _elapsed(r), r.status_code, False


_DISPATCH: dict[str, object] = {
    "fast_read": _do_fast_read,
    "mutation": _do_mutation,
    "admin_overview": _do_admin_overview,
    "async_dispatch": _do_async_dispatch,
    "auth": _do_auth,
}


def _elapsed(resp: httpx2.Response) -> float:
    # httpx2 exposes the request elapsed time in milliseconds.
    elapsed = getattr(resp, "elapsed", None)
    if elapsed is not None:
        return float(elapsed.total_seconds() * 1000.0)
    return 0.0


async def _run_vu(
    ctx: VuContext,
    config: LoadConfig,
    samples: dict[str, LatencySamples],
    limiters: dict[str, RateLimiter | None],
    stop_at: float,
) -> int:
    """Run one VU: warmup (discarded) then measurement (recorded) until ``stop_at``."""

    await _login(ctx)
    warmup_end = time.monotonic() + config.warmup_seconds
    request_count = 0
    cls_index = 0
    # Warmup loop.
    while time.monotonic() < warmup_end:
        cls = ctx.classes[cls_index % len(ctx.classes)]
        limiter = limiters.get(cls)
        if limiter is not None and not await limiter.try_acquire():
            cls_index += 1
            continue
        with contextlib.suppress(httpx2.HTTPError, OSError):
            await _DISPATCH[cls](ctx)  # type: ignore[operator]
        cls_index += 1
    # Measurement loop.
    while time.monotonic() < stop_at:
        cls = ctx.classes[cls_index % len(ctx.classes)]
        limiter = limiters.get(cls)
        if limiter is not None and not await limiter.try_acquire():
            cls_index += 1
            continue
        substituted = False
        status = 0
        latency = 0.0
        try:
            latency, status, substituted = await _DISPATCH[cls](ctx)  # type: ignore[operator]
        except (httpx2.HTTPError, OSError) as exc:
            # Transport failure (connection reset/timeout) counts as a 5xx-class
            # infrastructure error, not a substituted-provider failure.
            status = 599
            latency = 0.0
            log.debug("VU%d %s transport error: %s", ctx.index, cls, exc)
        samples[cls].record(latency, status, substituted_5xx=substituted)
        request_count += 1
        cls_index += 1
    return request_count


async def drive_load(
    config: LoadConfig,
    dataset: _DatasetView,
    base_url: str,
    password: str,
    samples: dict[str, LatencySamples],
) -> LoadResult:
    """Drive the configured VU fleet for one measurement run."""

    config.validate()
    # Deterministic per-VU (role, per-role index) assignment matching LoadConfig.
    vu_assignments: list[tuple[str, int]] = []
    for role, count in (
        ("SYSTEM_ADMIN", config.vu_system_admin),
        ("RISK_ADMIN", config.vu_risk_admin),
        ("PROJECT_MANAGER", config.vu_project_manager),
        ("VIEWER_AUDITOR", config.vu_viewer_auditor),
    ):
        vu_assignments.extend((role, j) for j in range(count))

    contexts: list[VuContext] = []
    clients: list[httpx2.AsyncClient] = []
    for i, (role, per_role_idx) in enumerate(vu_assignments):
        username = f"load-{role.lower()}-{per_role_idx:04d}"
        client = httpx2.AsyncClient(
            base_url=base_url,
            verify=False,  # proxy self-signed cert
            timeout=httpx2.Timeout(30.0, connect=10.0),
            follow_redirects=False,
        )
        clients.append(client)
        vu_rng = random.Random(config.seed * 1_000_003 + i)
        contexts.append(
            VuContext(
                index=i,
                role=role,
                username=username,
                password=password,
                client=client,
                todo_ids=[str(t) for t in dataset.todo_ids],
                risk_ids=[str(r) for r in dataset.risk_ids],
                project_ids=[str(p) for p in dataset.project_ids],
                classes=ROLE_CLASSES[role],
                rng=vu_rng,
            )
        )

    stop_at = time.monotonic() + config.warmup_seconds + config.measurement_seconds
    log.info(
        "driving %d VUs (warmup=%.0fs, measurement=%.0fs) against %s",
        config.vu_count,
        config.warmup_seconds,
        config.measurement_seconds,
        base_url,
    )
    measurement_start = time.monotonic() + config.warmup_seconds
    limiters: dict[str, RateLimiter | None] = {
        cls: (RateLimiter(rate) if rate is not None else None)
        for cls, rate in CLASS_RATE_LIMITS.items()
    }
    try:
        counts = await asyncio.gather(
        *(_run_vu(ctx, config, samples, limiters, stop_at) for ctx in contexts)
        )
    finally:
        for c in clients:
            await c.aclose()
    measurement_end = time.monotonic()
    return LoadResult(
        samples=samples,
        vu_request_counts=list(counts),
        measurement_start=measurement_start,
        measurement_end=measurement_end,
    )

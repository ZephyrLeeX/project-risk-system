"""Audit query, integrity and metadata-only export application service."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from risk_platform.admin.models import User
from risk_platform.audit.models import AuditActorType, AuditLog, AuditResult
from risk_platform.audit.schemas import (
    AuditActionGroup,
    AuditDateRange,
    AuditExportFormat,
    AuditExportRequest,
    AuditFilter,
    AuditListQuery,
    AuditLogDetail,
    AuditLogIntegrity,
    AuditLogListItem,
    AuditLogOption,
    AuditLogOptions,
    AuditLogSummary,
    AuditModuleKey,
    PaginatedAuditLogs,
)
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.projects.models import Project
from risk_platform.rbac.models import Role, UserRole
from risk_platform.rbac.scopes import project_scope_predicate
from risk_platform.shared.errors import ApiError

_TZ = ZoneInfo("Asia/Shanghai")
_MODULES: dict[AuditModuleKey, tuple[str, ...]] = {
    AuditModuleKey.AUTH: ("AUTH",),
    AuditModuleKey.PERMISSION: ("ADMIN_USER", "ADMIN_ROLE"),
    AuditModuleKey.MAILBOX: ("MAILBOX", "MAIL_SYNC", "MAIL_MESSAGE"),
    AuditModuleKey.AI: ("AI", "ADMIN_AI"),
    AuditModuleKey.RISK: ("RISK", "TODO"),
    AuditModuleKey.IMPORT: ("IMPORT",),
    AuditModuleKey.CONFIG: ("SYSTEM_CONFIG",),
    AuditModuleKey.AUDIT: ("AUDIT",),
}
_ACTION_PATTERNS: dict[AuditActionGroup, tuple[str, ...]] = {
    AuditActionGroup.CREATE: ("CREATE", "CREATED", "REPORT", "STARTED"),
    AuditActionGroup.UPDATE: (
        "UPDATE",
        "UPDATED",
        "CHANGED",
        "STATUS",
        "RESOLVED",
        "REOPENED",
        "MATCHED",
        "UNMATCHED",
        "UNLOCK",
    ),
    AuditActionGroup.TEST: ("TEST",),
    AuditActionGroup.LOGIN: ("LOGIN", "LOGOUT", "PASSWORD"),
    AuditActionGroup.PUBLISH: ("PUBLISH", "PUBLISHED", "CONFIRM", "CONFIRMED"),
    AuditActionGroup.ROLLBACK: ("ROLLBACK", "ROLLED_BACK"),
    AuditActionGroup.EXPORT: ("EXPORT",),
}
_MODULE_LABELS = {
    "AUTH": "认证",
    "PERMISSION": "权限",
    "MAILBOX": "邮箱",
    "AI": "AI",
    "RISK": "风险",
    "IMPORT": "导入",
    "CONFIG": "配置",
    "AUDIT": "审计",
    "OTHER": "其他",
}
_ACTION_LABELS = {
    "CREATE": "创建",
    "UPDATE": "更新",
    "TEST": "测试",
    "LOGIN": "登录",
    "PUBLISH": "发布",
    "ROLLBACK": "回滚",
    "EXPORT": "导出",
    "OTHER": "其他",
}


class ExportedAuditFile:
    def __init__(self, content: bytes, filename: str, media_type: str, count: int) -> None:
        self.content, self.filename, self.media_type, self.count = (
            content,
            filename,
            media_type,
            count,
        )


class AuditQueryService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def summary(self, identity: SessionIdentity) -> AuditLogSummary:
        async with self._factory() as session:
            scope = self._scope(identity)
            today = datetime.combine(datetime.now(_TZ).date(), time.min, tzinfo=_TZ).astimezone(UTC)
            tomorrow = today + timedelta(days=1)
            yesterday = today - timedelta(days=1)
            today_where = and_(scope, AuditLog.createdAt >= today, AuditLog.createdAt < tomorrow)
            yesterday_where = and_(
                scope, AuditLog.createdAt >= yesterday, AuditLog.createdAt < today
            )
            values = await session.execute(
                select(
                    func.count().filter(today_where),
                    func.count().filter(yesterday_where),
                    func.count().filter(and_(today_where, AuditLog.result == AuditResult.FAILURE)),
                    func.count(func.distinct(AuditLog.actorUserId)).filter(
                        and_(today_where, AuditLog.actorUserId.is_not(None))
                    ),
                    func.count(func.distinct(AuditLog.actorUserId)).filter(
                        and_(
                            today_where,
                            AuditLog.actorUserId.is_not(None),
                            AuditLog.actorUserId.in_(
                                select(User.id)
                                .join(UserRole, UserRole.userId == User.id)
                                .join(Role, Role.id == UserRole.roleId)
                                .where(Role.code == "SYSTEM_ADMIN")
                            ),
                        )
                    ),
                )
            )
            row = values.one()
            return AuditLogSummary(
                todayCount=row[0],
                yesterdayCount=row[1],
                dayChange=row[0] - row[1],
                failedCount=row[2],
                activeActorCount=row[3],
                systemAdminActorCount=row[4],
            )

    async def options(self, identity: SessionIdentity) -> AuditLogOptions:
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(AuditLog.module, func.count())
                    .where(self._scope(identity))
                    .group_by(AuditLog.module)
                )
            ).all()
            actions = (
                await session.execute(
                    select(AuditLog.action, func.count())
                    .where(self._scope(identity))
                    .group_by(AuditLog.action)
                )
            ).all()
        module_counts: dict[str, int] = {}
        action_counts: dict[AuditActionGroup, int] = {}
        for module, count in rows:
            module_key = _module_key(module)
            module_counts[module_key.value] = module_counts.get(module_key.value, 0) + count
        for action, count in actions:
            action_key = _action_group(action)
            action_counts[action_key] = action_counts.get(action_key, 0) + count
        return AuditLogOptions(
            modules=[
                AuditLogOption(value=k, label=_MODULE_LABELS.get(k, k), count=v)
                for k, v in sorted(module_counts.items())
            ],
            actions=[
                AuditLogOption(value=k.value, label=_ACTION_LABELS.get(k.value, k.value), count=v)
                for k, v in sorted(action_counts.items(), key=lambda x: x[0].value)
            ],
        )

    async def list(self, identity: SessionIdentity, query: AuditListQuery) -> PaginatedAuditLogs:
        async with self._factory() as session:
            statement = self._statement(identity, query)
            total = (
                await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
            )
            rows = (
                await session.execute(
                    statement.order_by(AuditLog.createdAt.desc(), AuditLog.id.desc())
                    .offset((query.page - 1) * query.pageSize)
                    .limit(query.pageSize)
                )
            ).all()
        items = [_map_item(row) for row in rows]
        return PaginatedAuditLogs(
            items=items, page=query.page, pageSize=query.pageSize, total=total
        )

    async def detail(self, identity: SessionIdentity, audit_id: UUID) -> AuditLogDetail:
        async with self._factory() as session:
            row = (
                await session.execute(
                    self._statement(
                        identity,
                        AuditFilter(
                            dateRange=AuditDateRange.CUSTOM,
                            startDate=date(1970, 1, 1),
                            endDate=datetime.now(_TZ).date(),
                        ),
                    ).where(AuditLog.id == audit_id)
                )
            ).first()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "审计事件不存在或不在当前授权范围内")
        item = _map_item(row)
        return AuditLogDetail(
            **item.model_dump(),
            context="操作记录仅包含受控元数据；未写入快照、请求正文或敏感内容。",
            previousHash=row[0].previousHash,
            integrityHash=row[0].integrityHash,
        )

    async def integrity(self, identity: SessionIdentity) -> AuditLogIntegrity:
        async with self._factory() as session:
            result = await AuditService(session).verify_integrity()
        if identity.user.roleCodes and "SYSTEM_ADMIN" not in identity.user.roleCodes:
            # Integrity is global chain metadata; exposing it to permitted auditors is safe.
            pass
        return AuditLogIntegrity(
            status=result.status,
            totalRecords=result.total_records,
            verifiedRecords=result.verified_records,
            firstBrokenEventId=result.first_broken_event_id,
            lastVerifiedAt=datetime.now(UTC),
        )

    async def export(
        self, identity: SessionIdentity, request: AuditExportRequest, trace_id: UUID
    ) -> ExportedAuditFile:
        reason = request.reason.strip()
        async with self._factory() as session:
            try:
                rows = (
                    await session.execute(
                        self._statement(identity, request)
                        .order_by(AuditLog.createdAt.desc(), AuditLog.id.desc())
                        .limit(10_001)
                    )
                ).all()
                if len(rows) > 10_000:
                    raise ApiError(
                        400,
                        "EXPORT_SCOPE_TOO_LARGE",
                        "当前筛选结果超过10000条，请缩小日期或筛选范围后重试",
                    )
                items = [_map_item(row) for row in rows]
                audit = AuditService(session)
                await audit.record_success(
                    actor_id=UUID(identity.user.id),
                    actor_type=AuditActorType.USER,
                    module="AUDIT",
                    action="AUDIT_LOG_EXPORTED",
                    resource_type="AUDIT_EXPORT",
                    resource_id=None,
                    trace_id=trace_id,
                )
                await session.commit()
            except Exception as error:
                await AuditService(session).record_failure(
                    actor_id=UUID(identity.user.id),
                    actor_type=AuditActorType.USER,
                    module="AUDIT",
                    action="AUDIT_LOG_EXPORT_FAILED",
                    resource_type="AUDIT_EXPORT",
                    resource_id=None,
                    trace_id=trace_id,
                    failure_code=(
                        "EXPORT_SCOPE_TOO_LARGE" if isinstance(error, ApiError) else "EXPORT_FAILED"
                    ),
                )
                await session.commit()
                raise
        return _render_export(items, request.format, reason)

    def _scope(self, identity: SessionIdentity) -> ColumnElement[bool]:
        if "SYSTEM_ADMIN" in identity.user.roleCodes or identity.user.dataScope == "ALL":
            return true()
        return and_(
            AuditLog.projectId.is_not(None),
            AuditLog.projectId.in_(
                select(Project.id).where(
                    project_scope_predicate(UUID(identity.user.id), identity.user.dataScope)
                )
            ),
        )

    def _statement(
        self, identity: SessionIdentity, query: AuditFilter
    ) -> Select[tuple[AuditLog, User | None, str | None]]:
        conditions: list[ColumnElement[bool]] = [self._scope(identity)]
        conditions.extend(_filter_conditions(query))
        role_name = (
            select(Role.name)
            .join(UserRole, UserRole.roleId == Role.id)
            .where(UserRole.userId == AuditLog.actorUserId)
            .limit(1)
            .scalar_subquery()
        )
        return (
            select(AuditLog, User, role_name)
            .outerjoin(User, User.id == AuditLog.actorUserId)
            .where(*conditions)
        )


def _filter_conditions(query: AuditFilter) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    date_range = _date_range(query)
    if date_range is not None:
        conditions.extend([AuditLog.createdAt >= date_range[0], AuditLog.createdAt < date_range[1]])
    if query.result:
        conditions.append(AuditLog.result == AuditResult(query.result))
    if query.module is not AuditModuleKey.ALL:
        known = tuple(x for values in _MODULES.values() for x in values)
        conditions.append(
            AuditLog.module.not_in(known)
            if query.module is AuditModuleKey.OTHER
            else AuditLog.module.in_(_MODULES[query.module])
        )
    if query.action is not AuditActionGroup.ALL:
        known = tuple(pattern for values in _ACTION_PATTERNS.values() for pattern in values)
        expressions = [
            AuditLog.action.ilike(f"%{pattern}%")
            for pattern in (_ACTION_PATTERNS.get(query.action, ()))
        ]
        conditions.append(
            and_(*[~AuditLog.action.ilike(f"%{pattern}%") for pattern in known])
            if query.action is AuditActionGroup.OTHER
            else or_(*expressions)
        )
    keyword = query.keyword.strip() if query.keyword else ""
    if keyword:
        pattern = f"%{keyword}%"
        conditions.append(
            or_(
                AuditLog.action.ilike(pattern),
                AuditLog.module.ilike(pattern),
                AuditLog.resourceType.ilike(pattern),
                AuditLog.resourceId.ilike(pattern),
                AuditLog.traceId.ilike(pattern),
                User.displayName.ilike(pattern),
                User.username.ilike(pattern),
            )
        )
    return conditions


def _date_range(query: AuditFilter) -> tuple[datetime, datetime] | None:
    now = datetime.now(_TZ).date()
    if query.dateRange is AuditDateRange.CUSTOM:
        if query.startDate is None or query.endDate is None:
            raise ApiError(400, "BAD_REQUEST", "自定义日期范围必须同时提供开始日期和结束日期")
        start, end = query.startDate, query.endDate
    else:
        end, start = now + timedelta(days=1), now
        if query.dateRange is AuditDateRange.SEVEN_DAYS:
            start -= timedelta(days=6)
        if query.dateRange is AuditDateRange.THIRTY_DAYS:
            start -= timedelta(days=29)
    return datetime.combine(start, time.min, tzinfo=_TZ).astimezone(UTC), datetime.combine(
        end + timedelta(days=1) if query.dateRange is AuditDateRange.CUSTOM else end,
        time.min,
        tzinfo=_TZ,
    ).astimezone(UTC)


def _module_key(module: str) -> AuditModuleKey:
    for key, values in _MODULES.items():
        if module in values:
            return key
    return AuditModuleKey.OTHER


def _action_group(action: str) -> AuditActionGroup:
    for key, patterns in _ACTION_PATTERNS.items():
        if any(pattern in action for pattern in patterns):
            return key
    return AuditActionGroup.OTHER


def _map_item(row: Sequence[object]) -> AuditLogListItem:
    audit, user, actor_role = cast(tuple[AuditLog, User | None, str | None], tuple(row))
    module = _module_key(audit.module)
    group = _action_group(audit.action)
    created = audit.createdAt
    event_id = f"AUD-{created.astimezone(_TZ):%Y%m%d-%H%M%S}-{str(audit.id)[:6].upper()}"
    resource_label = f"{audit.resourceType}{(' / ' + audit.resourceId) if audit.resourceId else ''}"
    summary = f"{_ACTION_LABELS.get(group.value, audit.action)} · {audit.resourceType}"
    if audit.resourceId:
        summary += f"/{audit.resourceId}"
    return AuditLogListItem(
        id=audit.id,
        eventId=event_id,
        createdAt=created,
        actorName=user.displayName if user else "系统任务",
        actorAccount=user.username if user else None,
        actorRole=actor_role,
        module=module,
        moduleLabel=_MODULE_LABELS.get(module.value, module.value),
        rawModule=audit.module,
        action=audit.action,
        actionLabel=_ACTION_LABELS.get(group.value, audit.action),
        actionGroup=group,
        resourceType=audit.resourceType,
        resourceId=audit.resourceId,
        resourceLabel=resource_label,
        summary=summary,
        result=audit.result.value,
        traceId=audit.traceId,
        errorCode=audit.failureCode,
    )


def _render_export(
    items: list[AuditLogListItem], format: AuditExportFormat, reason: str
) -> ExportedAuditFile:
    del reason
    headers = [
        "时间",
        "事件编号",
        "模块",
        "操作",
        "操作人",
        "账号",
        "资源",
        "摘要",
        "结果",
        "Trace ID",
        "错误码",
    ]
    rows = [
        [
            item.createdAt.isoformat(),
            item.eventId,
            item.moduleLabel,
            item.actionLabel,
            item.actorName,
            item.actorAccount or "",
            item.resourceLabel,
            item.summary,
            item.result,
            item.traceId,
            item.errorCode or "",
        ]
        for item in items
    ]
    stamp = datetime.now(_TZ).strftime("%Y%m%d%H%M%S")
    if format is AuditExportFormat.CSV:
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerows([headers, *rows])
        return ExportedAuditFile(
            ("\ufeff" + output.getvalue()).encode(),
            f"审计日志_{stamp}.csv",
            "text/csv; charset=utf-8",
            len(items),
        )
    sheet_rows = "".join(
        f'<row r="{i}">'
        + "".join(f'<c t="inlineStr"><is><t>{_xml(value)}</t></is></c>' for value in row)
        + "</row>"
        for i, row in enumerate([headers, *rows], 1)
    )
    content = _xlsx(sheet_rows, len(headers), len(items) + 1)
    return ExportedAuditFile(
        content,
        f"审计日志_{stamp}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        len(items),
    )


def _xml(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _xlsx(rows: str, columns: int, row_count: int) -> bytes:
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',  # noqa: E501
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',  # noqa: E501
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="审计日志" sheetId="1" r:id="rId1"/></sheets></workbook>',  # noqa: E501
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',  # noqa: E501
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>',
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


__all__ = ["AuditQueryService", "ExportedAuditFile"]

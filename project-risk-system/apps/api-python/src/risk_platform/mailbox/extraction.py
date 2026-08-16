"""T026's fail-closed mail risk extraction and candidate publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.ai_providers.client import AiProviderClient, ProviderRequestError
from risk_platform.ai_providers.models import (
    AiCallLog,
    AiCallResult,
    AiCallScene,
    AiConnectionStatus,
    AiProviderConfig,
)
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.mailbox.connection import MailSourceUnavailable
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailMessage,
    MailMessageProjectMatch,
    MailProjectMatchType,
    MailRiskCandidate,
    MailRiskCandidateStatus,
    MailSourceHandoff,
    MailStageStatus,
    ProjectRiskLevel,
)
from risk_platform.mailbox.parse_worker import MailParseWorker
from risk_platform.mailbox.parsing import MailParseError, parse_mail
from risk_platform.mailbox.schemas import MailRiskCandidateResponse, MailRiskCandidateUpdateRequest
from risk_platform.projects.models import Project
from risk_platform.rbac.models import DataScopeType
from risk_platform.rbac.scopes import get_scoped_project
from risk_platform.reliability.models import DurableTask, DurableTaskKind
from risk_platform.risks.models import ProjectRiskLevel as RiskLevel
from risk_platform.risks.models import RiskCategory, RiskSourceType
from risk_platform.risks.service import RiskCreate, RisksService
from risk_platform.shared.crypto import LegacySecretFields, SecretCipher, SecretCryptoError
from risk_platform.shared.errors import ApiError
from risk_platform.weekly_reports.service import invalidate_candidate

SCHEMA_VERSION = "MAIL_PROVIDER_DERIVED_CONTENT_V2"
CATEGORY_OPTIONS_VERSION = "RISK_CATEGORY_OPTIONS_V1"
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_URL = re.compile(r"https?://\S+", re.I)
_NUMBER = re.compile(r"(?<!\d)\d{7,20}(?!\d)")
_SECRET = re.compile(
    r"(?i)(authorization|cookie|password|api[_ -]?key|token|secret|验证码)\s*[:=][^\n]*"
)
_PEM = re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.S)


@dataclass(frozen=True, slots=True)
class _CategoryOption:
    option_id: str
    category_id: UUID
    name: str
    description: str | None
    default_level: str | None


def _ai_extraction_disabled(mailbox: MailboxConfig | None) -> bool:
    """The mailbox switch skips Provider work without treating mail parsing as failed."""

    return mailbox is None or not mailbox.aiExtractionEnabled


def _safe_text(value: str) -> tuple[str, int]:
    """Return a bounded-safe provider segment and its irreversible replacements."""

    if any(ord(char) in range(0x7F, 0xA0) for char in value):
        return "", 0
    normalized = "".join(
        "" if ord(char) < 32 and char not in "\n\t" else char for char in value
    ).strip()
    normalized = " ".join(normalized.split())
    count = 0

    def replace(match: re.Match[str], replacement: str) -> str:
        nonlocal count
        count += 1
        return replacement

    normalized = _PEM.sub(lambda item: replace(item, "[SECRET]"), normalized)
    normalized = _SECRET.sub(lambda item: replace(item, "[SECRET]"), normalized)
    normalized = _EMAIL.sub(lambda item: replace(item, "[EMAIL]"), normalized)
    normalized = _URL.sub(lambda item: replace(item, "[URL]"), normalized)
    return _NUMBER.sub(lambda item: replace(item, "[NUMBER]"), normalized), count


def _provider_payload(
    body: str, attachments: list[str], source_date: str | None, options: list[_CategoryOption]
) -> dict[str, object]:
    body_limited = body[:6_000]
    cleaned_body, redactions = _safe_text(body_limited)
    parts = [f"BODY\n{cleaned_body}"] if cleaned_body else []
    included_attachments = 0
    for index, attachment in enumerate(attachments[:3], 1):
        cleaned, count = _safe_text(attachment[:2_000])
        redactions += count
        if cleaned:
            parts.append(f"ATTACHMENT_{index}\n{cleaned}")
            included_attachments += 1
    text = "\n".join(parts)
    truncated = len(body) > 6_000 or any(len(item) > 2_000 for item in attachments)
    if len(text) > 12_000:
        text = text[:12_000]
        truncated = True
    if not text:
        raise ValueError("DERIVED_CONTENT_EMPTY")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_date": source_date,
        "project_options": [],
        "analysis_text": text,
        "content_stats": {
            "body_chars": len(body_limited),
            "attachment_count": included_attachments,
            "total_chars": len(text),
            "redaction_count": redactions,
            "truncated": truncated,
        },
        "risk_category_options": [
            {
                "option_id": x.option_id,
                "name": x.name,
                "description": x.description,
                "default_level": x.default_level,
            }
            for x in options
        ],
    }


def _parse_output(
    raw: str, projects: dict[str, UUID], categories: dict[str, UUID]
) -> list[dict[str, object]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("PROVIDER_INVALID_OUTPUT") from None
    if (
        not isinstance(value, dict)
        or set(value) != {"risks"}
        or not isinstance(value["risks"], list)
    ):
        raise ValueError("PROVIDER_INVALID_OUTPUT")
    risks: list[dict[str, object]] = []
    for item in value["risks"]:
        required = {
            "project_option_id",
            "category_option_id",
            "level",
            "description",
            "evidence",
            "suggestion",
            "confidence",
        }
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("PROVIDER_INVALID_OUTPUT")
        project, category, level = (
            item["project_option_id"],
            item["category_option_id"],
            item["level"],
        )
        if not isinstance(project, str) or project not in projects:
            raise ValueError("PROVIDER_INVALID_OUTPUT")
        if not isinstance(category, str) or category not in categories:
            raise ValueError("PROVIDER_INVALID_OUTPUT")
        if not isinstance(level, str) or level not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("PROVIDER_INVALID_OUTPUT")
        fields = [item[x] for x in ("description", "evidence", "suggestion")]
        if not all(isinstance(x, str) and 2 <= len(x.strip()) <= 4000 for x in fields):
            raise ValueError("PROVIDER_INVALID_OUTPUT")
        if (
            isinstance(item["confidence"], bool)
            or not isinstance(item["confidence"], int)
            or not 0 <= item["confidence"] <= 100
        ):
            raise ValueError("PROVIDER_INVALID_OUTPUT")
        risks.append(
            {
                "project_id": projects[project],
                "category_id": categories[category],
                "level": level,
                "description": fields[0].strip(),
                "evidence": fields[1].strip(),
                "suggestion": fields[2].strip(),
                "confidence": item["confidence"],
            }
        )
    return risks


class CategoryMappingStale(RuntimeError):
    pass


class MailRiskExtractionWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: SecretCipher,
        client: AiProviderClient | None = None,
        parser: MailParseWorker | None = None,
    ) -> None:
        self._sessions, self._cipher, self._client = (
            session_factory,
            cipher,
            client or AiProviderClient(),
        )
        self._parser = parser or MailParseWorker(session_factory, cipher)

    async def handle(self, payload: Mapping[str, object]) -> None:
        mailbox_id, uid_validity, imap_uid = (
            UUID(str(payload["mailbox_config_id"])),
            int(str(payload["uid_validity"])),
            int(str(payload["imap_uid"])),
        )
        try:
            source, fallback = await self._parser._refetch(mailbox_id, uid_validity, imap_uid)
            parsed = parse_mail(source, fallback)
            source_date = (
                parsed.sent_at.date().isoformat() if hasattr(parsed.sent_at, "date") else None
            )
            await self._extract(
                mailbox_id,
                uid_validity,
                imap_uid,
                parsed.body_text,
                parsed.attachment_texts,
                source_date,
            )
        except ProviderRequestError as error:
            if error.retryable and await self._exhausted(mailbox_id, uid_validity, imap_uid):
                await self._stage(
                    mailbox_id,
                    uid_validity,
                    imap_uid,
                    MailStageStatus.PERMANENT_FAILURE,
                    "PROVIDER_RETRY_EXHAUSTED",
                )
                return
            await self._stage(
                mailbox_id,
                uid_validity,
                imap_uid,
                MailStageStatus.RETRYABLE_FAILURE
                if error.retryable
                else MailStageStatus.PERMANENT_FAILURE,
                error.code,
            )
            if error.retryable:
                raise
        except CategoryMappingStale:
            if await self._exhausted(mailbox_id, uid_validity, imap_uid):
                await self._stage(
                    mailbox_id,
                    uid_validity,
                    imap_uid,
                    MailStageStatus.PERMANENT_FAILURE,
                    "PROVIDER_RETRY_EXHAUSTED",
                )
                return
            await self._stage(
                mailbox_id,
                uid_validity,
                imap_uid,
                MailStageStatus.RETRYABLE_FAILURE,
                "CATEGORY_MAPPING_STALE",
            )
            raise
        except ValueError as error:
            await self._stage(
                mailbox_id, uid_validity, imap_uid, MailStageStatus.PERMANENT_FAILURE, str(error)
            )
        except MailParseError as error:
            await self._stage(
                mailbox_id, uid_validity, imap_uid, MailStageStatus.PERMANENT_FAILURE, error.code
            )
        except MailSourceUnavailable as error:
            await self._stage(
                mailbox_id, uid_validity, imap_uid, MailStageStatus.PERMANENT_FAILURE, str(error)
            )
        except Exception:
            if await self._exhausted(mailbox_id, uid_validity, imap_uid):
                await self._stage(
                    mailbox_id,
                    uid_validity,
                    imap_uid,
                    MailStageStatus.PERMANENT_FAILURE,
                    "PROVIDER_RETRY_EXHAUSTED",
                )
                return
            await self._stage(
                mailbox_id,
                uid_validity,
                imap_uid,
                MailStageStatus.RETRYABLE_FAILURE,
                "MAIL_AI_RUNTIME_FAILURE",
            )
            raise

    async def _extract(
        self,
        mailbox_id: UUID,
        uid_validity: int,
        imap_uid: int,
        body: str,
        attachments: list[str],
        source_date: str | None,
    ) -> None:
        async with self._sessions() as session:
            handoff = await session.scalar(
                select(MailSourceHandoff).where(
                    MailSourceHandoff.mailboxConfigId == mailbox_id,
                    MailSourceHandoff.uidValidity == uid_validity,
                    MailSourceHandoff.imapUid == imap_uid,
                )
            )
            if handoff is not None and handoff.aiReviewStatus is MailStageStatus.SUCCEEDED:
                return
            message = await session.scalar(
                select(MailMessage).where(
                    MailMessage.mailboxConfigId == mailbox_id,
                    MailMessage.uidValidity == uid_validity,
                    MailMessage.imapUid == imap_uid,
                )
            )
            mailbox = await session.get(MailboxConfig, mailbox_id)
            if _ai_extraction_disabled(mailbox):
                await self._stage(
                    mailbox_id,
                    uid_validity,
                    imap_uid,
                    MailStageStatus.SUCCEEDED,
                    "AI_EXTRACTION_DISABLED",
                )
                return
            provider = await session.scalar(
                select(AiProviderConfig)
                .where(
                    AiProviderConfig.enabled.is_(True),
                    AiProviderConfig.lastTestStatus == AiConnectionStatus.HEALTHY,
                )
                .order_by(AiProviderConfig.isDefault.desc(), AiProviderConfig.priority)
            )
            categories = (
                await session.scalars(
                    select(RiskCategory)
                    .where(RiskCategory.isActive.is_(True))
                    .order_by(RiskCategory.sortOrder, RiskCategory.code, RiskCategory.id)
                )
            ).all()
            matches = (
                []
                if message is None
                else (
                    await session.scalars(
                        select(MailMessageProjectMatch).where(
                            MailMessageProjectMatch.messageId == message.id
                        )
                    )
                ).all()
            )
        if message is None or not matches:
            raise ValueError("NO_MATCHED_PROJECT")
        if provider is None:
            raise ValueError("PROVIDER_UNAVAILABLE")
        if not categories:
            raise ValueError("NO_ACTIVE_RISK_CATEGORY")
        options = [
            _CategoryOption(
                f"C{i}",
                x.id,
                x.name,
                x.description,
                x.defaultLevel.value if x.defaultLevel else None,
            )
            for i, x in enumerate(categories, 1)
        ]
        project_map = {f"P{i}": item.projectId for i, item in enumerate(matches, 1)}
        request = _provider_payload(body, attachments, source_date, options)
        request["project_options"] = list(project_map)
        try:
            key = self._cipher.decrypt_legacy(
                LegacySecretFields(
                    provider.encryptedApiKey, provider.keyIv, provider.keyAuthTag, "v1"
                )
            )
        except SecretCryptoError:
            raise ValueError("PROVIDER_UNAVAILABLE") from None
        raw, usage, duration, trace = await self._call_provider(provider, key, request)
        try:
            risks = _parse_output(raw, project_map, {x.option_id: x.category_id for x in options})
        except ValueError:
            await self._log_call(provider, trace, "PROVIDER_INVALID_OUTPUT")
            raise
        await self._log_call(provider, trace, None, usage=usage, duration=duration)
        async with transaction(self._sessions) as session:
            handoff = await session.scalar(
                select(MailSourceHandoff)
                .where(
                    MailSourceHandoff.mailboxConfigId == mailbox_id,
                    MailSourceHandoff.uidValidity == uid_validity,
                    MailSourceHandoff.imapUid == imap_uid,
                )
                .with_for_update()
            )
            if handoff is None:
                return
            if handoff.aiReviewStatus is MailStageStatus.SUCCEEDED:
                return
            for risk in risks:
                category = await session.get(RiskCategory, risk["category_id"])
                if category is None or not category.isActive:
                    raise CategoryMappingStale
                session.add(
                    MailRiskCandidate(
                        messageId=message.id,
                        projectId=risk["project_id"],
                        categoryId=risk["category_id"],
                        level=ProjectRiskLevel(str(risk["level"])),
                        description=str(risk["description"]),
                        evidence=str(risk["evidence"]),
                        suggestion=str(risk["suggestion"]),
                        confidence=int(cast(int, risk["confidence"])),
                    )
                )
            handoff.aiReviewStatus, handoff.failureCode, handoff.failureSummary = (
                MailStageStatus.SUCCEEDED,
                None,
                None,
            )

    async def _stage(
        self, mailbox_id: UUID, uid_validity: int, imap_uid: int, status: MailStageStatus, code: str
    ) -> None:
        async with transaction(self._sessions) as session:
            handoff = await session.scalar(
                select(MailSourceHandoff)
                .where(
                    MailSourceHandoff.mailboxConfigId == mailbox_id,
                    MailSourceHandoff.uidValidity == uid_validity,
                    MailSourceHandoff.imapUid == imap_uid,
                )
                .with_for_update()
            )
            if handoff is not None:
                handoff.aiReviewStatus, handoff.failureCode, handoff.failureSummary = (
                    status,
                    code,
                    code,
                )

    async def _call_provider(
        self, provider: AiProviderConfig, key: str, payload: dict[str, object]
    ) -> tuple[str, dict[str, int], int, UUID]:
        """Apply only the configured in-attempt transient retry budget."""

        last_error: ProviderRequestError | None = None
        for attempt in range(provider.retryCount + 1):
            trace = uuid4()
            try:
                raw, usage, duration = await self._client.extract_risks(
                    provider.endpoint,
                    provider.model,
                    key,
                    provider.timeoutSeconds,
                    payload,
                    provider.protocol,
                )
            except ProviderRequestError as error:
                await self._log_call(provider, trace, error.code)
                last_error = error
                if not error.retryable or attempt >= provider.retryCount:
                    raise
                await asyncio.sleep(min(2**attempt, 4))
                continue
            return raw, usage, duration, trace
        assert last_error is not None
        raise last_error

    async def _log_call(
        self,
        provider: AiProviderConfig,
        trace: UUID,
        code: str | None,
        *,
        usage: dict[str, int] | None = None,
        duration: int = 0,
    ) -> None:
        async with transaction(self._sessions) as session:
            values = usage or {"input": 0, "output": 0, "total": 0}
            session.add(
                AiCallLog(
                    traceId=str(trace),
                    providerId=provider.id,
                    providerNameSnapshot=provider.name,
                    modelSnapshot=provider.model,
                    scene=AiCallScene.RISK_EXTRACTION,
                    inputTokens=values["input"],
                    outputTokens=values["output"],
                    totalTokens=values["total"],
                    durationMs=duration,
                    result=AiCallResult.FAILURE if code else AiCallResult.SUCCESS,
                    errorCode=code,
                    errorSummary=code,
                )
            )

    async def _exhausted(self, mailbox_id: UUID, uid_validity: int, imap_uid: int) -> bool:
        async with self._sessions() as session:
            task = await session.scalar(
                select(DurableTask).where(
                    DurableTask.kind == DurableTaskKind.MAIL_AI_REVIEW_PUBLISH,
                    DurableTask.idempotencyKey == f"mail-ai:{mailbox_id}:{uid_validity}:{imap_uid}",
                )
            )
            return task is None or task.attemptCount >= task.maxAttempts


class MailRiskCandidateService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def confirm(self, candidate_id: UUID, actor_id: UUID, trace_id: UUID) -> UUID:
        async with transaction(self._sessions) as session:
            candidate = await session.scalar(
                select(MailRiskCandidate)
                .where(MailRiskCandidate.id == candidate_id)
                .with_for_update()
            )
            if candidate is None:
                raise ApiError(404, "NOT_FOUND", "风险线索不存在")
            if candidate.status is MailRiskCandidateStatus.CONFIRMED and candidate.confirmedRiskId:
                return candidate.confirmedRiskId
            if candidate.status is not MailRiskCandidateStatus.PENDING:
                raise ApiError(400, "BAD_REQUEST", "该风险线索已经处理")
            category = await session.get(RiskCategory, candidate.categoryId)
            if category is None or not category.isActive:
                raise ApiError(400, "BAD_REQUEST", "风险分类无效")
            command = RiskCreate(
                project_id=candidate.projectId,
                category_id=candidate.categoryId,
                title=candidate.description[:250],
                description=candidate.description,
                evidence=candidate.evidence,
                suggestion=candidate.suggestion,
                level=RiskLevel(candidate.level.value),
                source_type=RiskSourceType.MAIL_AI,
                source_ref_id=candidate.id,
                dedupe_fingerprint=hashlib.sha256(f"MAIL_AI:{candidate.id}".encode()).hexdigest(),
            )
            risk = await RisksService(self._sessions).create_in_session(
                session, command, actor_id=actor_id, trace_id=trace_id
            )
            (
                candidate.status,
                candidate.confirmedRiskId,
                candidate.reviewedById,
                candidate.reviewedAt,
            ) = MailRiskCandidateStatus.CONFIRMED, risk.id, actor_id, datetime.now(UTC)
            await invalidate_candidate(session, candidate)
            await self._audit_candidate(
                session,
                actor_id=actor_id,
                trace_id=trace_id,
                action="MAIL_RISK_CONFIRMED",
                candidate=candidate,
            )
            return risk.id

    async def update(
        self,
        candidate_id: UUID,
        payload: MailRiskCandidateUpdateRequest,
        identity: SessionIdentity,
        trace_id: UUID,
    ) -> MailRiskCandidateResponse:
        async with transaction(self._sessions) as session:
            candidate = await self._owned(session, candidate_id, identity, lock=True)
            self._pending(candidate)
            old_project_id = candidate.projectId
            project = await get_scoped_project(
                session,
                payload.projectId,
                UUID(identity.user.id),
                DataScopeType(identity.user.dataScope),
            )
            category = await session.scalar(
                select(RiskCategory).where(
                    RiskCategory.id == payload.categoryId, RiskCategory.isActive.is_(True)
                )
            )
            if project is None or category is None:
                raise ApiError(400, "BAD_REQUEST", "项目或风险分类无效")
            candidate.projectId, candidate.categoryId = project.id, category.id
            candidate.level = ProjectRiskLevel(payload.level)
            candidate.description, candidate.evidence, candidate.suggestion = (
                payload.description.strip(),
                payload.evidence.strip(),
                payload.suggestion.strip(),
            )
            candidate.reviewedById, candidate.reviewedAt = UUID(identity.user.id), datetime.now(UTC)
            match = await session.scalar(
                select(MailMessageProjectMatch).where(
                    MailMessageProjectMatch.messageId == candidate.messageId,
                    MailMessageProjectMatch.projectId == project.id,
                )
            )
            if match is None:
                session.add(
                    MailMessageProjectMatch(
                        messageId=candidate.messageId,
                        projectId=project.id,
                        matchType=MailProjectMatchType.MANUAL,
                        confidence=100,
                        matchedText=project.name,
                        confirmedById=UUID(identity.user.id),
                    )
                )
            await invalidate_candidate(session, candidate, old_project_id=old_project_id)
            await self._audit_candidate(
                session,
                actor_id=UUID(identity.user.id),
                trace_id=trace_id,
                action="MAIL_RISK_ADJUSTED",
                candidate=candidate,
            )
            return self._response(candidate, project.name, category.name)

    async def ignore(
        self, candidate_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> MailRiskCandidateResponse:
        async with transaction(self._sessions) as session:
            candidate = await self._owned(session, candidate_id, identity, lock=True)
            self._pending(candidate)
            candidate.status, candidate.reviewedById, candidate.reviewedAt = (
                MailRiskCandidateStatus.IGNORED,
                UUID(identity.user.id),
                datetime.now(UTC),
            )
            await self._audit_candidate(
                session,
                actor_id=UUID(identity.user.id),
                trace_id=trace_id,
                action="MAIL_RISK_IGNORED",
                candidate=candidate,
            )
            return await self._response_for(session, candidate)

    async def confirm_response(
        self, candidate_id: UUID, identity: SessionIdentity, trace_id: UUID
    ) -> MailRiskCandidateResponse:
        async with transaction(self._sessions) as session:
            candidate = await self._owned(session, candidate_id, identity, lock=True)
            if candidate.status is MailRiskCandidateStatus.CONFIRMED:
                return await self._response_for(session, candidate)
            self._pending(candidate)
            category = await session.get(RiskCategory, candidate.categoryId)
            if category is None or not category.isActive:
                raise ApiError(400, "BAD_REQUEST", "风险分类无效")
            command = RiskCreate(
                project_id=candidate.projectId,
                category_id=candidate.categoryId,
                title=candidate.description[:250],
                description=candidate.description,
                evidence=candidate.evidence,
                suggestion=candidate.suggestion,
                level=RiskLevel(candidate.level.value),
                source_type=RiskSourceType.MAIL_AI,
                source_ref_id=candidate.id,
                dedupe_fingerprint=hashlib.sha256(f"MAIL_AI:{candidate.id}".encode()).hexdigest(),
            )
            risk = await RisksService(self._sessions).create_in_session(
                session, command, actor_id=UUID(identity.user.id), trace_id=trace_id
            )
            candidate.status, candidate.confirmedRiskId = MailRiskCandidateStatus.CONFIRMED, risk.id
            candidate.reviewedById, candidate.reviewedAt = UUID(identity.user.id), datetime.now(UTC)
            await invalidate_candidate(session, candidate)
            await self._audit_candidate(
                session,
                actor_id=UUID(identity.user.id),
                trace_id=trace_id,
                action="MAIL_RISK_CONFIRMED",
                candidate=candidate,
            )
            return await self._response_for(session, candidate)

    @staticmethod
    async def _audit_candidate(
        session: AsyncSession,
        *,
        actor_id: UUID,
        trace_id: UUID,
        action: str,
        candidate: MailRiskCandidate,
    ) -> None:
        await AuditService(session).record_success(
            actor_id=actor_id,
            actor_type=AuditActorType.USER,
            module="MAILBOX",
            action=action,
            resource_type="MAIL_RISK_CANDIDATE",
            resource_id=str(candidate.id),
            trace_id=trace_id,
            project_id=candidate.projectId,
        )

    async def _owned(
        self, session: AsyncSession, candidate_id: UUID, identity: SessionIdentity, *, lock: bool
    ) -> MailRiskCandidate:
        statement = select(MailRiskCandidate).where(MailRiskCandidate.id == candidate_id)
        if lock:
            statement = statement.with_for_update()
        candidate = await session.scalar(statement)
        if (
            candidate is None
            or not await self._owns_mailbox(session, candidate, UUID(identity.user.id))
            or await get_scoped_project(
                session,
                candidate.projectId,
                UUID(identity.user.id),
                DataScopeType(identity.user.dataScope),
            )
            is None
        ):
            raise ApiError(404, "NOT_FOUND", "风险线索不存在")
        return candidate

    @staticmethod
    async def _owns_mailbox(
        session: AsyncSession, candidate: MailRiskCandidate, actor_id: UUID
    ) -> bool:
        owner = await session.scalar(
            select(MailboxConfig.userId)
            .join(MailMessage, MailMessage.mailboxConfigId == MailboxConfig.id)
            .where(MailMessage.id == candidate.messageId)
        )
        return owner == actor_id

    @staticmethod
    def _pending(candidate: MailRiskCandidate) -> None:
        if candidate.status is not MailRiskCandidateStatus.PENDING:
            raise ApiError(400, "BAD_REQUEST", "该风险线索已经处理")

    async def _response_for(
        self, session: AsyncSession, candidate: MailRiskCandidate
    ) -> MailRiskCandidateResponse:
        project, category = (
            await session.get(Project, candidate.projectId),
            await session.get(RiskCategory, candidate.categoryId),
        )
        assert project is not None and category is not None
        return self._response(candidate, project.name, category.name)

    @staticmethod
    def _response(
        candidate: MailRiskCandidate, project_name: str, category_name: str
    ) -> MailRiskCandidateResponse:
        labels = {"HIGH": "高风险", "MEDIUM": "中风险", "LOW": "低风险"}
        return MailRiskCandidateResponse(
            id=candidate.id,
            projectId=candidate.projectId,
            projectName=project_name,
            categoryId=candidate.categoryId,
            categoryName=category_name,
            level=cast(Literal["HIGH", "MEDIUM", "LOW"], candidate.level.value),
            levelLabel=labels[candidate.level.value],
            description=candidate.description,
            evidence=candidate.evidence,
            suggestion=candidate.suggestion,
            confidence=candidate.confidence,
            status=candidate.status.value,
            confirmedRiskId=candidate.confirmedRiskId,
            reviewedAt=candidate.reviewedAt.isoformat() if candidate.reviewedAt else None,
        )


__all__ = [
    "MailRiskCandidateService",
    "MailRiskExtractionWorker",
    "_ai_extraction_disabled",
    "_parse_output",
    "_provider_payload",
]

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User
from risk_platform.audit.models import AuditActorType, AuditResult
from risk_platform.audit.service import AuditEvent, AuditService
from risk_platform.auth.service import SessionIdentity
from risk_platform.db import transaction
from risk_platform.mailbox.connection import MailboxConnection
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailboxConnectionStatus,
    MailboxEncryption,
    MailboxProvider,
    MailSyncBatch,
    MailSyncStatus,
    MailSyncTrigger,
)
from risk_platform.mailbox.schemas import (
    MailboxConfigRequest,
    MailboxConnectionTestResult,
    MailboxOverview,
    MailSyncBatchResponse,
)
from risk_platform.reliability.core import enqueue_task
from risk_platform.reliability.models import DurableTaskKind
from risk_platform.shared.crypto import (
    LegacySecretFields,
    SecretCipher,
    SecretCryptoError,
)
from risk_platform.shared.errors import ApiError

_INTERVAL_MINUTES = 30


class MailboxService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: SecretCipher,
        connection: MailboxConnection | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._connection = connection or MailboxConnection()

    @staticmethod
    def _require_owner(identity: SessionIdentity) -> UUID:
        if "RISK_ADMIN" not in identity.user.roleCodes:
            raise ApiError(403, "FORBIDDEN", "仅风险管理员可以配置或同步本人邮箱")
        return UUID(identity.user.id)

    async def overview(self, identity: SessionIdentity) -> MailboxOverview:
        user_id = self._require_owner(identity)
        async with self._session_factory() as session:
            config = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == user_id)
            )
            email = await session.scalar(select(User.email).where(User.id == user_id))
            if config is None:
                return self._empty(str(email or ""))
            totals = await session.execute(
                select(
                    func.coalesce(func.sum(MailSyncBatch.successCount), 0),
                    func.coalesce(func.sum(MailSyncBatch.riskCandidateCount), 0),
                ).where(
                    MailSyncBatch.mailboxConfigId == config.id,
                    MailSyncBatch.status.in_([MailSyncStatus.SUCCESS, MailSyncStatus.PARTIAL]),
                )
            )
            synced, candidates = totals.one()
            return self._map(config, int(synced), int(candidates))

    async def save(
        self, payload: MailboxConfigRequest, identity: SessionIdentity, trace_id: UUID
    ) -> MailboxOverview:
        user_id = self._require_owner(identity)
        async with transaction(self._session_factory) as session:
            existing = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == user_id)
            )
            if existing is None and not payload.authCode:
                raise ApiError(400, "BAD_REQUEST", "首次配置邮箱时必须填写邮箱授权码")
            normalized = self._normalize(payload)
            auth_code = payload.authCode.strip() if payload.authCode else None
            credential = self._cipher.encrypt_legacy(auth_code) if auth_code else None
            legacy_fields = (
                self._legacy_fields(credential, auth_code) if credential and auth_code else None
            )
            if existing is None:
                assert legacy_fields is not None
                config = MailboxConfig(userId=user_id, **normalized, **legacy_fields)
                session.add(config)
            else:
                changed = self._connection_changed(existing, normalized, credential)
                for key, value in normalized.items():
                    setattr(existing, key, value)
                if legacy_fields is not None:
                    for key, value in legacy_fields.items():
                        setattr(existing, key, value)
                if changed:
                    existing.connectionStatus = MailboxConnectionStatus.UNTESTED
                    existing.lastTestAt = None
                    existing.lastTestLatencyMs = None
                    existing.lastTestErrorCode = None
                    existing.lastTestErrorSummary = None
                config = existing
            await session.flush()
            await AuditService(session).record(
                AuditEvent(
                    actor_id=user_id,
                    actor_type=AuditActorType.USER,
                    module="MAILBOX",
                    action="MAILBOX_CONFIG_SAVED",
                    resource_type="MAILBOX_CONFIG",
                    resource_id=str(config.id),
                    trace_id=trace_id,
                    result=AuditResult.SUCCESS,
                )
            )
        return await self.overview(identity)

    async def test(
        self, payload: MailboxConfigRequest, identity: SessionIdentity, trace_id: UUID
    ) -> MailboxConnectionTestResult:
        user_id = self._require_owner(identity)
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == user_id)
            )
            auth_code = payload.authCode
            if not auth_code and existing is not None:
                auth_code = self._decrypt(existing)
        if not auth_code:
            raise ApiError(400, "BAD_REQUEST", "请填写邮箱授权码后再测试连接")
        normalized = self._normalize(payload)
        outcome = await self._connection.test(
            email=cast(str, normalized["email"]),
            auth_code=auth_code,
            host=cast(str, normalized["imapHost"]),
            port=cast(int, normalized["imapPort"]),
            encryption=cast(str, normalized["encryption"]),
            folder=cast(str, normalized["folder"]),
        )
        tested_at = datetime.now(UTC)
        same_saved = (
            existing is not None
            and not payload.authCode
            and all(
                getattr(existing, key) == value
                for key, value in normalized.items()
                if key in {"email", "imapHost", "imapPort", "encryption", "folder"}
            )
        )
        async with transaction(self._session_factory) as session:
            if same_saved:
                assert existing is not None
                config = await session.get(MailboxConfig, existing.id)
                assert config is not None
                config.connectionStatus = (
                    MailboxConnectionStatus.HEALTHY
                    if outcome.success
                    else MailboxConnectionStatus.FAILED
                )
                config.lastTestAt = tested_at
                config.lastTestLatencyMs = outcome.latency_ms
                config.lastTestErrorCode = outcome.error_code
                config.lastTestErrorSummary = outcome.error_summary
            await AuditService(session).record(
                AuditEvent(
                    actor_id=user_id,
                    actor_type=AuditActorType.USER,
                    module="MAILBOX",
                    action="MAILBOX_CONNECTION_TESTED",
                    resource_type="MAILBOX_CONFIG",
                    resource_id=str(existing.id if existing else user_id),
                    trace_id=trace_id,
                    result=AuditResult.SUCCESS if outcome.success else AuditResult.FAILURE,
                    failure_code=outcome.error_code,
                )
            )
        return MailboxConnectionTestResult(
            success=outcome.success,
            status="HEALTHY" if outcome.success else "FAILED",
            latencyMs=outcome.latency_ms,
            testedAt=tested_at.isoformat(),
            folder=cast(str, normalized["folder"]),
            errorCode=outcome.error_code,
            errorSummary=outcome.error_summary,
        )

    async def set_status(
        self, enabled: bool, identity: SessionIdentity, trace_id: UUID
    ) -> MailboxOverview:
        user_id = self._require_owner(identity)
        async with transaction(self._session_factory) as session:
            config = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == user_id)
            )
            if config is None:
                raise ApiError(404, "NOT_FOUND", "尚未保存个人邮箱配置")
            if config.enabled != enabled:
                config.enabled = enabled
                await AuditService(session).record_success(
                    actor_id=user_id,
                    actor_type=AuditActorType.USER,
                    module="MAILBOX",
                    action="MAILBOX_ENABLED" if enabled else "MAILBOX_DISABLED",
                    resource_type="MAILBOX_CONFIG",
                    resource_id=str(config.id),
                    trace_id=trace_id,
                )
        return await self.overview(identity)

    async def enqueue_sync(
        self, identity: SessionIdentity, trace_id: UUID
    ) -> MailSyncBatchResponse:
        user_id = self._require_owner(identity)
        async with transaction(self._session_factory) as session:
            config = await session.scalar(
                select(MailboxConfig).where(MailboxConfig.userId == user_id)
            )
            if config is None:
                raise ApiError(404, "NOT_FOUND", "尚未保存个人邮箱配置")
            if not config.enabled:
                raise ApiError(400, "BAD_REQUEST", "邮箱已停用，请先恢复邮箱")  # noqa: RUF001
            if config.connectionStatus != MailboxConnectionStatus.HEALTHY:
                raise ApiError(400, "BAD_REQUEST", "请先完成邮箱连接测试")
            running = await session.scalar(
                select(MailSyncBatch).where(
                    MailSyncBatch.mailboxConfigId == config.id,
                    MailSyncBatch.status.in_([MailSyncStatus.QUEUED, MailSyncStatus.RUNNING]),
                )
            )
            if running is not None:
                return self._batch_response(running)
            code = f"MAIL-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:8].upper()}"
            task = await enqueue_task(
                session,
                DurableTaskKind.MAILBOX_SYNC,
                f"mailbox:{config.id}:manual:{code}",
                {"mailbox_config_id": str(config.id), "operator_user_id": str(user_id)},
            )
            batch = MailSyncBatch(
                taskId=task.id,
                code=code,
                mailboxConfigId=config.id,
                trigger=MailSyncTrigger.MANUAL,
                operatorUserId=user_id,
            )
            session.add(batch)
            await session.flush()
            await AuditService(session).record_success(
                actor_id=user_id,
                actor_type=AuditActorType.USER,
                module="MAILBOX",
                action="MAILBOX_SYNC_REQUESTED",
                resource_type="MAIL_SYNC_BATCH",
                resource_id=str(batch.id),
                trace_id=trace_id,
            )
            return self._batch_response(batch)

    def _decrypt(self, config: MailboxConfig) -> str:
        try:
            return self._cipher.decrypt_legacy(self._legacy_secret(config))
        except SecretCryptoError:
            raise ApiError(500, "SECRET_DECRYPTION_FAILED", "邮箱授权码解密失败") from None

    @staticmethod
    def _legacy_secret(config: MailboxConfig) -> LegacySecretFields:
        return LegacySecretFields(
            config.encryptedAuthCode, config.authCodeIv, config.authCodeTag, "v1"
        )

    @staticmethod
    def _legacy_fields(credential: LegacySecretFields, plaintext: str) -> dict[str, str]:
        return {
            "encryptedAuthCode": credential.ciphertext,
            "authCodeIv": credential.iv,
            "authCodeTag": credential.auth_tag,
            "authCodeLast4": plaintext[-4:],
        }

    @staticmethod
    def _normalize(payload: MailboxConfigRequest) -> dict[str, object]:
        provider = MailboxProvider(payload.provider)
        return {
            "provider": provider,
            "email": payload.email,
            "imapHost": "imap.qq.com"
            if provider == MailboxProvider.QQ
            else payload.imapHost.lower(),
            "imapPort": 993 if provider == MailboxProvider.QQ else payload.imapPort,
            "encryption": MailboxEncryption.SSL
            if provider == MailboxProvider.QQ
            else payload.encryption,
            "folder": payload.folder,
            "subjectKeywords": payload.subjectKeywords,
            "senderRule": (payload.senderRule or "").strip() or None,
            "initialSyncWeeks": payload.initialSyncWeeks,
            "readAttachments": payload.readAttachments,
            "aiExtractionEnabled": payload.aiExtractionEnabled,
        }

    @staticmethod
    def _connection_changed(
        existing: MailboxConfig,
        normalized: dict[str, object],
        credential: LegacySecretFields | None,
    ) -> bool:
        return credential is not None or any(
            getattr(existing, key) != value
            for key, value in normalized.items()
            if key in {"email", "imapHost", "imapPort", "encryption", "folder"}
        )

    @staticmethod
    def _empty(email: str) -> MailboxOverview:
        return MailboxOverview(
            configured=False,
            provider="QQ",
            email=email,
            maskedEmail=None,
            hasAuthCode=False,
            authCodeLast4=None,
            imapHost="imap.qq.com",
            imapPort=993,
            encryption="SSL",
            folder="INBOX",
            subjectKeywords=["项目周报", "工作周报", "风险周报"],
            senderRule="",
            initialSyncWeeks=4,
            readAttachments=True,
            aiExtractionEnabled=True,
            enabled=False,
            autoSyncEnabled=True,
            autoSyncIntervalMinutes=_INTERVAL_MINUTES,
            connectionStatus="UNTESTED",
            lastTestAt=None,
            lastTestLatencyMs=None,
            lastTestErrorCode=None,
            lastTestErrorSummary=None,
            lastSyncAt=None,
            lastSyncStatus=None,
            lastSyncNewCount=0,
            lastSyncSuccessCount=0,
            lastSyncRiskCandidateCount=0,
            lastSyncFailedCount=0,
            nextSyncAt=None,
            uidCursor=None,
            totalSyncedCount=0,
            totalRiskCandidateCount=0,
            updatedAt=None,
        )

    @staticmethod
    def _map(config: MailboxConfig, synced: int, candidates: int) -> MailboxOverview:
        base = config.lastSyncAt or config.lastTestAt or config.updatedAt
        subject_keywords = config.subjectKeywords
        if not isinstance(subject_keywords, list):
            subject_keywords = []
        return MailboxOverview(
            configured=True,
            provider=config.provider.value,
            email=config.email,
            maskedEmail=f"{config.email[:2]}***@{config.email.split('@')[-1]}",
            hasAuthCode=True,
            authCodeLast4=config.authCodeLast4,
            imapHost=config.imapHost,
            imapPort=config.imapPort,
            encryption=config.encryption.value,
            folder=config.folder,
            subjectKeywords=[str(v) for v in subject_keywords if isinstance(v, str)],
            senderRule=config.senderRule or "",
            initialSyncWeeks=cast("Literal[1, 4, 8, 12]", config.initialSyncWeeks),
            readAttachments=config.readAttachments,
            aiExtractionEnabled=config.aiExtractionEnabled,
            enabled=config.enabled,
            autoSyncEnabled=config.autoSyncEnabled,
            autoSyncIntervalMinutes=_INTERVAL_MINUTES,
            connectionStatus=config.connectionStatus.value,
            lastTestAt=config.lastTestAt.isoformat() if config.lastTestAt else None,
            lastTestLatencyMs=config.lastTestLatencyMs,
            lastTestErrorCode=config.lastTestErrorCode,
            lastTestErrorSummary=config.lastTestErrorSummary,
            lastSyncAt=config.lastSyncAt.isoformat() if config.lastSyncAt else None,
            lastSyncStatus=config.lastSyncStatus.value if config.lastSyncStatus else None,
            lastSyncNewCount=config.lastSyncNewCount,
            lastSyncSuccessCount=config.lastSyncSuccessCount,
            lastSyncRiskCandidateCount=config.lastSyncRiskCandidateCount,
            lastSyncFailedCount=config.lastSyncFailedCount,
            nextSyncAt=(base + timedelta(minutes=_INTERVAL_MINUTES)).isoformat()
            if config.enabled and config.autoSyncEnabled
            else None,
            uidCursor=str(config.uidCursor) if config.uidCursor is not None else None,
            totalSyncedCount=synced,
            totalRiskCandidateCount=candidates,
            updatedAt=config.updatedAt.isoformat(),
        )

    @staticmethod
    def _batch_response(batch: MailSyncBatch) -> MailSyncBatchResponse:
        return MailSyncBatchResponse(
            id=str(batch.id),
            code=batch.code,
            trigger=batch.trigger.value,
            status=batch.status.value,
            createdAt=batch.createdAt.isoformat()
            if batch.createdAt
            else datetime.now(UTC).isoformat(),
            startedAt=batch.startedAt.isoformat() if batch.startedAt else None,
            finishedAt=batch.finishedAt.isoformat() if batch.finishedAt else None,
            discoveredCount=batch.discoveredCount,
            handedOffCount=batch.handedOffCount,
            duplicateCount=max(batch.discoveredCount - batch.handedOffCount, 0),
            downstreamPendingCount=batch.downstreamPendingCount,
            scannedCount=batch.scannedCount,
            newCount=batch.newCount,
            successCount=batch.successCount,
            skippedCount=batch.skippedCount,
            failedCount=batch.failedCount,
            riskCandidateCount=batch.riskCandidateCount,
            errorSummary=batch.errorSummary,
        )

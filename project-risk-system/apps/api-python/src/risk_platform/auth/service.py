"""Authentication application service with transactional lockout and revocation."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from argon2.low_level import Type
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.admin.models import User, UserStatus
from risk_platform.audit.models import AuditActorType
from risk_platform.audit.service import AuditService
from risk_platform.auth.policy import password_policy_violations
from risk_platform.auth.repository import AuthRepository, UserAccess
from risk_platform.auth.schemas import AuthenticatedUser, DataScope, RoleCode
from risk_platform.config import Settings
from risk_platform.db import transaction
from risk_platform.shared.errors import ApiError

_MINIMUM_SESSION_KEY_BYTES = 32

_ROLE_CODES = frozenset({"SYSTEM_ADMIN", "RISK_ADMIN", "PROJECT_MANAGER", "VIEWER_AUDITOR"})
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$Ymaiuxk/nFrzRCfJIhnyQw$"
    "9zDMUyQoxh8VW2oGQYTK+hkufa/IySoq1KzMQszVWZc"
)


@dataclass(frozen=True, slots=True)
class AuthConfiguration:
    session_hours: int = 8
    login_max_attempts: int = 5
    login_lock_minutes: int = 30
    password_min_length: int = 12

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthConfiguration:
        source = os.environ if environ is None else environ
        fields = {
            "SESSION_TTL_HOURS": "session_hours",
            "LOGIN_MAX_ATTEMPTS": "login_max_attempts",
            "LOGIN_LOCK_MINUTES": "login_lock_minutes",
            "PASSWORD_MIN_LENGTH": "password_min_length",
        }
        values: dict[str, int] = {}
        for environment_name, field_name in fields.items():
            if environment_name not in source:
                continue
            try:
                values[field_name] = int(source[environment_name])
            except ValueError:
                raise ValueError(f"invalid {environment_name}") from None
        try:
            return cls(**values)
        except ValueError as exc:
            invalid_field = str(exc).removeprefix("invalid ")
            environment_name = next(
                (name for name, field in fields.items() if field == invalid_field),
                "AUTH_CONFIGURATION",
            )
            raise ValueError(f"invalid {environment_name}") from None

    def __post_init__(self) -> None:
        limits = (
            ("session_hours", self.session_hours, 1, 168),
            ("login_max_attempts", self.login_max_attempts, 3, 20),
            ("login_lock_minutes", self.login_lock_minutes, 1, 1_440),
            ("password_min_length", self.password_min_length, 12, 128),
        )
        for name, value, minimum, maximum in limits:
            if isinstance(value, bool) or not minimum <= value <= maximum:
                raise ValueError(f"invalid {name}")


class SessionKeyError(RuntimeError):
    """Stable session-key load error which never exposes paths or key material."""


@dataclass(frozen=True, slots=True)
class SessionKey:
    _value: bytes

    def __post_init__(self) -> None:
        if len(self._value) < _MINIMUM_SESSION_KEY_BYTES:
            raise SessionKeyError("SESSION_KEY_TOO_SHORT")
        object.__setattr__(self, "_value", bytes(self._value))

    @classmethod
    def from_file(cls, path: Path) -> SessionKey:
        try:
            value = path.read_bytes().strip()
        except OSError:
            raise SessionKeyError("SESSION_KEY_LOAD_FAILED") from None
        return cls(value)

    @classmethod
    def from_settings(cls, settings: Settings) -> SessionKey:
        if settings.session_secret_file is None:
            raise SessionKeyError("SESSION_KEY_FILE_REQUIRED")
        return cls.from_file(settings.session_secret_file)

    def digest(self, purpose: bytes, value: str) -> str:
        return hmac.new(
            self._value,
            purpose + b"\0" + value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class RequestContext:
    client_ip: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    session_id: UUID
    expires_at: datetime
    user: AuthenticatedUser


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: str
    expires_at: datetime
    user: AuthenticatedUser


@dataclass(frozen=True, slots=True)
class RuntimeSecurity:
    session_hours: int
    login_max_attempts: int
    login_lock_minutes: int
    password_min_length: int


class AuthService:
    """Own authentication transactions; callers never receive raw persisted tokens."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        session_key: SessionKey,
        configuration: AuthConfiguration | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._session_key = session_key
        self._configuration = configuration or AuthConfiguration()
        self._password_hasher = PasswordHasher(type=Type.ID)

    @classmethod
    def from_settings(
        cls,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> AuthService:
        return cls(
            session_factory,
            SessionKey.from_settings(settings),
            AuthConfiguration.from_env(),
        )

    async def login(
        self,
        *,
        username: str,
        password: str,
        context: RequestContext,
        trace_id: UUID,
    ) -> LoginResult:
        normalized_username = username.strip().casefold()
        async with transaction(self._session_factory) as session:
            outcome = await self._login_transaction(
                session,
                normalized_username,
                password,
                context,
                trace_id,
            )
        if isinstance(outcome, ApiError):
            raise outcome
        return outcome

    async def _login_transaction(
        self,
        session: AsyncSession,
        username: str,
        password: str,
        context: RequestContext,
        trace_id: UUID,
    ) -> LoginResult | ApiError:
        repository = AuthRepository(session)
        audit = AuditService(session)
        user = await repository.user_by_username(username, for_update=True)
        now = self._utc_now_millis()

        if user is None:
            await self._verify_password(_DUMMY_PASSWORD_HASH, password)
            await self._audit_failure(
                audit,
                actor_id=None,
                resource_id=None,
                trace_id=trace_id,
                action="AUTH_LOGIN_FAILED",
                failure_code="INVALID_CREDENTIALS",
            )
            return self._unauthorized("账号或密码错误")

        resource_id = str(user.id)
        if user.status is UserStatus.DISABLED:
            await self._audit_failure(
                audit,
                actor_id=None,
                resource_id=resource_id,
                trace_id=trace_id,
                action="AUTH_LOGIN_FAILED",
                failure_code="ACCOUNT_DISABLED",
            )
            return self._unauthorized("账号或密码错误")

        if user.status is UserStatus.LOCKED and user.lockedUntil and user.lockedUntil > now:
            await self._audit_failure(
                audit,
                actor_id=None,
                resource_id=resource_id,
                trace_id=trace_id,
                action="AUTH_LOGIN_FAILED",
                failure_code="ACCOUNT_LOCKED",
            )
            retry_at = self.format_expiration(user.lockedUntil)
            return ApiError(
                423,
                "ACCOUNT_LOCKED",
                f"账号已锁定，请于 {retry_at} 后重试",  # noqa: RUF001
            )

        if user.status is UserStatus.LOCKED:
            user.status = UserStatus.ACTIVE
            user.failedLoginCount = 0
            user.lockedUntil = None

        if not await self._verify_password(user.passwordHash, password):
            security = await self._runtime_security(repository)
            user.failedLoginCount += 1
            should_lock = user.failedLoginCount >= security.login_max_attempts
            user.status = UserStatus.LOCKED if should_lock else UserStatus.ACTIVE
            user.lockedUntil = (
                now + timedelta(minutes=security.login_lock_minutes) if should_lock else None
            )
            await self._audit_failure(
                audit,
                actor_id=None,
                resource_id=resource_id,
                trace_id=trace_id,
                action="AUTH_ACCOUNT_LOCKED" if should_lock else "AUTH_LOGIN_FAILED",
                failure_code="ACCOUNT_LOCKED" if should_lock else "INVALID_CREDENTIALS",
            )
            return self._unauthorized("账号或密码错误")

        security = await self._runtime_security(repository)
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(hours=security.session_hours)
        user.status = UserStatus.ACTIVE
        user.failedLoginCount = 0
        user.lockedUntil = None
        user.lastLoginAt = now
        session_row = await repository.create_session(
            token_hash=self.hash_token(token),
            user_id=user.id,
            expires_at=expires_at,
            client_ip_hash=self._hash_client_ip(context.client_ip),
            user_agent=context.user_agent[:500] if context.user_agent else None,
        )
        access = await repository.user_access(user.id)
        authenticated_user = self._authenticated_user(user, access)
        await audit.record_success(
            actor_id=user.id,
            actor_type=AuditActorType.USER,
            module="AUTH",
            action="AUTH_LOGIN_SUCCESS",
            resource_type="SESSION",
            resource_id=str(session_row.id),
            trace_id=trace_id,
        )
        return LoginResult(token=token, expires_at=expires_at, user=authenticated_user)

    async def authenticate(self, token: str, *, trace_id: UUID) -> SessionIdentity:
        async with transaction(self._session_factory) as session:
            repository = AuthRepository(session)
            audit = AuditService(session)
            session_row = await repository.session_by_hash(self.hash_token(token), for_update=False)
            now = self._utc_now_millis()
            user = (
                await repository.user_by_id(session_row.userId, for_update=False)
                if session_row is not None
                else None
            )
            if (
                session_row is None
                or session_row.revokedAt is not None
                or session_row.expiresAt <= now
                or user is None
                or user.status is not UserStatus.ACTIVE
            ):
                await self._audit_failure(
                    audit,
                    actor_id=None,
                    resource_id=str(session_row.id) if session_row is not None else None,
                    trace_id=trace_id,
                    action="AUTH_SESSION_INVALID",
                    failure_code="SESSION_INVALID",
                    resource_type="SESSION",
                )
                outcome: SessionIdentity | ApiError = self._unauthorized(
                    "登录状态已失效，请重新登录"  # noqa: RUF001
                )
            else:
                access = await repository.user_access(user.id)
                outcome = SessionIdentity(
                    session_id=session_row.id,
                    expires_at=session_row.expiresAt,
                    user=self._authenticated_user(user, access),
                )
        if isinstance(outcome, ApiError):
            raise outcome
        return outcome

    async def record_missing_session(self, *, trace_id: UUID) -> None:
        async with transaction(self._session_factory) as session:
            await self._audit_failure(
                AuditService(session),
                actor_id=None,
                resource_id=None,
                trace_id=trace_id,
                action="AUTH_SESSION_INVALID",
                failure_code="SESSION_MISSING",
                resource_type="SESSION",
            )

    async def change_password(
        self,
        identity: SessionIdentity,
        *,
        current_password: str,
        new_password: str,
        confirm_password: str,
        trace_id: UUID,
    ) -> None:
        async with transaction(self._session_factory) as session:
            outcome = await self._change_password_transaction(
                session,
                identity,
                current_password,
                new_password,
                confirm_password,
                trace_id,
            )
        if outcome is not None:
            raise outcome

    async def _change_password_transaction(
        self,
        session: AsyncSession,
        identity: SessionIdentity,
        current_password: str,
        new_password: str,
        confirm_password: str,
        trace_id: UUID,
    ) -> ApiError | None:
        repository = AuthRepository(session)
        audit = AuditService(session)
        user = await repository.user_by_id(UUID(identity.user.id), for_update=True)
        session_row = await repository.session_by_id(identity.session_id, for_update=True)
        now = self._utc_now_millis()
        if (
            user is None
            or user.status is not UserStatus.ACTIVE
            or session_row is None
            or session_row.userId != (user.id if user is not None else None)
            or session_row.revokedAt is not None
            or session_row.expiresAt <= now
        ):
            await self._audit_failure(
                audit,
                actor_id=None,
                resource_id=str(identity.session_id),
                trace_id=trace_id,
                action="AUTH_PASSWORD_CHANGE_FAILED",
                failure_code="SESSION_INVALID",
                resource_type="SESSION",
            )
            return self._unauthorized("登录状态已失效，请重新登录")  # noqa: RUF001

        if new_password != confirm_password:
            return await self._password_failure(
                audit, user.id, trace_id, "PASSWORD_CONFIRMATION_MISMATCH", "两次输入的新密码不一致"
            )
        if not await self._verify_password(user.passwordHash, current_password):
            return await self._password_failure(
                audit, user.id, trace_id, "CURRENT_PASSWORD_INVALID", "当前密码不正确"
            )
        if await self._verify_password(user.passwordHash, new_password):
            return await self._password_failure(
                audit, user.id, trace_id, "PASSWORD_REUSE", "新密码不能与当前密码相同"
            )

        security = await self._runtime_security(repository)
        violations = password_policy_violations(
            new_password,
            minimum_length=security.password_min_length,
            username=user.username,
        )
        if violations:
            return await self._password_failure(
                audit,
                user.id,
                trace_id,
                "PASSWORD_POLICY_VIOLATION",
                "；".join(violations),  # noqa: RUF001
            )

        user.passwordHash = await asyncio.to_thread(self._password_hasher.hash, new_password)
        user.mustChangePassword = False
        user.passwordChangedAt = now
        user.failedLoginCount = 0
        user.lockedUntil = None
        await repository.revoke_user_sessions(user.id, now)
        await audit.record_success(
            actor_id=user.id,
            actor_type=AuditActorType.USER,
            module="AUTH",
            action="AUTH_PASSWORD_CHANGED",
            resource_type="USER",
            resource_id=str(user.id),
            trace_id=trace_id,
        )
        return None

    async def logout(self, identity: SessionIdentity, *, trace_id: UUID) -> None:
        async with transaction(self._session_factory) as session:
            repository = AuthRepository(session)
            session_row = await repository.session_by_id(identity.session_id, for_update=True)
            if session_row is None or session_row.userId != UUID(identity.user.id):
                await self._audit_failure(
                    AuditService(session),
                    actor_id=None,
                    resource_id=str(identity.session_id),
                    trace_id=trace_id,
                    action="AUTH_LOGOUT_FAILED",
                    failure_code="SESSION_INVALID",
                    resource_type="SESSION",
                )
                outcome: ApiError | None = self._unauthorized(
                    "登录状态已失效，请重新登录"  # noqa: RUF001
                )
            else:
                session_row.revokedAt = session_row.revokedAt or self._utc_now_millis()
                await AuditService(session).record_success(
                    actor_id=UUID(identity.user.id),
                    actor_type=AuditActorType.USER,
                    module="AUTH",
                    action="AUTH_LOGOUT",
                    resource_type="SESSION",
                    resource_id=str(identity.session_id),
                    trace_id=trace_id,
                )
                outcome = None
        if outcome is not None:
            raise outcome

    async def _password_failure(
        self,
        audit: AuditService,
        user_id: UUID,
        trace_id: UUID,
        failure_code: str,
        message: str,
    ) -> ApiError:
        await self._audit_failure(
            audit,
            actor_id=user_id,
            resource_id=str(user_id),
            trace_id=trace_id,
            action="AUTH_PASSWORD_CHANGE_FAILED",
            failure_code=failure_code,
        )
        return ApiError(400, "BAD_REQUEST", message)

    @staticmethod
    async def _audit_failure(
        audit: AuditService,
        *,
        actor_id: UUID | None,
        resource_id: str | None,
        trace_id: UUID,
        action: str,
        failure_code: str,
        resource_type: str = "USER",
    ) -> None:
        await audit.record_failure(
            actor_id=actor_id,
            actor_type=AuditActorType.USER if actor_id is not None else AuditActorType.SYSTEM,
            module="AUTH",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            trace_id=trace_id,
            failure_code=failure_code,
        )

    async def _runtime_security(self, repository: AuthRepository) -> RuntimeSecurity:
        configured = self._configuration
        values = await repository.latest_security_settings()
        return RuntimeSecurity(
            session_hours=self._bounded_int(
                values.get("sessionHours"), configured.session_hours, 1, 168
            ),
            login_max_attempts=self._bounded_int(
                values.get("loginMaxAttempts"), configured.login_max_attempts, 3, 20
            ),
            login_lock_minutes=self._bounded_int(
                values.get("loginLockMinutes"), configured.login_lock_minutes, 1, 1_440
            ),
            password_min_length=self._bounded_int(
                values.get("passwordMinLength"), configured.password_min_length, 12, 128
            ),
        )

    @staticmethod
    def _bounded_int(value: object, fallback: int, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            return fallback
        return value if minimum <= value <= maximum else fallback

    async def _verify_password(self, password_hash: str, password: str) -> bool:
        def verify() -> bool:
            try:
                return self._password_hasher.verify(password_hash, password)
            except VerificationError:
                return False

        return await asyncio.to_thread(verify)

    def hash_token(self, token: str) -> str:
        return self._session_key.digest(b"session-token", token)

    def _hash_client_ip(self, client_ip: str | None) -> str | None:
        if client_ip is None:
            return None
        return self._session_key.digest(b"client-ip", client_ip)

    @staticmethod
    def _utc_now_millis() -> datetime:
        now = datetime.now(UTC)
        return now.replace(microsecond=(now.microsecond // 1_000) * 1_000)

    @staticmethod
    def format_expiration(value: datetime) -> str:
        utc_value = value.astimezone(UTC)
        milliseconds = utc_value.microsecond // 1_000
        return f"{utc_value:%Y-%m-%dT%H:%M:%S}.{milliseconds:03d}Z"

    @staticmethod
    def _authenticated_user(user: User, access: UserAccess) -> AuthenticatedUser:
        roles = sorted({code for code, _scope in access.roles if code in _ROLE_CODES})
        scopes = {scope for _code, scope in access.roles}
        return AuthenticatedUser(
            id=str(user.id),
            username=user.username,
            displayName=user.displayName,
            departmentName=access.department_name,
            roleCodes=cast(list[RoleCode], roles),
            permissions=list(access.permissions),
            dataScope=AuthService._aggregate_data_scope(scopes),
            mustChangePassword=user.mustChangePassword,
        )

    @staticmethod
    def _aggregate_data_scope(scopes: set[str]) -> DataScope:
        if "ALL" in scopes:
            return "ALL"
        if "OWNED_OR_ASSIGNED" in scopes or {"OWNED", "ASSIGNED"} <= scopes:
            return "OWNED_OR_ASSIGNED"
        if "OWNED" in scopes:
            return "OWNED"
        if "ASSIGNED" in scopes:
            return "ASSIGNED"
        return "NONE"

    @staticmethod
    def _unauthorized(message: str) -> ApiError:
        return ApiError(
            401,
            "UNAUTHORIZED",
            message,
            headers={"WWW-Authenticate": "Session"},
        )


__all__ = [
    "AuthConfiguration",
    "AuthService",
    "LoginResult",
    "RequestContext",
    "RuntimeSecurity",
    "SessionIdentity",
    "SessionKey",
    "SessionKeyError",
]

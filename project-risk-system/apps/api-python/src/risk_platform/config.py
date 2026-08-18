"""Validated process configuration for the HTTP application."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

Environment = Literal["development", "test", "production"]
IpNetwork = IPv4Network | IPv6Network


def normalize_outbound_hostname(hostname: str) -> str:
    """Normalize an explicitly configured outbound hostname safely."""

    normalized = hostname.rstrip(".").lower()
    if (
        not normalized
        or "*" in normalized
        or any(character.isspace() for character in normalized)
    ):
        raise ValueError("invalid outbound hostname")
    try:
        canonical = normalized.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("invalid outbound hostname") from None
    if len(canonical) > 253:
        raise ValueError("invalid outbound hostname")
    try:
        ip_address(canonical)
    except ValueError:
        pass
    else:
        raise ValueError("invalid outbound hostname")
    labels = canonical.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        raise ValueError("invalid outbound hostname")
    return canonical


class SettingsError(RuntimeError):
    """A safe startup error that never includes configuration values."""


class Settings(BaseModel):
    """Validated settings used by the shared HTTP bootstrap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: Environment = "development"
    api_port: int = Field(default=3000, ge=1, le=65_535)
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    request_origin_validation_enabled: bool = True
    trusted_proxy_cidrs: tuple[IpNetwork, ...] = ()
    ai_outbound_allowed_hostnames: frozenset[str] = frozenset()
    ai_outbound_allowed_cidrs: tuple[IpNetwork, ...] = ()
    session_cookie_name: str = "project_risk_session"
    session_secret_file: Path | None = None
    wechat_user_info_url: str | None = None
    wechat_user_info_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    wechat_user_info_max_retries: int = Field(default=2, ge=0, le=3)

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        if not origins:
            raise ValueError("at least one CORS origin is required")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or origin == "*"
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins")
        return origins

    @field_validator("session_cookie_name")
    @classmethod
    def validate_cookie_name(cls, name: str) -> str:
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise ValueError("invalid cookie name")
        return name

    @field_validator("session_secret_file", mode="before")
    @classmethod
    def validate_session_secret_file(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("session secret file path must not be empty")
        return value

    @model_validator(mode="after")
    def require_production_session_secret_file(self) -> Self:
        if self.environment == "production" and self.session_secret_file is None:
            raise ValueError("SESSION_SECRET_FILE is required in production")
        return self

    @property
    def session_cookie_secure(self) -> bool:
        """Production session cookies are always HTTPS-only."""

        return self.environment == "production"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        """Load only owned variables and report validation failures without values."""

        source = os.environ if environ is None else environ
        raw: dict[str, object] = {}
        scalar_names = {
            "NODE_ENV": "environment",
            "API_PORT": "api_port",
            "REQUEST_ORIGIN_VALIDATION_ENABLED": "request_origin_validation_enabled",
            "SESSION_COOKIE_NAME": "session_cookie_name",
            "SESSION_SECRET_FILE": "session_secret_file",
            "WECHAT_USER_INFO_URL": "wechat_user_info_url",
            "WECHAT_USER_INFO_TIMEOUT_SECONDS": "wechat_user_info_timeout_seconds",
            "WECHAT_USER_INFO_MAX_RETRIES": "wechat_user_info_max_retries",
        }
        for env_name, field_name in scalar_names.items():
            if env_name in source:
                if env_name == "WECHAT_USER_INFO_URL" and not source[env_name].strip():
                    continue
                raw[field_name] = source[env_name]

        if "CORS_ORIGIN" in source:
            raw["cors_origins"] = cls._split_csv(source["CORS_ORIGIN"])
        if "TRUSTED_PROXY_CIDRS" in source:
            try:
                raw["trusted_proxy_cidrs"] = tuple(
                    ip_network(value, strict=False)
                    for value in cls._split_csv(source["TRUSTED_PROXY_CIDRS"])
                )
            except ValueError:
                raise SettingsError("配置项无效: TRUSTED_PROXY_CIDRS") from None
        if "AI_OUTBOUND_ALLOWED_HOSTNAMES" in source:
            try:
                raw["ai_outbound_allowed_hostnames"] = frozenset(
                    normalize_outbound_hostname(value)
                    for value in cls._split_csv(source["AI_OUTBOUND_ALLOWED_HOSTNAMES"])
                )
            except ValueError:
                raise SettingsError("配置项无效: AI_OUTBOUND_ALLOWED_HOSTNAMES") from None
        if "AI_OUTBOUND_ALLOWED_CIDRS" in source:
            try:
                raw["ai_outbound_allowed_cidrs"] = tuple(
                    ip_network(value, strict=False)
                    for value in cls._split_csv(source["AI_OUTBOUND_ALLOWED_CIDRS"])
                )
            except ValueError:
                raise SettingsError("配置项无效: AI_OUTBOUND_ALLOWED_CIDRS") from None
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            fields = cls._invalid_fields(exc)
            raise SettingsError(f"配置项无效: {', '.join(fields)}") from None

    @staticmethod
    def _split_csv(value: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in value.split(",") if item.strip())

    @staticmethod
    def _invalid_fields(exc: ValidationError) -> list[str]:
        return sorted(
            {
                str(error["loc"][0]) if error["loc"] else "SESSION_SECRET_FILE"
                for error in exc.errors()
            }
        )


__all__ = [
    "Environment",
    "IpNetwork",
    "Settings",
    "SettingsError",
    "normalize_outbound_hostname",
]

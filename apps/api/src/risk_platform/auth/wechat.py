"""Server-side WeChat mini-program SSO integration."""

from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request as UrlRequest,
    build_opener,
)


class _RedirectBlockedError(RuntimeError):
    """The fixed user-info endpoint must never follow an HTTP redirect."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request: UrlRequest, *args: object, **kwargs: object) -> None:
        del request, args, kwargs
        raise _RedirectBlockedError


class WechatUserInfoError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class WechatUserInfoClient:
    url: str
    timeout_seconds: float = 3.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("WECHAT_USER_INFO_URL must be an HTTPS URL without query")
        if self.timeout_seconds <= 0 or self.max_retries < 0 or self.max_retries > 3:
            raise ValueError("invalid WeChat client limits")

    async def fetch_mobile(self, person_token: str) -> str:
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                payload = await asyncio.to_thread(self._request, person_token)
                return self._mobile_from_payload(payload)
            except WechatUserInfoError as exc:
                if exc.code == "WECHAT_USER_INFO_UNAVAILABLE" and attempt + 1 < attempts:
                    await asyncio.sleep(0.05 * (attempt + 1))
                    continue
                raise

        raise WechatUserInfoError("WECHAT_USER_INFO_UNAVAILABLE")

    def _request(self, person_token: str) -> object:
        request = UrlRequest(
            self.url,
            data=b"{}",
            headers={"Content-Type": "application/json", "personToken": person_token},
            method="POST",
        )
        try:
            opener = build_opener(
                _NoRedirectHandler(),
                HTTPSHandler(context=ssl.create_default_context()),
            )
            with opener.open(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                status = response.status
                raw = response.read(64 * 1024 + 1)
        except HTTPError as exc:
            if 500 <= exc.code <= 599:
                raise WechatUserInfoError("WECHAT_USER_INFO_UNAVAILABLE") from None
            raise WechatUserInfoError("WECHAT_TOKEN_INVALID") from None
        except (_RedirectBlockedError, TimeoutError, URLError, OSError):
            raise WechatUserInfoError("WECHAT_USER_INFO_UNAVAILABLE") from None
        if status >= 500:
            raise WechatUserInfoError("WECHAT_USER_INFO_UNAVAILABLE")
        if status < 200 or status >= 300:
            raise WechatUserInfoError("WECHAT_TOKEN_INVALID")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WechatUserInfoError("WECHAT_USER_INFO_UNAVAILABLE") from None

    @staticmethod
    def _mobile_from_payload(payload: object) -> str:
        if not isinstance(payload, dict):
            raise WechatUserInfoError("WECHAT_USER_INFO_UNAVAILABLE")
        meta = payload.get("meta")
        if not isinstance(meta, dict) or meta.get("code") != 0:
            raise WechatUserInfoError("WECHAT_TOKEN_INVALID")
        data = payload.get("data")
        mobile = data.get("mobile") if isinstance(data, dict) else None
        if not isinstance(mobile, str) or not mobile.strip():
            raise WechatUserInfoError("WECHAT_USER_NOT_BOUND")
        normalized = mobile.strip()
        if len(normalized) != 11 or not normalized.isdecimal() or not normalized.startswith("1"):
            raise WechatUserInfoError("WECHAT_USER_NOT_BOUND")
        return normalized


__all__ = ["WechatUserInfoClient", "WechatUserInfoError"]

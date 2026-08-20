"""Request trace propagation without trusting arbitrary input."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TRACE_HEADER = b"x-trace-id"


def _validated_trace_id(raw_value: bytes | None) -> str:
    if raw_value is not None:
        try:
            return str(UUID(raw_value.decode("ascii")))
        except (UnicodeDecodeError, ValueError):
            pass
    return str(uuid4())


def get_trace_id(request: Request) -> str:
    """Return the trace assigned by :class:`TraceMiddleware`."""

    trace_id = getattr(request.state, "trace_id", None)
    if not isinstance(trace_id, str):
        raise RuntimeError("TraceMiddleware is not installed")
    return trace_id


class TraceMiddleware:
    """Assign one UUID trace to request state, envelope and response headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = next((value for key, value in scope["headers"] if key == TRACE_HEADER), None)
        state = scope.setdefault("state", {})
        state["trace_id"] = _validated_trace_id(incoming)

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers = [(key, value) for key, value in headers if key.lower() != TRACE_HEADER]
                headers.append((TRACE_HEADER, state["trace_id"].encode("ascii")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_trace)


__all__ = ["TraceMiddleware", "get_trace_id"]

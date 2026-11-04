"""Request context middleware (pure ASGI, so it is safe for streaming responses)."""

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
# Accept caller-provided IDs only if they are short and safe to put in logs.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
# Probe endpoints are called every few seconds; logging them only adds noise.
_QUIET_PATHS = frozenset({"/healthz", "/readyz"})

_access_log = structlog.get_logger("workmind.access")


def _incoming_request_id(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key.decode("latin-1") == REQUEST_ID_HEADER:
            candidate = value.decode("latin-1")
            return candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else None
    return None


class RequestContextMiddleware:
    """Assigns a request ID, binds it to every log line, echoes it, and logs access."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            path: str = scope["path"]
            if path not in _QUIET_PATHS:
                _access_log.info(
                    "request",
                    method=scope["method"],
                    path=path,
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            structlog.contextvars.clear_contextvars()

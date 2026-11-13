"""HTTP-level guard for /mcp: no valid personal access token, no MCP at all (401), and the
token owner's request rate limit (429 rate_limited, docs/multi-tenancy.md).

Tools authenticate the token again when they run, because the MCP SDK executes them outside
this request's task. Bearer tokens are never ambient credentials, so no CSRF check applies.
"""

import json
from collections.abc import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

from needwise.core.ratelimit import RateLimitedError
from needwise.core.resources import Resources
from needwise.modules.identity import tokens


def _unauthorized(message: str) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps({"code": "unauthenticated", "message": message}).encode()
    return body, [
        (b"content-type", b"application/json"),
        (b"www-authenticate", b'Bearer realm="NeedWise", error="invalid_token"'),
    ]


class RequireAccessToken:
    def __init__(self, app: ASGIApp, resources: Callable[[], Resources]) -> None:
        self.app = app
        self.resources = resources

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = dict(scope.get("headers", [])).get(b"authorization", b"").decode("latin-1")
        problem = None
        resources = self.resources()
        if header[:7].lower() != "bearer ":
            problem = "Send a NeedWise personal access token: Authorization: Bearer nw_pat_..."
        elif (found := await tokens.authenticate(resources.db, header[7:].strip())) is None:
            problem = "The access token is invalid, revoked or expired."
        else:
            token, _identity = found
            try:
                await resources.limits.check(token.tenant_id, token.user_id, "mcp")
            except RateLimitedError as exc:
                await _send_limited(send, exc)
                return
        if problem is None:
            await self.app(scope, receive, send)
            return
        body, headers = _unauthorized(problem)
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})


async def _send_limited(send: Send, error: RateLimitedError) -> None:
    content = {
        "type": "about:blank",
        "title": error.title,
        "status": error.status,
        "code": error.code,
        "detail": error.detail,
        **error.extra,
    }
    headers = [(b"content-type", b"application/problem+json")]
    headers += [(k.lower().encode(), v.encode()) for k, v in error.headers.items()]
    await send({"type": "http.response.start", "status": error.status, "headers": headers})
    await send({"type": "http.response.body", "body": json.dumps(content).encode()})

"""HTTP-level guard for /mcp: no valid personal access token, no MCP at all (401).

Tools authenticate the token again when they run, because the MCP SDK executes them outside
this request's task. Bearer tokens are never ambient credentials, so no CSRF check applies.
"""

import json
from collections.abc import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

from workmind.core.resources import Resources
from workmind.modules.identity import tokens


def _unauthorized(message: str) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps({"code": "unauthenticated", "message": message}).encode()
    return body, [
        (b"content-type", b"application/json"),
        (b"www-authenticate", b'Bearer realm="WorkMind", error="invalid_token"'),
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
        if header[:7].lower() != "bearer ":
            problem = "Send a WorkMind personal access token: Authorization: Bearer wm_pat_..."
        elif await tokens.authenticate(self.resources().db, header[7:].strip()) is None:
            problem = "The access token is invalid, revoked or expired."
        if problem is None:
            await self.app(scope, receive, send)
            return
        body, headers = _unauthorized(problem)
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})

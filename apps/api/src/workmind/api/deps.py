"""Request dependencies: authentication (session cookie) and CSRF protection."""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request

from workmind.core.config import Settings
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError, unauthenticated
from workmind.core.resources import Resources
from workmind.core.tenancy import tenant_transaction
from workmind.modules.identity.service import load_identity

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-csrf-protection"


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def resources_of(request: Request) -> Resources:
    return request.app.state.resources


def verify_csrf(request: Request, settings: Settings) -> None:
    """Cookie-authenticated, state-changing requests must come from our own origin and carry a
    custom header that cross-site forms cannot set (OWASP CSRF cheat sheet). SameSite=Lax
    cookies are the first layer; this is the second."""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None and (referer := request.headers.get("referer")):
        parts = urlsplit(referer)
        origin = f"{parts.scheme}://{parts.netloc}"
    if origin != settings.public_origin:
        raise ApiError(403, "csrf_origin", "Cross-site request blocked")
    if request.headers.get(CSRF_HEADER) != "1":
        raise ApiError(
            403, "csrf_header", "Missing CSRF protection header", f"Send '{CSRF_HEADER}: 1'."
        )


async def get_request_context(request: Request) -> RequestContext:
    settings, resources = settings_of(request), resources_of(request)
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise unauthenticated()
    verify_csrf(request, settings)

    session = await resources.sessions.get(session_id)
    if session is None:
        raise unauthenticated("Your session has expired. Sign in again.")
    # Loaded on every request, so disabling a user or changing a role applies immediately.
    async with tenant_transaction(resources.db, session.tenant_id) as db:
        identity = await load_identity(db, session.user_id)
    if identity is None or identity.user.status != "active":
        await resources.sessions.delete(session_id)
        raise unauthenticated()

    return RequestContext(
        request_id=getattr(request.state, "request_id", ""),
        tenant_id=session.tenant_id,
        user_id=session.user_id,
        org_role=identity.user.org_role,
        principals=identity.principals,
        auth_method="session",
    )


CurrentContext = Annotated[RequestContext, Depends(get_request_context)]

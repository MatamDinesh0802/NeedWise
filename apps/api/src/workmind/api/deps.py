"""Request dependencies: authentication (session cookie) and CSRF protection."""

from typing import Annotated
from urllib.parse import urlsplit

import structlog
from fastapi import Depends, Request

from workmind.core.config import Settings
from workmind.core.context import RequestContext
from workmind.core.edition import Feature, enterprise_feature_error
from workmind.core.errors import ApiError, unauthenticated
from workmind.core.resources import Resources
from workmind.core.tenancy import tenant_transaction
from workmind.modules.identity import tokens
from workmind.modules.identity.service import load_identity, tenant_is_active

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


async def _token_context(request: Request, plain: str) -> RequestContext:
    """Bearer access tokens are not ambient credentials (a browser never sends them on its
    own), so CSRF checks do not apply to them."""
    result = await tokens.authenticate(resources_of(request).db, plain)
    if result is None:
        raise unauthenticated("The access token is invalid, revoked or expired.")
    token, identity = result
    structlog.contextvars.bind_contextvars(tenant_id=str(token.tenant_id))
    return RequestContext(
        request_id=getattr(request.state, "request_id", ""),
        tenant_id=token.tenant_id,
        user_id=token.user_id,
        org_role=identity.user.org_role,
        principals=identity.principals,
        auth_method="pat",
        scopes=frozenset(token.scopes),
    )


async def get_request_context(request: Request) -> RequestContext:
    settings, resources = settings_of(request), resources_of(request)
    authorization = request.headers.get("authorization", "")
    if authorization[:7].lower() == "bearer ":
        return await _token_context(request, authorization[7:].strip())

    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise unauthenticated()
    verify_csrf(request, settings)

    session = await resources.sessions.get(session_id)
    if session is None:
        raise unauthenticated("Your session has expired. Sign in again.")
    # Loaded on every request, so disabling a user, changing a role or suspending the tenant
    # applies immediately.
    async with tenant_transaction(resources.db, session.tenant_id) as db:
        identity = await load_identity(db, session.user_id)
        active_tenant = await tenant_is_active(db, session.tenant_id)
    if identity is None or identity.user.status != "active" or not active_tenant:
        await resources.sessions.delete(session_id)
        raise unauthenticated()

    # Every log line of the request from here on (and its access log line) names the tenant.
    structlog.contextvars.bind_contextvars(tenant_id=str(session.tenant_id))
    return RequestContext(
        request_id=getattr(request.state, "request_id", ""),
        tenant_id=session.tenant_id,
        user_id=session.user_id,
        org_role=identity.user.org_role,
        principals=identity.principals,
        auth_method="session",
        scopes=frozenset({"*"}),
    )


CurrentContext = Annotated[RequestContext, Depends(get_request_context)]


def require_scope(scope: str):
    async def dependency(ctx: CurrentContext) -> RequestContext:
        if not ctx.has_scope(scope):
            raise ApiError(403, "scope_required", "Missing scope", f"Requires scope '{scope}'.")
        return ctx

    return Depends(dependency)


def require_feature(feature: Feature):
    """An Enterprise feature: sign-in first (401), then the license (402 "enterprise_feature").
    Use as `dependencies=[require_feature("...")]` or `Annotated[RequestContext, ...]`."""

    async def dependency(request: Request, ctx: CurrentContext) -> RequestContext:
        if not resources_of(request).edition.has(feature):
            raise enterprise_feature_error(feature)
        return ctx

    return Depends(dependency)


async def _session_only(ctx: CurrentContext) -> RequestContext:
    if ctx.auth_method != "session":
        raise ApiError(403, "session_required", "Sign in with a browser session for this action")
    return ctx


SessionContext = Annotated[RequestContext, Depends(_session_only)]


async def enforce_limit(request: Request, ctx: RequestContext, bucket: str) -> None:
    """Counts the request against the person's and the tenant's limit (429 rate_limited)."""
    await resources_of(request).limits.check(ctx.tenant_id, ctx.user_id, bucket)


class RateLimit:
    """A route's limit; `bucket` stays readable so a test can list every limited route."""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    async def __call__(self, request: Request, ctx: CurrentContext) -> None:
        await enforce_limit(request, ctx, self.bucket)


def rate_limit(bucket: str):
    """Use as `dependencies=[rate_limit("chat")]`: sign-in first (401), then the limit (429)."""
    return Depends(RateLimit(bucket))


def client_address(request: Request) -> str | None:
    """The caller's address (uvicorn applies X-Forwarded-For from trusted proxies: Caddy)."""
    return request.client.host if request.client else None

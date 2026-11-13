"""Login, logout and back-channel logout: the Backend-for-Frontend side of OIDC (ADR 0003).

Flow: /api/auth/login -> Keycloak -> /api/auth/callback -> session cookie -> app.
Tokens never reach the browser; it only ever holds an opaque, HttpOnly session cookie.
"""

import hmac
import json
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from needwise.api.deps import client_address, resources_of, settings_of, verify_csrf
from needwise.core.config import Settings
from needwise.core.tenancy import tenant_transaction
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.identity.oidc import OidcError
from needwise.modules.identity.service import (
    SignInError,
    record_denied_sign_in,
    resolve_tenant,
    sign_in,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = structlog.get_logger("needwise.auth")

FLOW_COOKIE = "nw_oidc_flow"
FLOW_TTL_S = 600


class LogoutResponse(BaseModel):
    redirect_url: str


def safe_return_to(value: str | None) -> str:
    """Only same-site relative paths: blocks open redirects such as //evil.example."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    parts = urlsplit(value)
    return "/" if parts.scheme or parts.netloc else value


def _client(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def _set_flow_cookie(response: Response, settings: Settings, state: str) -> None:
    response.set_cookie(
        FLOW_COOKIE,
        state,
        max_age=FLOW_TTL_S,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/api/auth",
    )


def _clear_flow_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        FLOW_COOKIE, path="/api/auth", secure=settings.secure_cookies, httponly=True, samesite="lax"
    )


@router.get("/login", summary="Start sign-in (redirects to Keycloak)")
async def login(
    request: Request,
    return_to: str | None = None,
    login_hint: Annotated[str | None, Query(max_length=254, pattern=r"^[^\s<>\"']+$")] = None,
) -> RedirectResponse:
    settings, resources = settings_of(request), resources_of(request)
    # Each start stores a flow in Valkey for 10 minutes: counted per address.
    await resources.limits.check_address(client_address(request), "login")
    try:
        attempt = await resources.oidc.begin_login(login_hint=login_hint)
    except OidcError as exc:
        log.warning("login_unavailable", reason=str(exc))
        return RedirectResponse("/?auth_error=login_unavailable", status_code=303)
    await resources.valkey.set(
        f"oidc:flow:{attempt.state}",
        json.dumps(
            {
                "nonce": attempt.nonce,
                "code_verifier": attempt.code_verifier,
                "return_to": safe_return_to(return_to),
            }
        ),
        ex=FLOW_TTL_S,
    )
    response = RedirectResponse(attempt.url, status_code=302)
    # Binds the flow to this browser: a callback with someone else's state is rejected.
    _set_flow_cookie(response, settings, attempt.state)
    return response


@router.get("/callback", summary="Keycloak redirects here after sign-in")
async def callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    settings, resources = settings_of(request), resources_of(request)

    def fail(reason: str) -> RedirectResponse:
        response = RedirectResponse(f"/?auth_error={reason}", status_code=303)
        _clear_flow_cookie(response, settings)
        return response

    await resources.limits.check_address(client_address(request), "login")
    if error:
        return fail("login_cancelled" if error == "access_denied" else "login_failed")
    browser_state = request.cookies.get(FLOW_COOKIE)
    if not (code and state and browser_state and hmac.compare_digest(state, browser_state)):
        return fail("login_expired")
    raw_flow = await resources.valkey.getdel(f"oidc:flow:{state}")  # single use
    if raw_flow is None:
        return fail("login_expired")
    flow = json.loads(raw_flow)

    try:
        tokens = await resources.oidc.exchange_code(code, flow["code_verifier"])
        claims = await resources.oidc.verify_id_token(tokens["id_token"], nonce=flow["nonce"])
    except OidcError as exc:
        log.warning("login_failed", reason=str(exc))
        return fail("login_failed")

    tenant = await resolve_tenant(resources.db, claims.get("organization"))
    if tenant is None:
        return fail("no_organization")
    ip, user_agent = _client(request)
    try:
        user = await sign_in(resources.db, tenant.id, claims, ip=ip, user_agent=user_agent)
    except SignInError as exc:
        await record_denied_sign_in(
            resources.db,
            tenant.id,
            reason=exc.code,
            email=claims.get("email"),
            ip=ip,
            user_agent=user_agent,
        )
        return fail(exc.code)

    session_id = await resources.sessions.create(
        user_id=user.id,
        tenant_id=tenant.id,
        keycloak_sub=str(claims["sub"]),
        keycloak_sid=claims.get("sid"),
        id_token=tokens["id_token"],
    )
    response = RedirectResponse(flow["return_to"], status_code=303)
    response.set_cookie(
        settings.session_cookie_name,
        session_id,
        max_age=settings.session_absolute_timeout_s,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    _clear_flow_cookie(response, settings)
    log.info("login", user_id=str(user.id), tenant=tenant.slug)
    return response


@router.post("/logout", summary="End the session", response_model=LogoutResponse)
async def logout(request: Request) -> JSONResponse:
    """Returns the Keycloak end-session URL; the browser navigates there to end the SSO session."""
    settings, resources = settings_of(request), resources_of(request)
    verify_csrf(request, settings)
    redirect_url = f"{settings.public_url.rstrip('/')}/"

    session_id = request.cookies.get(settings.session_cookie_name)
    session = await resources.sessions.get(session_id) if session_id else None
    if session_id and session:
        await resources.sessions.delete(session_id)
        ip, user_agent = _client(request)
        async with tenant_transaction(resources.db, session.tenant_id) as db:
            await record_event(
                db,
                tenant_id=session.tenant_id,
                actor=Actor(type="user", id=session.user_id),
                action="auth.logout",
                resource_type="user",
                resource_id=str(session.user_id),
                ip=ip,
                user_agent=user_agent,
            )
        try:
            redirect_url = await resources.oidc.end_session_url(
                id_token_hint=session.id_token, post_logout_redirect_uri=redirect_url
            )
        except OidcError as exc:
            # The NeedWise session is gone either way; the SSO session ends on its own timeout.
            log.warning("end_session_unavailable", reason=str(exc))

    response = JSONResponse(LogoutResponse(redirect_url=redirect_url).model_dump())
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/backchannel-logout", include_in_schema=False)
async def backchannel_logout(request: Request) -> Response:
    """Keycloak calls this server-to-server when an SSO session ends (admin action, logout
    elsewhere, user disabled). Caddy does not expose it publicly."""
    resources = resources_of(request)
    form = parse_qs((await request.body()).decode())
    token = form.get("logout_token", [""])[0]
    try:
        claims = await resources.oidc.verify_logout_token(token)
    except OidcError as exc:
        log.warning("backchannel_logout_rejected", reason=str(exc))
        return Response(status_code=400)

    sid = claims.get("sid")
    removed = (
        await resources.sessions.delete_by_keycloak_session(sid)
        if sid
        else await resources.sessions.delete_by_keycloak_subject(str(claims["sub"]))
    )
    log.info("backchannel_logout", sessions_removed=removed)
    return Response(status_code=200, headers={"Cache-Control": "no-store"})

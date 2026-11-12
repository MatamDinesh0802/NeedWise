"""Application factory.

Run with: uvicorn workmind.main:create_app --factory
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from mcp.server.transport_security import TransportSecuritySettings
from opentelemetry.sdk.trace import TracerProvider
from starlette.routing import Route

from workmind.api import auth, v1
from workmind.api.deps import require_feature
from workmind.api.ops import probes
from workmind.api.v1 import enterprise_fallbacks
from workmind.core.config import Settings, get_settings
from workmind.core.errors import ApiError, api_error_handler, validation_error_handler
from workmind.core.extensions import ExtensionRegistry, load_extensions
from workmind.core.health import build_health_checks
from workmind.core.logging import configure_logging
from workmind.core.middleware import RequestContextMiddleware
from workmind.core.resources import Resources
from workmind.core.telemetry import configure_telemetry
from workmind.modules.authz.sync import OutboxWorker
from workmind.modules.mcp_server.auth import RequireAccessToken
from workmind.modules.mcp_server.server import build_mcp_server

log = structlog.get_logger("workmind")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = app.state.settings
    resources = Resources.create(settings, extensions=app.state.extensions)
    app.state.resources = resources
    await resources.load_remote_tools()
    # Tests inject fake checks before startup; keep them if present.
    if not hasattr(app.state, "health_checks"):
        app.state.health_checks = build_health_checks(
            resources, settings, settings.health_check_timeout_s
        )
    outbox_worker = (
        asyncio.create_task(OutboxWorker(resources.db, resources.fga).run_forever())
        if settings.authz_outbox_worker
        else None
    )
    log.info(
        "startup",
        product=settings.product_name,
        version=settings.version,
        environment=settings.app_env,
        edition=resources.edition.name,
    )
    mcp_sessions = (
        app.state.mcp.session_manager.run()
        if hasattr(app.state, "mcp")
        else contextlib.nullcontext()
    )
    try:
        async with mcp_sessions:
            yield
    finally:
        if outbox_worker is not None:
            outbox_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await outbox_worker
        await resources.aclose()
        tracer_provider: TracerProvider | None = app.state.tracer_provider
        if tracer_provider is not None:
            tracer_provider.shutdown()
        log.info("shutdown")


def create_app(
    settings: Settings | None = None, *, extensions: ExtensionRegistry | None = None
) -> FastAPI:
    """`extensions`: tests pass their own; otherwise the license decides (ADR 0011)."""
    settings = settings or get_settings()
    configure_logging(settings)
    if extensions is None:
        extensions = load_extensions(settings)

    app = FastAPI(
        title=f"{settings.product_name} API",
        version=settings.version,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.extensions = extensions
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(probes)
    app.include_router(auth.router)
    app.include_router(v1.router, prefix="/api/v1")
    # After the core routes, so an enterprise route can never shadow one.
    for extension in extensions.routers:
        app.include_router(
            extension.router, prefix="/api/v1", dependencies=[require_feature(extension.feature)]
        )
    # Last: Enterprise paths that nothing registered answer 402 "enterprise_feature".
    app.include_router(enterprise_fallbacks.router, prefix="/api/v1")
    _add_mcp(app, settings)
    app.state.tracer_provider = configure_telemetry(app, settings)
    return app


def _add_mcp(app: FastAPI, settings: Settings) -> None:
    """The WorkMind MCP server at /mcp (Streamable HTTP, stateless). Its session manager runs
    inside the app's lifespan, because Starlette never runs a mounted app's own lifespan."""
    mcp = build_mcp_server(lambda: app.state.resources, settings.version)
    public = urlsplit(settings.public_url).netloc
    security = TransportSecuritySettings(
        allowed_hosts=sorted({public, "localhost:*", "127.0.0.1:*", "api:*", "testserver"}),
        allowed_origins=sorted(
            {settings.public_origin, "http://localhost:*", "http://127.0.0.1:*"}
        ),
    )
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=security,
    )
    guarded = RequireAccessToken(mcp_app, lambda: app.state.resources)
    app.router.routes.append(Route("/mcp", endpoint=guarded, methods=["GET", "POST", "DELETE"]))
    app.state.mcp = mcp

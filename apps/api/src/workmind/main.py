"""Application factory.

Run with: uvicorn workmind.main:create_app --factory
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider

from workmind.api import auth, v1
from workmind.api.ops import probes
from workmind.core.config import Settings, get_settings
from workmind.core.errors import ApiError, api_error_handler
from workmind.core.health import build_health_checks
from workmind.core.logging import configure_logging
from workmind.core.middleware import RequestContextMiddleware
from workmind.core.resources import Resources
from workmind.core.telemetry import configure_telemetry
from workmind.modules.authz.sync import OutboxWorker

log = structlog.get_logger("workmind")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = app.state.settings
    resources = Resources.create(settings)
    app.state.resources = resources
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
    )
    try:
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=f"{settings.product_name} API",
        version=settings.version,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(probes)
    app.include_router(auth.router)
    app.include_router(v1.router, prefix="/api/v1")
    app.state.tracer_provider = configure_telemetry(app, settings)
    return app

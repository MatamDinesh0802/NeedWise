"""Operational endpoints for orchestrators and the UI.

- /healthz: liveness. The process is up; no dependencies are checked.
- /readyz: readiness. 200 only when every dependency answers; otherwise 503.
- /api/v1/system/status: the same data for the UI, always 200.
"""

from collections.abc import Mapping
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from workmind.core.config import Settings
from workmind.core.health import ComponentStatus, HealthCheck, run_checks


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class SystemStatus(BaseModel):
    status: Literal["ok", "degraded"]
    product: str
    version: str
    environment: str
    components: list[ComponentStatus]


async def collect_status(request: Request) -> SystemStatus:
    settings: Settings = request.app.state.settings
    checks: Mapping[str, HealthCheck] = request.app.state.health_checks
    components = await run_checks(checks, settings.health_check_timeout_s)
    healthy = all(component.status == "ok" for component in components)
    return SystemStatus(
        status="ok" if healthy else "degraded",
        product=settings.product_name,
        version=settings.version,
        environment=settings.app_env,
        components=components,
    )


probes = APIRouter(tags=["ops"])


@probes.get("/healthz")
async def healthz() -> Liveness:
    return Liveness()


@probes.get("/readyz", response_model=SystemStatus, responses={503: {"model": SystemStatus}})
async def readyz(request: Request) -> JSONResponse:
    status = await collect_status(request)
    return JSONResponse(
        status_code=200 if status.status == "ok" else 503,
        content=status.model_dump(mode="json"),
    )


system = APIRouter(prefix="/system", tags=["system"])


@system.get("/status")
async def system_status(request: Request) -> SystemStatus:
    return await collect_status(request)

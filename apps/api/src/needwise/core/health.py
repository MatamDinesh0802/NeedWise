"""Dependency health checks used by /readyz and the system status endpoint."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

import httpx
from pydantic import BaseModel
from sqlalchemy import text

from needwise.core.config import Settings
from needwise.core.resources import Resources

# A check succeeds by returning and fails by raising.
HealthCheck = Callable[[], Awaitable[None]]


class ComponentStatus(BaseModel):
    name: str
    status: Literal["ok", "down"]
    latency_ms: float
    # Only the exception type is exposed: messages can contain hostnames or credentials.
    detail: str | None = None


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


async def run_check(name: str, check: HealthCheck, timeout_s: float) -> ComponentStatus:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_s):
            await check()
    except Exception as exc:
        return ComponentStatus(
            name=name, status="down", latency_ms=_elapsed_ms(started), detail=type(exc).__name__
        )
    return ComponentStatus(name=name, status="ok", latency_ms=_elapsed_ms(started))


async def run_checks(checks: Mapping[str, HealthCheck], timeout_s: float) -> list[ComponentStatus]:
    return list(
        await asyncio.gather(*(run_check(name, check, timeout_s) for name, check in checks.items()))
    )


def _http_check(client: httpx.AsyncClient, url: str, timeout_s: float) -> HealthCheck:
    async def check() -> None:
        response = await client.get(url, timeout=timeout_s)
        response.raise_for_status()

    return check


def build_health_checks(
    resources: Resources, settings: Settings, timeout_s: float
) -> dict[str, HealthCheck]:
    async def postgres() -> None:
        async with resources.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def valkey() -> None:
        await resources.valkey.ping()  # pyright: ignore[reportUnknownMemberType] (untyped **kwargs upstream)

    return {
        "postgres": postgres,
        "valkey": valkey,
        "qdrant": _http_check(resources.http, f"{settings.qdrant_url}/readyz", timeout_s),
        "openfga": _http_check(resources.http, f"{settings.openfga_api_url}/healthz", timeout_s),
        "keycloak": _http_check(
            resources.http, f"{settings.keycloak_url}/realms/master", timeout_s
        ),
    }

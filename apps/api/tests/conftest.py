from collections.abc import Callable, Iterator, Mapping

import pytest
from fastapi.testclient import TestClient

from workmind.core.config import Settings
from workmind.core.health import HealthCheck
from workmind.main import create_app


async def healthy() -> None:
    return None


async def refused() -> None:
    raise ConnectionRefusedError("connection refused")


ALL_HEALTHY: dict[str, HealthCheck] = {
    name: healthy for name in ("postgres", "valkey", "qdrant", "openfga", "keycloak")
}

ClientFactory = Callable[[Mapping[str, HealthCheck]], TestClient]


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: tests must not depend on a developer's local .env
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        health_check_timeout_s=0.2,
        authz_outbox_worker=False,
    )


@pytest.fixture
def make_client(settings: Settings) -> Iterator[ClientFactory]:
    clients: list[TestClient] = []

    def factory(checks: Mapping[str, HealthCheck]) -> TestClient:
        app = create_app(settings)
        app.state.health_checks = dict(checks)
        client = TestClient(app)
        client.__enter__()  # runs the lifespan
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)

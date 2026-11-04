import asyncio

from tests.conftest import ALL_HEALTHY, ClientFactory, refused
from workmind.core.health import run_check


def test_healthz_is_ok_without_dependencies(make_client: ClientFactory) -> None:
    client = make_client({"postgres": refused})

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_is_200_when_all_components_are_healthy(make_client: ClientFactory) -> None:
    client = make_client(ALL_HEALTHY)

    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert {c["name"] for c in body["components"]} == set(ALL_HEALTHY)
    assert all(c["status"] == "ok" for c in body["components"])


def test_readyz_is_503_and_names_the_failing_component(make_client: ClientFactory) -> None:
    client = make_client({**ALL_HEALTHY, "qdrant": refused})

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    qdrant = next(c for c in body["components"] if c["name"] == "qdrant")
    assert qdrant == {**qdrant, "status": "down", "detail": "ConnectionRefusedError"}


def test_failure_detail_never_leaks_the_exception_message(make_client: ClientFactory) -> None:
    client = make_client({"postgres": refused})

    body = client.get("/api/v1/system/status").json()

    assert "connection refused" not in str(body)


def test_system_status_is_always_200_for_the_ui(make_client: ClientFactory) -> None:
    client = make_client({**ALL_HEALTHY, "keycloak": refused})

    response = client.get("/api/v1/system/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["product"] == "WorkMind AI"
    assert body["environment"] == "test"
    assert body["version"]


async def test_slow_check_times_out_as_down() -> None:
    async def hangs() -> None:
        await asyncio.sleep(5)

    status = await run_check("slow", hangs, timeout_s=0.05)

    assert status.status == "down"
    assert status.detail == "TimeoutError"
    assert status.latency_ms < 1000

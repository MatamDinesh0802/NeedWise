"""GET /api/v1/edition and require_feature against the real app, database and sessions."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from needwise.api.deps import require_feature
from needwise.core.config import Settings
from needwise.core.context import RequestContext
from needwise.core.edition import Edition, License
from needwise.core.extensions import ExtensionRegistry
from needwise.main import create_app
from tests.integration.conftest import VALKEY_URL, Acme, Database

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL to run")

EXPIRES = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)

# What an enterprise package would register: one route per gating style.
mounted = APIRouter()


@mounted.get("/ee-test/retention")
async def retention_status() -> dict[str, str]:
    return {"feature": "retention_policies"}


@mounted.get("/ee-test/chargeback")
async def chargeback(
    ctx: Annotated[RequestContext, require_feature("chargeback_reports")],
) -> dict[str, str]:
    return {"user": str(ctx.user_id)}


def _client(database: Database, edition: Edition) -> TestClient:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        authz_outbox_worker=False,
    )
    registry = ExtensionRegistry(edition)
    registry.add_router(mounted, feature="retention_policies")
    return TestClient(create_app(settings, extensions=registry))


@pytest.fixture
def community(database: Database) -> Iterator[TestClient]:
    with _client(database, Edition.community()) as client:
        yield client


@pytest.fixture
def licensed(database: Database) -> Iterator[TestClient]:
    issued = datetime.now(UTC) - timedelta(days=1)
    lic = License("Acme Robotics", frozenset({"retention_policies"}), issued, EXPIRES, key_id="t")
    with _client(database, Edition(lic)) as client:
        yield client


async def test_the_edition_is_community_without_a_license(api: TestClient, acme: Acme) -> None:
    response = api.get("/api/v1/edition", headers=acme.as_("bob"))
    assert response.status_code == 200
    assert response.json() == {
        "edition": "community",
        "features": [],
        "licensee": None,
        "expires_at": None,
    }


async def test_every_signed_in_user_can_read_the_edition(licensed: TestClient, acme: Acme) -> None:
    for person in ("alice", "mallory"):  # an HR manager and an external contractor
        response = licensed.get("/api/v1/edition", headers=acme.as_(person))
        assert response.status_code == 200
        body = response.json()
        assert body["edition"] == "enterprise"
        assert body["features"] == ["retention_policies"]
        assert body["licensee"] == "Acme Robotics"
        assert datetime.fromisoformat(body["expires_at"]) == EXPIRES
    assert licensed.get("/api/v1/edition").status_code == 401


async def test_a_gated_route_answers_402_without_the_feature(
    community: TestClient, acme: Acme
) -> None:
    for path in ("/api/v1/ee-test/retention", "/api/v1/ee-test/chargeback"):
        response = community.get(path, headers=acme.as_("bob"))
        assert response.status_code == 402, path
        assert response.headers["content-type"] == "application/problem+json"
        problem = response.json()
        assert problem["code"] == "enterprise_feature"
        assert problem["title"] == "Enterprise feature"
        assert "docs/enterprise.md" in problem["detail"]
    assert (
        "'retention_policies'"
        in community.get("/api/v1/ee-test/retention", headers=acme.as_("bob")).json()["detail"]
    )


async def test_a_gated_route_needs_sign_in_before_the_license(community: TestClient) -> None:
    response = community.get("/api/v1/ee-test/retention")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_a_licensed_feature_opens_only_its_own_routes(
    licensed: TestClient, acme: Acme
) -> None:
    allowed = licensed.get("/api/v1/ee-test/retention", headers=acme.as_("bob"))
    assert allowed.status_code == 200
    assert allowed.json() == {"feature": "retention_policies"}

    # Mounted with retention_policies, but this route also needs chargeback_reports.
    other = licensed.get("/api/v1/ee-test/chargeback", headers=acme.as_("bob"))
    assert other.status_code == 402
    assert "'chargeback_reports'" in other.json()["detail"]

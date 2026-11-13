"""Rate limits counted in Valkey (docs/multi-tenancy.md): per person, per tenant and per
client address, never shared across tenants, refused with 429 `rate_limited` and Retry-After.
Needs the test database and TEST_VALKEY_URL."""

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from needwise.core.config import Settings
from needwise.core.ratelimit import RateLimitedError, RateLimiter, merge_limits
from needwise.main import create_app
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL")
WINDOW_START = 1_800_000_000.0  # a fixed clock: every test starts at the beginning of a window
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
async def valkey() -> AsyncIterator[Redis]:
    client = Redis.from_url(VALKEY_URL or "")  # pyright: ignore[reportUnknownMemberType]
    yield client
    await client.aclose()


def limiter(valkey: Redis, **overrides: dict[str, int]) -> RateLimiter:
    limits = merge_limits(overrides)
    clock = [WINDOW_START + uuid.uuid4().int % 1000 * 3600]  # a fresh window per test
    return RateLimiter(valkey, limits, clock=lambda: clock[0])


async def test_a_person_over_their_limit_does_not_use_up_the_tenants(valkey: Redis) -> None:
    limits = limiter(valkey, chat={"per_user": 2, "per_tenant": 3})
    tenant, alice, bob, carol = (uuid.uuid4() for _ in range(4))

    await limits.check(tenant, alice, "chat")
    await limits.check(tenant, alice, "chat")
    with pytest.raises(RateLimitedError) as refused:
        await limits.check(tenant, alice, "chat")
    for _ in range(3):  # refused again and again: none of these count for the tenant
        with pytest.raises(RateLimitedError):
            await limits.check(tenant, alice, "chat")
    await limits.check(tenant, bob, "chat")  # the tenant's third
    with pytest.raises(RateLimitedError) as tenant_full:
        await limits.check(tenant, carol, "chat")

    assert refused.value.extra["limit"]["scope"] == "user"
    assert tenant_full.value.extra["limit"]["scope"] == "tenant"
    assert 1 <= int(tenant_full.value.headers["Retry-After"]) <= 60


async def test_tenants_never_share_a_limit(valkey: Redis) -> None:
    limits = limiter(valkey, analytics={"per_user": 0, "per_tenant": 2})
    busy, quiet = uuid.uuid4(), uuid.uuid4()

    for _ in range(2):
        await limits.check(busy, uuid.uuid4(), "analytics")
    with pytest.raises(RateLimitedError):
        await limits.check(busy, uuid.uuid4(), "analytics")
    for _ in range(2):
        await limits.check(quiet, uuid.uuid4(), "analytics")  # unaffected

    keys = [k.decode() async for k in valkey.scan_iter(f"t:{busy}:rl:analytics:*")]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    assert keys  # the busy tenant's counters live under its own prefix
    assert not [k async for k in valkey.scan_iter(f"*:rl:analytics:*{quiet}*")]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


async def test_a_new_window_starts_over(valkey: Redis) -> None:
    clock = [WINDOW_START + uuid.uuid4().int % 1000 * 3600]
    limits = RateLimiter(valkey, merge_limits({"sync": {"per_user": 1}}), clock=lambda: clock[0])
    tenant, user = uuid.uuid4(), uuid.uuid4()

    await limits.check(tenant, user, "sync")
    with pytest.raises(RateLimitedError):
        await limits.check(tenant, user, "sync")
    clock[0] += 60
    await limits.check(tenant, user, "sync")


async def test_sign_in_is_counted_per_address(valkey: Redis) -> None:
    limits = limiter(valkey, login={"per_address": 2})
    office, home = f"203.0.113.{uuid.uuid4().int % 250}", "198.51.100.7"

    await limits.check_address(office, "login")
    await limits.check_address(office, "login")
    with pytest.raises(RateLimitedError) as refused:
        await limits.check_address(office, "login")
    await limits.check_address(home, "login")

    assert refused.value.extra["limit"]["scope"] == "address"


# --- Over HTTP ----------------------------------------------------------------------------------


@pytest.fixture
def api(database: Database) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        authz_outbox_worker=False,
        keycloak_url="http://127.0.0.1:9/auth",
        rate_limits={
            "search": {"per_user": 2, "per_tenant": 100},
            "mcp": {"per_user": 2},
            "login": {"per_address": 2},
        },
    )
    address = f"192.0.2.{uuid.uuid4().int % 250}"  # a fresh address for the sign-in limit
    with TestClient(create_app(settings), client=(address, 50000)) as client:
        yield client


@pytest.fixture
async def tenants(owner_sessions: SessionFactory) -> tuple[Acme, Acme]:
    return await seed_acme(owner_sessions), await seed_acme(owner_sessions)


def _search(api: TestClient, acme: Acme, who: str) -> Any:
    return api.post("/api/v1/search", json={"query": "leave"}, headers=acme.as_(who, write=True))


async def test_the_api_answers_429_with_retry_after(
    api: TestClient, tenants: tuple[Acme, Acme]
) -> None:
    acme, globex = tenants
    first, second = _search(api, acme, "bob"), _search(api, acme, "bob")
    refused = _search(api, acme, "bob")

    assert first.status_code != 429
    assert second.status_code != 429
    assert refused.status_code == 429
    assert refused.headers["content-type"] == "application/problem+json"
    assert 1 <= int(refused.headers["retry-after"]) <= 60
    body = refused.json()
    assert body["code"] == "rate_limited"
    assert body["limit"] == {
        "bucket": "search",
        "scope": "user",
        "limit": 2,
        "window_s": 60,
        "retry_after_s": int(refused.headers["retry-after"]),
    }
    assert _search(api, acme, "alice").status_code != 429  # another person
    assert _search(api, globex, "bob").status_code != 429  # the same person's twin elsewhere
    assert api.post("/api/v1/search", json={"query": "x"}).status_code == 401  # sign-in first


async def test_mcp_tokens_are_limited_per_owner(
    api: TestClient, tenants: tuple[Acme, Acme]
) -> None:
    acme, _ = tenants
    token = api.post(
        "/api/v1/me/tokens",
        json={"name": "mcp", "scopes": ["knowledge:read"]},
        headers=acme.as_("bob", write=True),
    ).json()["token"]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {token}"}

    answers = [api.post("/mcp", content=body, headers=headers) for _ in range(3)]

    assert [a.status_code for a in answers] == [200, 200, 429]
    assert answers[2].json()["code"] == "rate_limited"
    assert answers[2].headers["retry-after"]


async def test_sign_in_starts_are_limited_per_address(api: TestClient) -> None:
    answers = [api.get("/api/auth/login", follow_redirects=False) for _ in range(3)]

    assert [a.status_code for a in answers[:2]] == [303, 303]  # Keycloak is unreachable here
    assert answers[2].status_code == 429
    assert answers[2].json()["limit"]["scope"] == "address"

"""One person's analytics queries are counted in Valkey, across every API process
(analytics/slots.py). Needs TEST_VALKEY_URL.

Before the fix each uvicorn process counted its own: with two processes (the default), one
person ran two queries at once, where the threat model says one."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from tests.integration.conftest import VALKEY_URL
from workmind.core.tenancy import key_tenant, tenant_key
from workmind.modules.analytics.slots import ValkeySlots

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL")


@pytest.fixture
async def valkey() -> AsyncIterator[Redis]:
    client = Redis.from_url(VALKEY_URL or "")  # pyright: ignore[reportUnknownMemberType]
    yield client
    await client.aclose()


async def test_two_processes_share_one_persons_slot(valkey: Redis) -> None:
    tenant, dan, carol = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    first_process, second_process = ValkeySlots(valkey), ValkeySlots(valkey)
    dans = tenant_key(tenant, "analytics", "running", dan)
    carols = tenant_key(tenant, "analytics", "running", carol)

    lease = await first_process.take(dans, 1, 30)
    assert lease is not None
    assert await second_process.take(dans, 1, 30) is None  # the other process: still refused
    carol_lease = await second_process.take(carols, 1, 30)
    assert carol_lease is not None
    assert key_tenant(dans) == tenant  # under the tenant's prefix: deleted with the tenant

    await first_process.give_back(dans, lease)
    again = await second_process.take(dans, 1, 30)
    assert again is not None
    await second_process.give_back(dans, again)
    await second_process.give_back(carols, carol_lease)
    assert not await valkey.exists(dans, carols)


async def test_a_lease_whose_process_died_expires(valkey: Redis) -> None:
    owner = tenant_key(uuid.uuid4(), "analytics", "running", uuid.uuid4())
    slots = ValkeySlots(valkey)

    assert await slots.take(owner, 1, 0.3) is not None  # never given back
    assert await slots.take(owner, 1, 0.3) is None
    await asyncio.sleep(0.4)

    lease = await slots.take(owner, 1, 0.3)
    assert lease is not None
    await slots.give_back(owner, lease)


async def test_without_valkey_the_slots_are_counted_in_the_process() -> None:
    unreachable = Redis.from_url("redis://127.0.0.1:1/0")  # pyright: ignore[reportUnknownMemberType]
    slots = ValkeySlots(unreachable)
    owner = tenant_key(uuid.uuid4(), "analytics", "running", uuid.uuid4())
    try:
        lease = await slots.take(owner, 1, 5)
        assert lease is not None
        assert await slots.take(owner, 1, 5) is None
        await slots.give_back(owner, lease)
        assert await slots.take(owner, 1, 5) is not None
    finally:
        await unreachable.aclose()

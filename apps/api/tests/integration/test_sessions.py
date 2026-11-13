"""Session store against a real Valkey (TEST_VALKEY_URL, e.g. redis://localhost:6379/15)."""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from needwise.modules.identity.sessions import SessionStore

VALKEY_URL = os.environ.get("TEST_VALKEY_URL")
pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL to run")


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def valkey() -> AsyncIterator[Redis]:
    assert VALKEY_URL is not None
    client = Redis.from_url(VALKEY_URL)  # pyright: ignore[reportUnknownMemberType]
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


def _store(valkey: Redis, clock: Clock) -> SessionStore:
    return SessionStore(valkey, idle_timeout_s=1800, absolute_timeout_s=3600, clock=clock)


async def _create(store: SessionStore, *, sid: str = "kc-session", sub: str = "kc-user") -> str:
    return await store.create(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        keycloak_sub=sub,
        keycloak_sid=sid,
        id_token="id-token",
    )


async def test_round_trip_and_unknown_ids(valkey: Redis) -> None:
    store = _store(valkey, Clock())
    session_id = await _create(store)

    session = await store.get(session_id)
    assert session is not None
    assert session.keycloak_sid == "kc-session"
    assert await store.get("not-a-session") is None
    assert await store.get("x" * 500) is None


async def test_valkey_never_stores_the_raw_session_id(valkey: Redis) -> None:
    session_id = await _create(_store(valkey, Clock()))

    keys = [str(key) for key in await valkey.keys("*")]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    assert keys
    assert not any(session_id in key for key in keys)


async def test_absolute_lifetime_ends_the_session_even_when_active(valkey: Redis) -> None:
    clock = Clock()
    store = _store(valkey, clock)
    session_id = await _create(store)

    clock.now += 3599
    assert await store.get(session_id) is not None
    clock.now += 2
    assert await store.get(session_id) is None


async def test_idle_timeout_is_enforced_by_key_expiry(valkey: Redis) -> None:
    session_id = await _create(_store(valkey, Clock()))
    key = next(k for k in await valkey.keys("session:*"))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    assert 0 < await valkey.ttl(key) <= 1800
    assert session_id


async def test_back_channel_logout_ends_every_session_of_that_keycloak_session(
    valkey: Redis,
) -> None:
    store = _store(valkey, Clock())
    laptop, phone = await _create(store, sid="kc-1"), await _create(store, sid="kc-1")
    other = await _create(store, sid="kc-2", sub="someone-else")

    assert await store.delete_by_keycloak_session("kc-1") == 2
    assert await store.get(laptop) is None
    assert await store.get(phone) is None
    assert await store.get(other) is not None


async def test_the_tenant_index_keeps_only_one_lifetime_of_sign_ins(valkey: Redis) -> None:
    # Regression: the index was a set with one member per sign-in, never removed, so in a
    # tenant with a sign-in every few hours it only grew (and ending its sessions read it all).
    clock = Clock()
    store = _store(valkey, clock)
    tenant = uuid.uuid4()

    async def sign_in() -> str:
        return await store.create(
            user_id=uuid.uuid4(),
            tenant_id=tenant,
            keycloak_sub="kc-user",
            keycloak_sid=None,
            id_token="t",
        )

    for _ in range(5):
        await sign_in()
        clock.now += 3600 + 1  # past the absolute lifetime
    live = await sign_in()

    index = f"t:{tenant}:sessions:by-expiry"
    assert await valkey.zcard(index) == 1  # pyright: ignore[reportUnknownMemberType]
    assert await store.delete_by_tenant(tenant) == 1
    assert await store.get(live) is None


async def test_ending_a_tenants_sessions_also_reads_the_older_index(valkey: Redis) -> None:
    store = _store(valkey, Clock())
    tenant = uuid.uuid4()
    session_id = await store.create(
        user_id=uuid.uuid4(), tenant_id=tenant, keycloak_sub="s", keycloak_sid=None, id_token="t"
    )
    # A session recorded by the previous version: only in the plain set.
    keys = [k async for k in valkey.scan_iter("session:*")]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    await valkey.delete(f"t:{tenant}:sessions:by-expiry")
    await valkey.sadd(f"t:{tenant}:sessions", *keys)  # pyright: ignore[reportUnknownMemberType, reportGeneralTypeIssues, reportUnknownArgumentType]

    assert await store.delete_by_tenant(tenant) == 1
    assert await store.get(session_id) is None
    assert not await valkey.exists(f"t:{tenant}:sessions")

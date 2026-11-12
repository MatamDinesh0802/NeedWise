"""Unbounded consumption (docs/security/owasp-llm-top10.md, LLM10): how many answers one person
may have in progress at once.

Before the fix only a conversation's own lock limited runs: one session or access token could
start an answer in each of a thousand conversations at once, and with a local (unpriced) model
no budget stops it. Needs TEST_VALKEY_URL.
"""

import time
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from tests.integration.conftest import VALKEY_URL, Acme, Database
from workmind.core.config import Settings
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.main import create_app
from workmind.modules.conversations.inflight import InFlight

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL")


def person(tenant_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> RequestContext:
    return RequestContext(
        request_id="t",
        tenant_id=tenant_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        org_role="member",
        principals=frozenset(),
        auth_method="session",
    )


@pytest.fixture
async def valkey() -> AsyncIterator[Redis]:
    client = Redis.from_url(VALKEY_URL or "")  # pyright: ignore[reportUnknownMemberType]
    yield client
    await client.aclose()


async def test_slots_are_per_person_freed_on_release_and_expire(valkey: Redis) -> None:
    inflight = InFlight(valkey, limit=2, ttl_s=60)
    bob, alice = person(), person()

    assert await inflight.acquire(bob, "a")
    assert await inflight.acquire(bob, "b")
    assert not await inflight.acquire(bob, "c")  # at the limit: refused, nothing taken
    assert await inflight.acquire(alice, "a")  # someone else is not affected
    await inflight.release(bob, "a")
    assert await inflight.acquire(bob, "c")

    # A slot older than the TTL (its process died) no longer counts.
    await valkey.zadd(InFlight.key(alice), {"stale": time.time() - 120})
    assert await inflight.acquire(alice, "b")


async def test_hold_refuses_with_429(valkey: Redis) -> None:
    inflight = InFlight(valkey, limit=1)
    bob = person()

    async with inflight.hold(bob):
        with pytest.raises(ApiError) as refused:
            async with inflight.hold(bob):
                pass
    assert refused.value.status == 429
    async with inflight.hold(bob):  # freed when the block ended
        pass


@pytest.fixture
def limited_api(database: Database) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        authz_outbox_worker=False,
        max_inflight_per_user=1,
    )
    with TestClient(create_app(settings)) as client:
        yield client


async def test_a_person_at_the_limit_cannot_start_another_answer_anywhere(
    limited_api: TestClient, acme: Acme, valkey: Redis
) -> None:
    bob = person(acme.tenant_id, acme.users["bob"])
    # One answer already in progress (a run in another conversation, held by any API process).
    await valkey.zadd(InFlight.key(bob), {"running-elsewhere": time.time()})
    write = acme.as_("bob", write=True)
    conversation = limited_api.post("/api/v1/conversations", json={}, headers=write).json()["id"]

    chat = limited_api.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"content": "How long is parental leave?"},
        headers=write,
    )
    ask = limited_api.post("/api/v1/ask", json={"question": "Leave?"}, headers=write)
    research = limited_api.post("/api/v1/research", json={"question": "Leave?"}, headers=write)

    assert chat.status_code == 429
    assert chat.json()["code"] == "too_many_requests"
    assert ask.status_code == 429
    assert research.status_code == 429
    # Refused before anything was stored: the conversation is free and has no question in it.
    detail = limited_api.get(f"/api/v1/conversations/{conversation}", headers=write).json()
    assert detail["messages"] == []
    assert detail["active_run_id"] is None
    # Someone else is not affected by Bob's runs.
    other = limited_api.post(
        "/api/v1/conversations", json={}, headers=acme.as_("alice", write=True)
    )
    assert other.status_code == 201

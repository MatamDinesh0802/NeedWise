"""Tenant scoping helpers without infrastructure: Valkey key names and Qdrant filters.
The end-to-end checks are in tests/integration/test_cross_tenant.py."""

import uuid

import httpx
import pytest

from needwise.core.tenancy import GLOBAL_KEY_PREFIXES, key_tenant, tenant_key, tenant_key_pattern
from needwise.modules.retrieval.qdrant import QdrantClient, match, scoped

A = uuid.UUID("00000000-0000-7000-8000-00000000000a")
B = uuid.UUID("00000000-0000-7000-8000-00000000000b")


def test_tenant_keys_start_with_the_tenant_and_parse_back() -> None:
    key = tenant_key(A, "run", "r1", "events")

    assert key == f"t:{A}:run:r1:events"
    assert key_tenant(key) == A
    assert key_tenant(f"t:{B}:usage") == B
    assert key_tenant("session:abc") is None
    assert key_tenant("t:not-a-uuid:x") is None
    assert tenant_key_pattern(A) == f"t:{A}:*"


def test_global_prefixes_never_look_like_a_tenant_key() -> None:
    assert all(not p.startswith("t:") for p in GLOBAL_KEY_PREFIXES)


def test_the_tenant_condition_joins_must_and_keeps_the_callers_conditions() -> None:
    caller = {"must": [match("document_id", "d1")], "must_not": [match("trust", "discussion")]}

    narrowed = scoped(A, caller)

    assert narrowed["must"] == [match("tenant_id", str(A)), match("document_id", "d1")]
    assert narrowed["must_not"] == caller["must_not"]
    assert caller["must"] == [match("document_id", "d1")]  # the caller's filter is not changed


@pytest.mark.parametrize(
    "crafted",
    [
        None,
        {},
        {"must": []},
        {"must": match("tenant_id", str(B))},  # a single condition, not a list
        {"should": [match("tenant_id", str(B))]},
        {"must": [match("tenant_id", str(B))]},
        {"must": [{"should": [match("tenant_id", str(B))]}]},
    ],
)
def test_no_filter_a_caller_builds_drops_the_tenant_condition(crafted: object) -> None:
    narrowed = scoped(A, crafted)  # pyright: ignore[reportArgumentType]

    # Every point must satisfy every `must` condition: the first one pins tenant A.
    assert narrowed["must"][0] == match("tenant_id", str(A))


async def test_points_are_only_written_for_their_own_tenant() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no request expected, got {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        client = QdrantClient(http, "http://qdrant")
        point = {"id": "p1", "vector": {}, "payload": {"tenant_id": str(B)}}
        with pytest.raises(ValueError, match="belong to the tenant"):
            await client.upsert("chunks", [point], tenant_id=A)
        with pytest.raises(ValueError, match="never moves"):
            await client.set_payload("chunks", {"tenant_id": str(B)}, {}, tenant_id=A)

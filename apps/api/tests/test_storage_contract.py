"""One contract for every ObjectStorage implementation (ARCHITECTURE §23: an adapter counts
when its implementations pass a shared suite). Today: the local directory and the in-memory
store; an S3-compatible store is not built."""

import uuid
from pathlib import Path

import pytest

from needwise.modules.knowledge.storage import (
    ForeignObjectKeyError,
    LocalObjectStorage,
    MemoryObjectStorage,
    ObjectStorage,
    object_key,
    owned_key,
    tenant_prefix,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture(params=["local", "memory"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> ObjectStorage:
    if request.param == "local":
        return LocalObjectStorage(tmp_path)
    return MemoryObjectStorage()


async def test_put_get_overwrite_and_delete(storage: ObjectStorage) -> None:
    tenant = uuid.uuid4()
    key = object_key(tenant, HASH_A)
    await storage.put(key, b"first")
    assert await storage.get(key) == b"first"
    await storage.put(key, b"second")  # content-addressed: the same bytes in practice
    assert await storage.get(key) == b"second"
    await storage.delete(key)
    await storage.delete(key)  # deleting what is gone is fine
    with pytest.raises(FileNotFoundError):
        await storage.get(key)


async def test_listing_and_deleting_stay_inside_a_tenants_prefix(storage: ObjectStorage) -> None:
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    for tenant in (mine, theirs):
        for digest in (HASH_A, HASH_B):
            await storage.put(object_key(tenant, digest), digest.encode())

    assert await storage.list_keys(tenant_prefix(mine)) == sorted(
        [object_key(mine, HASH_A), object_key(mine, HASH_B)]
    )
    assert await storage.delete_prefix(tenant_prefix(mine)) == 2
    assert await storage.list_keys(tenant_prefix(mine)) == []
    assert await storage.delete_prefix(tenant_prefix(mine)) == 0
    assert len(await storage.list_keys(tenant_prefix(theirs))) == 2


def test_keys_are_built_from_hashes_and_checked_against_the_tenant() -> None:
    tenant = uuid.uuid4()
    with pytest.raises(ValueError, match="SHA-256"):
        object_key(tenant, "../../etc/passwd")
    key = object_key(tenant, HASH_A)
    assert owned_key(tenant, key) == key
    with pytest.raises(ForeignObjectKeyError):
        owned_key(uuid.uuid4(), key)
    with pytest.raises(ForeignObjectKeyError):
        owned_key(tenant, f"{tenant_prefix(tenant)}../other")

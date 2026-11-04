"""Object storage for original files (ARCHITECTURE section 23). Keys are tenant-prefixed and
content-addressed, so identical uploads are stored once per tenant.

Implementations: LocalObjectStorage (a directory: dev, single server) and MemoryObjectStorage
(tests). An S3-compatible implementation arrives with the production hardening phase.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Protocol


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


def object_key(tenant_id: uuid.UUID, content_hash: str) -> str:
    return f"tenants/{tenant_id}/objects/{content_hash[:2]}/{content_hash}"


class LocalObjectStorage:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root not in path.parents:  # never escape the storage root
            raise ValueError(f"invalid storage key: {key}")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic: readers never see half a file

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

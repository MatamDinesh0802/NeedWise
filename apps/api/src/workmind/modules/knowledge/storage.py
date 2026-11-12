"""Object storage for original files (ARCHITECTURE section 23). Keys are tenant-prefixed and
content-addressed, so identical uploads are stored once per tenant.

Keys are built here (`object_key`: tenants/<tenant_id>/objects/<sha256>), never from a file
name, and every key read from the database is checked against the reading tenant
(`owned_key`) before it is used. Deleting a tenant removes its whole prefix (`delete_prefix`).

Implementations: LocalObjectStorage (a directory: dev, single server) and MemoryObjectStorage
(tests). An S3-compatible implementation arrives with the production hardening phase.
"""

import asyncio
import re
import shutil
import uuid
from pathlib import Path
from typing import Protocol

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    async def list_keys(self, prefix: str) -> list[str]: ...


class ForeignObjectKeyError(PermissionError):
    """A stored key outside the tenant's prefix: never read, never deleted."""


def tenant_prefix(tenant_id: uuid.UUID) -> str:
    return f"tenants/{tenant_id}/"


def object_key(tenant_id: uuid.UUID, content_hash: str) -> str:
    if not _SHA256.match(content_hash):
        raise ValueError("object keys are built from a SHA-256, never from a name")
    return f"{tenant_prefix(tenant_id)}objects/{content_hash[:2]}/{content_hash}"


def owned_key(tenant_id: uuid.UUID, key: str) -> str:
    """`key`, if it belongs to the tenant: checked before reading or deleting a key that came
    from the database, so even a wrong row cannot reach another tenant's file."""
    parts = key.split("/")
    if not key.startswith(tenant_prefix(tenant_id)) or ".." in parts or "" in parts:
        raise ForeignObjectKeyError(f"storage key outside tenant {tenant_id}")
    return key


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

    async def delete_prefix(self, prefix: str) -> int:
        """Removes every object under `prefix` (a directory); returns how many files went."""
        directory = self._path(prefix.rstrip("/"))

        def remove() -> int:
            if not directory.is_dir():
                return 0
            count = sum(1 for p in directory.rglob("*") if p.is_file())
            shutil.rmtree(directory)
            return count

        return await asyncio.to_thread(remove)

    async def list_keys(self, prefix: str) -> list[str]:
        directory = self._path(prefix.rstrip("/"))

        def walk() -> list[str]:
            if not directory.is_dir():
                return []
            return sorted(
                str(p.relative_to(self._root)) for p in directory.rglob("*") if p.is_file()
            )

        return await asyncio.to_thread(walk)


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self.objects if k.startswith(prefix)]
        for key in doomed:
            del self.objects[key]
        return len(doomed)

    async def list_keys(self, prefix: str) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))

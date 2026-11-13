"""A thin Qdrant REST client: only the calls WorkMind uses (ARCHITECTURE section 11.2).

Every call on points takes the tenant and adds the tenant condition itself (`scoped`), so no
caller can forget it and no filter a caller builds can widen it to another tenant's points.
Upserts refuse points whose payload names another tenant. `count_all` is the one exception:
an operator's total over every tenant (evaluation reports), never used to answer a request.
"""

import contextlib
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

PAYLOAD_INDEXES: dict[str, dict[str, Any]] = {
    "tenant_id": {"type": "keyword", "is_tenant": True},  # tenant-optimized storage layout
    "acl": {"type": "keyword"},
    # A document's own access list (ADR 0012): only narrows `acl`.
    "own_acl": {"type": "bool"},
    "doc_acl": {"type": "keyword"},
    "document_id": {"type": "keyword"},
    "collection_id": {"type": "keyword"},
    "trust": {"type": "keyword"},
    "sensitivity": {"type": "keyword"},
    "is_active": {"type": "bool"},
}


class QdrantError(RuntimeError):
    pass


class QdrantConflictError(QdrantError):
    """HTTP 409: e.g. the collection was created by someone else a moment ago."""


def scoped(tenant_id: uuid.UUID, flt: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The caller's filter, narrowed to one tenant. The tenant condition joins `must`, which
    every point must satisfy, so nothing else in the filter (a `should`, another tenant's id in
    a condition, an empty filter) can match another tenant's points."""
    narrowed: dict[str, Any] = dict(flt or {})
    given: Any = narrowed.get("must") or []
    conditions: list[Any] = [given] if isinstance(given, Mapping) else list(given)  # pyright: ignore[reportUnknownArgumentType]
    narrowed["must"] = [match("tenant_id", str(tenant_id)), *conditions]
    return narrowed


class QdrantClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str | None = None) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._headers = {"api-key": api_key} if api_key else {}
        self._indexed: set[str] = set()  # collections whose payload indexes this process ensured

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._http.request(
                method, f"{self._base}{path}", json=body, headers=self._headers, timeout=60
            )
        except httpx.HTTPError as exc:
            raise QdrantError(f"Qdrant unreachable: {exc!r}") from exc
        if response.is_error:
            error = QdrantConflictError if response.status_code == 409 else QdrantError
            raise error(f"Qdrant HTTP {response.status_code} on {path}: {response.text[:300]}")
        return response.json().get("result")

    async def delete_collection(self, name: str) -> None:
        await self._call("DELETE", f"/collections/{name}")
        self._indexed.discard(name)

    async def collection_exists(self, name: str) -> bool:
        return bool((await self._call("GET", f"/collections/{name}/exists"))["exists"])

    async def ensure_collection(self, name: str, *, dense_size: int) -> None:
        """Idempotent and safe under concurrency: parallel ingestions of a brand-new index (or
        several workers) may all find it missing; whoever loses the race just carries on. The
        payload indexes are ensured once per process, so an index added in an upgrade (own_acl,
        doc_acl) also reaches a collection created before it."""
        if await self.collection_exists(name):
            if name in self._indexed:
                return
        else:
            # Created concurrently by someone else: fine, the payload indexes are idempotent.
            with contextlib.suppress(QdrantConflictError):
                await self._call(
                    "PUT",
                    f"/collections/{name}",
                    {
                        "vectors": {"dense": {"size": dense_size, "distance": "Cosine"}},
                        "sparse_vectors": {"bm25": {"modifier": "idf"}},
                    },
                )
        for field, schema in PAYLOAD_INDEXES.items():
            await self._call(
                "PUT",
                f"/collections/{name}/index?wait=true",
                {"field_name": field, "field_schema": schema},
            )
        self._indexed.add(name)

    async def list_collections(self) -> list[str]:
        result = await self._call("GET", "/collections")
        return sorted(c["name"] for c in result.get("collections", []))

    async def upsert(
        self, name: str, points: Sequence[dict[str, Any]], *, tenant_id: uuid.UUID
    ) -> None:
        if any(p.get("payload", {}).get("tenant_id") != str(tenant_id) for p in points):
            raise ValueError("every point must belong to the tenant it is written for")
        for start in range(0, len(points), 128):
            await self._call(
                "PUT",
                f"/collections/{name}/points?wait=true",
                {"points": list(points[start : start + 128])},
            )

    async def delete(
        self, name: str, flt: Mapping[str, Any] | None = None, *, tenant_id: uuid.UUID
    ) -> None:
        """Deletes the tenant's points that match `flt` (all of them when it is empty)."""
        await self._call(
            "POST",
            f"/collections/{name}/points/delete?wait=true",
            {"filter": scoped(tenant_id, flt)},
        )

    async def set_payload(
        self,
        name: str,
        payload: dict[str, Any],
        flt: Mapping[str, Any],
        *,
        tenant_id: uuid.UUID,
    ) -> None:
        if "tenant_id" in payload:
            raise ValueError("a point never moves to another tenant")
        await self._call(
            "POST",
            f"/collections/{name}/points/payload?wait=true",
            {"payload": payload, "filter": scoped(tenant_id, flt)},
        )

    async def count(
        self, name: str, flt: Mapping[str, Any] | None = None, *, tenant_id: uuid.UUID
    ) -> int:
        return await self._count(name, scoped(tenant_id, flt))

    async def count_all(self, name: str) -> int:
        """Points of every tenant: for operators' reports only."""
        return await self._count(name, {"must": []})

    async def _count(self, name: str, flt: dict[str, Any]) -> int:
        return int(
            (
                await self._call(
                    "POST", f"/collections/{name}/points/count", {"filter": flt, "exact": True}
                )
            )["count"]
        )

    async def query(
        self,
        name: str,
        *,
        tenant_id: uuid.UUID,
        query: Any,
        using: str,
        flt: Mapping[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        """A single-vector query (dense or BM25), within one tenant's points."""
        result = await self._call(
            "POST",
            f"/collections/{name}/points/query",
            {
                "query": query,
                "using": using,
                "filter": scoped(tenant_id, flt),
                "limit": limit,
                "with_payload": True,
            },
        )
        return list(result["points"])


def match(field: str, value: Any) -> dict[str, Any]:
    return {"key": field, "match": {"value": value}}


def match_any(field: str, values: Sequence[Any]) -> dict[str, Any]:
    return {"key": field, "match": {"any": list(values)}}

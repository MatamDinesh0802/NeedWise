"""A thin Qdrant REST client: only the calls WorkMind uses (ARCHITECTURE section 11.2)."""

from collections.abc import Sequence
from typing import Any

import httpx

PAYLOAD_INDEXES: dict[str, dict[str, Any]] = {
    "tenant_id": {"type": "keyword", "is_tenant": True},  # tenant-optimized storage layout
    "acl": {"type": "keyword"},
    "document_id": {"type": "keyword"},
    "collection_id": {"type": "keyword"},
    "trust": {"type": "keyword"},
    "sensitivity": {"type": "keyword"},
    "is_active": {"type": "bool"},
}


class QdrantError(RuntimeError):
    pass


class QdrantClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str | None = None) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._headers = {"api-key": api_key} if api_key else {}

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._http.request(
                method, f"{self._base}{path}", json=body, headers=self._headers, timeout=60
            )
        except httpx.HTTPError as exc:
            raise QdrantError(f"Qdrant unreachable: {exc!r}") from exc
        if response.is_error:
            raise QdrantError(
                f"Qdrant HTTP {response.status_code} on {path}: {response.text[:300]}"
            )
        return response.json().get("result")

    async def delete_collection(self, name: str) -> None:
        await self._call("DELETE", f"/collections/{name}")

    async def collection_exists(self, name: str) -> bool:
        return bool((await self._call("GET", f"/collections/{name}/exists"))["exists"])

    async def ensure_collection(self, name: str, *, dense_size: int) -> None:
        if await self.collection_exists(name):
            return
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

    async def upsert(self, name: str, points: Sequence[dict[str, Any]]) -> None:
        for start in range(0, len(points), 128):
            await self._call(
                "PUT",
                f"/collections/{name}/points?wait=true",
                {"points": list(points[start : start + 128])},
            )

    async def delete(self, name: str, flt: dict[str, Any]) -> None:
        await self._call("POST", f"/collections/{name}/points/delete?wait=true", {"filter": flt})

    async def set_payload(self, name: str, payload: dict[str, Any], flt: dict[str, Any]) -> None:
        await self._call(
            "POST",
            f"/collections/{name}/points/payload?wait=true",
            {"payload": payload, "filter": flt},
        )

    async def count(self, name: str, flt: dict[str, Any]) -> int:
        return int(
            (
                await self._call(
                    "POST", f"/collections/{name}/points/count", {"filter": flt, "exact": True}
                )
            )["count"]
        )

    async def query(
        self, name: str, *, query: Any, using: str, flt: dict[str, Any], limit: int
    ) -> list[dict[str, Any]]:
        """A single-vector query (dense or BM25 alone), used for ablations."""
        result = await self._call(
            "POST",
            f"/collections/{name}/points/query",
            {"query": query, "using": using, "filter": flt, "limit": limit, "with_payload": True},
        )
        return list(result["points"])


def match(field: str, value: Any) -> dict[str, Any]:
    return {"key": field, "match": {"value": value}}


def match_any(field: str, values: Sequence[Any]) -> dict[str, Any]:
    return {"key": field, "match": {"any": list(values)}}

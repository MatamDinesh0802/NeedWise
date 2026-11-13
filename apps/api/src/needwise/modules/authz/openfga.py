"""A thin OpenFGA HTTP client: only the calls NeedWise uses."""

import asyncio
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import httpx

MAX_TUPLES_PER_WRITE = 100  # OpenFGA's default limit
MAX_CHECKS_PER_BATCH = 50  # OpenFGA's default limit
RETRY_DELAYS = (0.2, 0.8, 2.0)  # seconds between attempts of a read-only call


@dataclass(frozen=True, order=True)
class RelationTuple:
    user: str  # "user:<id>", or a userset such as "group:<id>#member"
    relation: str
    object: str  # "<type>:<id>"

    def key(self) -> dict[str, str]:
        return {"user": self.user, "relation": self.relation, "object": self.object}

    @classmethod
    def from_key(cls, key: dict[str, Any]) -> "RelationTuple":
        return cls(user=key["user"], relation=key["relation"], object=key["object"])

    @property
    def object_type(self) -> str:
        return self.object.split(":", 1)[0]


class OpenFgaError(RuntimeError):
    pass


def authorization_model() -> dict[str, Any]:
    """The compiled model shipped with the package (generated from model.fga)."""
    return json.loads(files("needwise.modules.authz").joinpath("openfga/model.json").read_text())


class OpenFgaClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str | None) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def create_store(self, name: str) -> str:
        return str((await self._post("/stores", {"name": name}))["id"])

    async def delete_store(self, store_id: str) -> None:
        response = await self._http.delete(f"{self._base}/stores/{store_id}", headers=self._headers)
        self._raise_for(response)

    async def store_exists(self, store_id: str) -> bool:
        """False for a deleted store too (OpenFGA answers 404 for both)."""
        response = await self._http.get(f"{self._base}/stores/{store_id}", headers=self._headers)
        if response.status_code == 404:
            return False
        self._raise_for(response)
        return True

    async def stores_named(self, name: str) -> list[str]:
        """Ids of the live stores with this name (one per tenant: "tenant-<slug>")."""
        ids: list[str] = []
        token = ""
        while True:
            response = await self._http.get(
                f"{self._base}/stores",
                params={"name": name, "page_size": 100, "continuation_token": token},
                headers=self._headers,
            )
            self._raise_for(response)
            body = response.json()
            ids += [str(s["id"]) for s in body.get("stores", []) if s.get("name") == name]
            token = body.get("continuation_token", "")
            if not token:
                return ids

    async def write_model(self, store_id: str, model: dict[str, Any]) -> str:
        body = await self._post(f"/stores/{store_id}/authorization-models", model)
        return str(body["authorization_model_id"])

    async def write(
        self,
        store_id: str,
        *,
        writes: Iterable[RelationTuple] = (),
        deletes: Iterable[RelationTuple] = (),
    ) -> None:
        """Idempotent: existing writes and missing deletes are ignored, so retries are safe."""
        pending = [("writes", t) for t in sorted(set(writes))]
        pending += [("deletes", t) for t in sorted(set(deletes))]
        for start in range(0, len(pending), MAX_TUPLES_PER_WRITE):
            chunk = pending[start : start + MAX_TUPLES_PER_WRITE]
            body: dict[str, Any] = {}
            if chunk_writes := [t.key() for kind, t in chunk if kind == "writes"]:
                body["writes"] = {"tuple_keys": chunk_writes, "on_duplicate": "ignore"}
            if chunk_deletes := [t.key() for kind, t in chunk if kind == "deletes"]:
                body["deletes"] = {"tuple_keys": chunk_deletes, "on_missing": "ignore"}
            await self._post(f"/stores/{store_id}/write", body)

    async def read_all(self, store_id: str) -> list[RelationTuple]:
        tuples: list[RelationTuple] = []
        token = ""
        while True:
            body = await self._post(
                f"/stores/{store_id}/read", {"page_size": 100, "continuation_token": token}
            )
            tuples += [RelationTuple.from_key(t["key"]) for t in body.get("tuples", [])]
            token = body.get("continuation_token", "")
            if not token:
                return tuples

    async def check(self, store_id: str, question: RelationTuple) -> bool:
        body = await self._query(f"/stores/{store_id}/check", {"tuple_key": question.key()})
        return bool(body.get("allowed"))

    async def batch_check(
        self, store_id: str, questions: Sequence[RelationTuple]
    ) -> dict[RelationTuple, bool]:
        """Individual checks can fail inside a successful response (e.g. "deadline_exceeded"
        when OpenFGA is overloaded); only those are asked again. Never guesses an answer."""
        answers: dict[RelationTuple, bool] = {}
        pending = list(questions)
        for delay in (*RETRY_DELAYS, None):
            failed: list[tuple[RelationTuple, object]] = []
            for start in range(0, len(pending), MAX_CHECKS_PER_BATCH):
                chunk = pending[start : start + MAX_CHECKS_PER_BATCH]
                body = await self._query(
                    f"/stores/{store_id}/batch-check",
                    {
                        "checks": [
                            {"tuple_key": q.key(), "correlation_id": f"c{i}"}
                            for i, q in enumerate(chunk)
                        ]
                    },
                )
                results: dict[str, Any] = body.get("result", {})
                for i, question in enumerate(chunk):
                    result = results.get(f"c{i}", {})
                    if "error" in result:
                        failed.append((question, result["error"]))
                    else:
                        answers[question] = bool(result.get("allowed"))
            if not failed:
                return answers
            if delay is None:
                question, error = failed[0]
                raise OpenFgaError(f"check failed for {question}: {error}")
            pending = [question for question, _ in failed]
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def list_objects(
        self, store_id: str, *, user: str, relation: str, type: str
    ) -> list[str]:
        body = await self._query(
            f"/stores/{store_id}/list-objects", {"user": user, "relation": relation, "type": type}
        )
        return sorted(body.get("objects", []))

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._http.post(f"{self._base}{path}", json=body, headers=self._headers)
        self._raise_for(response)
        return response.json()

    async def _query(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Read-only calls (checks, list-objects) are safe to retry when OpenFGA is overloaded
        (HTTP 5xx "deadline_exceeded", 429) or briefly unreachable. Writes are not retried here:
        the outbox retries them as a whole."""
        for delay in (*RETRY_DELAYS, None):
            try:
                response = await self._http.post(
                    f"{self._base}{path}", json=body, headers=self._headers
                )
            except httpx.TransportError:
                if delay is None:
                    raise
            else:
                if (
                    not (response.status_code >= 500 or response.status_code == 429)
                    or delay is None
                ):
                    self._raise_for(response)
                    return response.json()
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.is_error:
            raise OpenFgaError(f"OpenFGA HTTP {response.status_code}: {response.text[:300]}")

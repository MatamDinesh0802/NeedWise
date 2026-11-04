"""A thin OpenFGA HTTP client: only the calls WorkMind uses."""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import httpx

MAX_TUPLES_PER_WRITE = 100  # OpenFGA's default limit
MAX_CHECKS_PER_BATCH = 50  # OpenFGA's default limit


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
    return json.loads(files("workmind.modules.authz").joinpath("openfga/model.json").read_text())


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
        body = await self._post(f"/stores/{store_id}/check", {"tuple_key": question.key()})
        return bool(body.get("allowed"))

    async def batch_check(
        self, store_id: str, questions: Sequence[RelationTuple]
    ) -> dict[RelationTuple, bool]:
        answers: dict[RelationTuple, bool] = {}
        for start in range(0, len(questions), MAX_CHECKS_PER_BATCH):
            chunk = questions[start : start + MAX_CHECKS_PER_BATCH]
            body = await self._post(
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
                    raise OpenFgaError(f"check failed for {question}: {result['error']}")
                answers[question] = bool(result.get("allowed"))
        return answers

    async def list_objects(
        self, store_id: str, *, user: str, relation: str, type: str
    ) -> list[str]:
        body = await self._post(
            f"/stores/{store_id}/list-objects", {"user": user, "relation": relation, "type": type}
        )
        return sorted(body.get("objects", []))

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._http.post(f"{self._base}{path}", json=body, headers=self._headers)
        self._raise_for(response)
        return response.json()

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.is_error:
            raise OpenFgaError(f"OpenFGA HTTP {response.status_code}: {response.text[:300]}")

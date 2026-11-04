"""Embedding providers (ARCHITECTURE section 23). Two implementations behind one interface:

- OpenAICompatibleEmbeddings: any `/v1/embeddings` server. That covers llama.cpp
  (`llama-server --embeddings`, the default for local development), Ollama, vLLM, LM Studio,
  Text Embeddings Inference, and hosted APIs such as OpenAI.
- HashEmbeddings: deterministic bag-of-words vectors with no model at all, for tests and CI.
  They capture word overlap, not meaning.

Many embedding models expect task prefixes (EmbeddingGemma: "task: search result | query: ..."),
so queries and documents go through configurable templates.
"""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

import httpx


class EmbeddingError(RuntimeError):
    pass


class EmbeddingUnavailableError(EmbeddingError):
    """The server is down or overloaded: worth retrying later."""


class EmbeddingRejectedError(EmbeddingError):
    """The server refused this input (e.g. too long): retrying the same input cannot help."""


class EmbeddingProvider(Protocol):
    model_id: str  # stored with every chunk; changing it means re-embedding (ARCHITECTURE 8)
    dimensions: int

    async def embed_documents(
        self, texts: Sequence[str], *, titles: Sequence[str] | None = None
    ) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class OpenAICompatibleEmbeddings:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        model: str,
        dimensions: int,
        api_key: str | None = None,
        query_template: str = "{text}",
        document_template: str = "{text}",
        batch_size: int = 32,
    ) -> None:
        self._http = http
        self._url = base_url.rstrip("/") + "/embeddings"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model_id = model
        self.dimensions = dimensions
        self._query_template = query_template
        self._document_template = document_template
        self._batch_size = batch_size

    async def embed_documents(
        self, texts: Sequence[str], *, titles: Sequence[str] | None = None
    ) -> list[list[float]]:
        titles = titles or ["none"] * len(texts)
        prompts = [
            self._document_template.format(text=text, title=title or "none")
            for text, title in zip(texts, titles, strict=True)
        ]
        vectors: list[list[float]] = []
        for start in range(0, len(prompts), self._batch_size):
            vectors += await self._embed(prompts[start : start + self._batch_size])
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([self._query_template.format(text=text)]))[0]

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        try:
            response = await self._http.post(
                self._url,
                json={"model": self.model_id, "input": inputs},
                headers=self._headers,
                timeout=120,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailableError(
                f"embedding server unreachable at {self._url}: {exc!r}"
            ) from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise EmbeddingUnavailableError(f"embedding server HTTP {response.status_code}")
        if response.is_error:
            raise EmbeddingRejectedError(
                f"embedding server HTTP {response.status_code}: {response.text[:200]}"
            )
        data: list[dict[str, Any]] = sorted(response.json()["data"], key=lambda d: d["index"])
        vectors = [list(map(float, d["embedding"])) for d in data]
        if len(vectors) != len(inputs) or any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingError(
                f"expected {len(inputs)} vectors of {self.dimensions} dimensions "
                f"from {self.model_id}"
            )
        return vectors


_WORD = re.compile(r"\w+", re.UNICODE)


class HashEmbeddings:
    """Feature-hashed word counts, L2-normalized. Deterministic and dependency-free."""

    def __init__(self, dimensions: int = 256) -> None:
        self.model_id = f"hash-{dimensions}"
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in _WORD.findall(text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    async def embed_documents(
        self, texts: Sequence[str], *, titles: Sequence[str] | None = None
    ) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

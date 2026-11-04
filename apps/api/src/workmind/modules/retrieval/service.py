"""Permission-aware hybrid retrieval (ARCHITECTURE section 11, ADR 0006).

    question -> dense + BM25 queries, pre-filtered by tenant and ACL (lock 1) -> RRF fusion
             -> trust boost, per-document diversity -> OpenFGA recheck (lock 2) -> passages

Lock 2 checks `viewer` on each passage's collection: documents inherit their collection's
access, and checking the collection never races a just-uploaded document's own tuple.
A passage that fails lock 2 is dropped and counted as drift, which should never happen.
"""

import asyncio
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.tenancy import global_transaction
from workmind.modules.authz.openfga import OpenFgaClient, RelationTuple
from workmind.modules.identity.models import Tenant
from workmind.modules.retrieval.qdrant import QdrantClient, match, match_any
from workmind.modules.retrieval.sparse import encode_query
from workmind.providers.embeddings import EmbeddingProvider

log = structlog.get_logger("workmind.retrieval")

Mode = Literal["hybrid", "dense", "sparse"]
TRUST_BOOST = {"official": 1.15, "reference": 1.0, "discussion": 0.85}
MAX_PER_DOCUMENT = 3
PREFETCH = 40  # candidates per list before fusion
# Fusion settings chosen by the ablation in evals/README.md. The textbook k=60 with equal
# weights scored *below* dense search alone here: it flattens rank differences so much that the
# trust boost and BM25 noise decide the order. A small k keeps "first place" meaningful.
RRF_K = 2
DEFAULT_SPARSE_WEIGHT = 0.5  # BM25's vote relative to the dense list's


def fuse(
    dense: Sequence[dict[str, Any]],
    sparse: Sequence[dict[str, Any]],
    *,
    sparse_weight: float,
    k: int = RRF_K,
) -> list[dict[str, Any]]:
    """Weighted reciprocal rank fusion. BM25 rescues exact identifiers (INC-2026-031) that
    embeddings blur; a weight below 1 stops its noise from outvoting a confident dense match."""
    scores: dict[str, float] = {}
    hits: dict[str, dict[str, Any]] = {}
    for weight, ranked in ((1.0, dense), (sparse_weight, sparse)):
        for rank, hit in enumerate(ranked, start=1):
            key = str(hit["id"])
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
            hits.setdefault(key, hit)
    order = sorted(scores, key=lambda key: -scores[key])
    return [{**hits[key], "score": scores[key]} for key in order]


@dataclass(frozen=True)
class Passage:
    marker: str  # "S1", "S2", ... as the answer cites them
    chunk_id: str
    document_id: str
    collection_id: str
    title: str
    filename: str
    heading_path: list[str]
    page: int | None
    text: str
    trust: str
    sensitivity: str
    score: float

    @property
    def section(self) -> str:
        return " > ".join(self.heading_path)


@dataclass
class RetrievalResult:
    query: str
    passages: list[Passage] = field(default_factory=list[Passage])
    candidates: int = 0
    dropped_by_recheck: int = 0
    best_similarity: float | None = None  # top dense cosine; None in keyword-only mode
    below_floor: bool = False  # nothing permitted was relevant enough to show a model


class Retriever:
    def __init__(
        self,
        *,
        qdrant: QdrantClient,
        embeddings: EmbeddingProvider,
        fga: OpenFgaClient | None,
        sessions: async_sessionmaker[AsyncSession],
        collection_name: str,
        sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
        rrf_k: int = RRF_K,
        trust_boost: bool = True,
        min_similarity: float = 0.0,
    ) -> None:
        # Tuning knobs, public so the evaluation can ablate them.
        self.sparse_weight = sparse_weight
        self.rrf_k = rrf_k
        self.trust_boost = trust_boost
        self.min_similarity = min_similarity
        self._qdrant = qdrant
        self._embeddings = embeddings
        self._fga = fga  # None only in tests that exercise lock 1 alone
        self._sessions = sessions
        self._collection = collection_name
        self._stores: dict[uuid.UUID, str | None] = {}

    def acl_filter(
        self,
        ctx: RequestContext,
        *,
        exclude_restricted: bool = False,
        collection_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Lock 1. Built here, never by callers, so no search can skip it. `collection_ids`
        only narrows the search (an agent's knowledge scope); it can never widen access."""
        flt: dict[str, Any] = {
            "must": [
                match("tenant_id", str(ctx.tenant_id)),
                match("is_active", True),
                match_any("acl", sorted(ctx.principals)),
            ]
        }
        if collection_ids is not None:
            flt["must"].append(match_any("collection_id", sorted(collection_ids)))
        if exclude_restricted:
            flt["must_not"] = [match("sensitivity", "restricted")]
        return flt

    async def search(
        self,
        ctx: RequestContext,
        query: str,
        *,
        limit: int = 8,
        mode: Mode = "hybrid",
        exclude_restricted: bool = False,
        collection_ids: Sequence[str] | None = None,
    ) -> RetrievalResult:
        result = RetrievalResult(query=query)
        if collection_ids is not None and not collection_ids:
            return result  # an empty scope matches nothing (not "everything")
        if not await self._qdrant.collection_exists(self._collection):
            return result
        flt = self.acl_filter(
            ctx, exclude_restricted=exclude_restricted, collection_ids=collection_ids
        )
        hits, result.best_similarity = await self._query(query, flt, limit=limit * 4, mode=mode)
        result.candidates = len(hits)
        if result.best_similarity is not None and result.best_similarity < self.min_similarity:
            # Nothing permitted is about this: answer "not found" without sending noise to a
            # model. Only the clearly off-topic falls below it (see evals/README.md).
            result.below_floor = True
            return result

        boost = TRUST_BOOST if self.trust_boost else {}
        scored = sorted(hits, key=lambda h: -h["score"] * boost.get(h["payload"]["trust"], 1.0))
        per_document: Counter[str] = Counter()
        chosen: list[dict[str, Any]] = []
        for hit in scored:
            document = hit["payload"]["document_id"]
            if per_document[document] < MAX_PER_DOCUMENT:
                per_document[document] += 1
                chosen.append(hit)

        allowed = await self._recheck(ctx, {h["payload"]["collection_id"] for h in chosen})
        kept = [h for h in chosen if h["payload"]["collection_id"] in allowed]
        result.dropped_by_recheck = len(chosen) - len(kept)
        if result.dropped_by_recheck:
            log.warning(
                "authz_projection_drift",
                dropped=result.dropped_by_recheck,
                user_id=str(ctx.user_id),
            )
        result.passages = [_passage(i, h) for i, h in enumerate(kept[:limit], start=1)]
        return result

    async def _query(
        self, query: str, flt: dict[str, Any], *, limit: int, mode: Mode
    ) -> tuple[list[dict[str, Any]], float | None]:
        """Hits, plus the best dense similarity (for the relevance floor)."""
        sparse = encode_query(query).as_qdrant()
        if mode == "sparse":
            hits = await self._qdrant.query(
                self._collection, query=sparse, using="bm25", flt=flt, limit=limit
            )
            return hits, None
        dense = await self._embeddings.embed_query(query)
        if mode == "dense":
            hits = await self._qdrant.query(
                self._collection, query=dense, using="dense", flt=flt, limit=limit
            )
            return hits, (float(hits[0]["score"]) if hits else None)
        depth = max(PREFETCH, limit)
        # The ACL filter applies to both lists, so fusion only ever sees permitted chunks.
        dense_hits, sparse_hits = await asyncio.gather(
            self._qdrant.query(self._collection, query=dense, using="dense", flt=flt, limit=depth),
            self._qdrant.query(self._collection, query=sparse, using="bm25", flt=flt, limit=depth),
        )
        fused = fuse(dense_hits, sparse_hits, sparse_weight=self.sparse_weight, k=self.rrf_k)
        return fused[:limit], (float(dense_hits[0]["score"]) if dense_hits else None)

    async def _recheck(self, ctx: RequestContext, collection_ids: set[str]) -> set[str]:
        """Lock 2: OpenFGA must also say yes for every collection a passage comes from."""
        if self._fga is None or not collection_ids:
            return collection_ids
        store = await self._store(ctx.tenant_id)
        if store is None:
            log.warning("authz_store_missing", tenant_id=str(ctx.tenant_id))
            return set()  # fail closed
        questions = [
            RelationTuple(f"user:{ctx.user_id}", "viewer", f"collection:{c}")
            for c in sorted(collection_ids)
        ]
        answers = await self._fga.batch_check(store, questions)
        return {q.object.split(":", 1)[1] for q, ok in answers.items() if ok}

    async def _store(self, tenant_id: uuid.UUID) -> str | None:
        if tenant_id not in self._stores:
            async with global_transaction(self._sessions) as session:
                self._stores[tenant_id] = await session.scalar(
                    select(Tenant.openfga_store_id).where(Tenant.id == tenant_id)
                )
        return self._stores[tenant_id]


def _passage(index: int, hit: dict[str, Any]) -> Passage:
    p = hit["payload"]
    return Passage(
        marker=f"S{index}",
        chunk_id=p["chunk_id"],
        document_id=p["document_id"],
        collection_id=p["collection_id"],
        title=p["title"],
        filename=p["filename"],
        heading_path=list(p.get("heading_path") or []),
        page=p.get("page"),
        text=p["text"],
        trust=p["trust"],
        sensitivity=p["sensitivity"],
        score=float(hit["score"]),
    )

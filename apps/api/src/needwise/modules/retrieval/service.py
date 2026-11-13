"""Permission-aware hybrid retrieval (ARCHITECTURE section 11, ADR 0006).

    question -> dense + BM25 queries, pre-filtered by tenant and ACL (lock 1) -> RRF fusion
             -> trust boost, per-document diversity -> OpenFGA recheck (lock 2) -> passages

Lock 2 checks `viewer` on each passage's collection: documents inherit their collection's
access, and checking the collection never races a just-uploaded document's own tuple. A
document with its own access list (ADR 0012) is also checked as a document (`viewer` on
`document:<id>`, which needs the collection AND the list). Which documents have one is read
from PostgreSQL, the source of truth, and from the chunk's payload: either is enough to ask.
The same PostgreSQL query also says whether the list names the caller, so a narrowed list holds
even while OpenFGA still waits for the outbox and the chunks still carry the old payload.
A passage that fails lock 2 is dropped and counted as drift, which should never happen.
"""

import asyncio
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.context import RequestContext
from needwise.core.tenancy import global_transaction, tenant_transaction
from needwise.modules.authz.openfga import OpenFgaClient, RelationTuple
from needwise.modules.identity.models import Tenant
from needwise.modules.knowledge.document_acl import visible_clause
from needwise.modules.knowledge.models import Document
from needwise.modules.retrieval.qdrant import QdrantClient, match, match_any
from needwise.modules.retrieval.sparse import encode_query
from needwise.providers.embeddings import EmbeddingProvider

log = structlog.get_logger("needwise.retrieval")

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
    # Official, but past its review date (docs/governance.md): shown as "Review overdue".
    review_overdue: bool = False

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
        principals = sorted(ctx.principals)
        flt: dict[str, Any] = {
            "must": [
                match("tenant_id", str(ctx.tenant_id)),
                match("is_active", True),
                match_any("acl", principals),
                # A document's own access list only narrows: no own list, or one naming the
                # caller. Points indexed before own lists existed have no `own_acl` field and
                # count as having none, which is what they are.
                {
                    "should": [
                        {"must_not": [match("own_acl", True)]},
                        match_any("doc_acl", principals),
                    ]
                },
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
        hits, result.best_similarity = await self._query(
            ctx.tenant_id, query, flt, limit=limit * 4, mode=mode
        )
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

        kept = await self._recheck(ctx, chosen)
        result.dropped_by_recheck = len(chosen) - len(kept)
        if result.dropped_by_recheck:
            log.warning(
                "authz_projection_drift",
                dropped=result.dropped_by_recheck,
                user_id=str(ctx.user_id),
            )
        passages = [_passage(i, h) for i, h in enumerate(kept[:limit], start=1)]
        result.passages = await self._mark_overdue(ctx, passages)
        return result

    async def _mark_overdue(self, ctx: RequestContext, passages: list[Passage]) -> list[Passage]:
        """Review dates live in PostgreSQL, not in the index: a review must not need a
        re-index. One query for the documents an answer may cite."""
        official = {p.document_id for p in passages if p.trust == "official"}
        if not official:
            return passages
        async with tenant_transaction(self._sessions, ctx.tenant_id) as session:
            overdue = {
                str(document_id)
                for document_id in await session.scalars(
                    select(Document.id).where(
                        Document.id.in_([uuid.UUID(d) for d in official]),
                        Document.next_review_at < datetime.now(UTC),
                    )
                )
            }
        return [replace(p, review_overdue=p.document_id in overdue) for p in passages]

    async def _query(
        self, tenant_id: uuid.UUID, query: str, flt: dict[str, Any], *, limit: int, mode: Mode
    ) -> tuple[list[dict[str, Any]], float | None]:
        """Hits, plus the best dense similarity (for the relevance floor)."""
        sparse = encode_query(query).as_qdrant()
        name = self._collection
        if mode == "sparse":
            hits = await self._qdrant.query(
                name, tenant_id=tenant_id, query=sparse, using="bm25", flt=flt, limit=limit
            )
            return hits, None
        dense = await self._embeddings.embed_query(query)
        if mode == "dense":
            hits = await self._qdrant.query(
                name, tenant_id=tenant_id, query=dense, using="dense", flt=flt, limit=limit
            )
            return hits, (float(hits[0]["score"]) if hits else None)
        depth = max(PREFETCH, limit)
        # The ACL filter applies to both lists, so fusion only ever sees permitted chunks.
        dense_hits, sparse_hits = await asyncio.gather(
            self._qdrant.query(
                name, tenant_id=tenant_id, query=dense, using="dense", flt=flt, limit=depth
            ),
            self._qdrant.query(
                name, tenant_id=tenant_id, query=sparse, using="bm25", flt=flt, limit=depth
            ),
        )
        fused = fuse(dense_hits, sparse_hits, sparse_weight=self.sparse_weight, k=self.rrf_k)
        return fused[:limit], (float(dense_hits[0]["score"]) if dense_hits else None)

    async def _recheck(
        self, ctx: RequestContext, hits: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Lock 2: OpenFGA must also say yes for every collection a passage comes from, and for
        every document with its own access list."""
        if self._fga is None or not hits:
            return hits
        store = await self._store(ctx.tenant_id)
        if store is None:
            log.warning("authz_store_missing", tenant_id=str(ctx.tenant_id))
            return []  # fail closed
        own = {str(h["payload"]["document_id"]) for h in hits if h["payload"].get("own_acl")}
        listed, closed = await self._own_acl_documents(
            ctx, {h["payload"]["document_id"] for h in hits}
        )
        own |= listed
        user = f"user:{ctx.user_id}"
        questions = [
            RelationTuple(user, "viewer", f"collection:{c}")
            for c in sorted({str(h["payload"]["collection_id"]) for h in hits})
        ]
        questions += [RelationTuple(user, "viewer", f"document:{d}") for d in sorted(own)]
        answers = await self._fga.batch_check(store, questions)
        allowed = {q.object for q, ok in answers.items() if ok}
        return [
            h
            for h in hits
            if f"collection:{h['payload']['collection_id']}" in allowed
            and str(h["payload"]["document_id"]) not in closed
            and (
                str(h["payload"]["document_id"]) not in own
                or f"document:{h['payload']['document_id']}" in allowed
            )
        ]

    async def _own_acl_documents(
        self, ctx: RequestContext, document_ids: set[str]
    ) -> tuple[set[str], set[str]]:
        """According to PostgreSQL: which of these documents have their own access list, and
        which of those the list closes to the caller."""
        ids: list[uuid.UUID] = []
        for document_id in document_ids:
            try:
                ids.append(uuid.UUID(str(document_id)))
            except ValueError:
                continue
        if not ids:
            return set(), set()
        async with tenant_transaction(self._sessions, ctx.tenant_id) as session:
            rows = await session.execute(
                select(Document.id, visible_clause(ctx.principals)).where(
                    Document.id.in_(ids), Document.own_acl.is_(True)
                )
            )
            found = [(str(document_id), bool(open_)) for document_id, open_ in rows]
        return {d for d, _ in found}, {d for d, open_ in found if not open_}

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

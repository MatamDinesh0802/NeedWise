"""Documents with their own access list (ADR 0012, docs/permission-sync.md).

A document normally has its collection's access. It may instead carry its own list
(`own_acl`, rows in document_grants), written by permission sync (Enterprise) from the source's
ACL, or by an editor. The list only narrows: a principal must be able to view the collection
AND be on the list. The rules everywhere:

- Lock 1 (Qdrant payload) and lock 2 (OpenFGA tuples) are both derived from these rows
  (`knowledge/acl.py`), changed in the same transaction (outbox) and refreshed on the chunks
  right after it commits.
- Every other path that shows a document (the Knowledge Hub, governance, approvals, workflow
  triggers, notifications) asks `can_view` / `visible_clause` with the caller's principals.
- Identity mapping fails closed: a principal the source names that maps to nobody in
  NeedWise is kept on the list without a principal, so it grants nothing, and administrators
  see it (`unmapped`). An own list with nobody mapped on it means nobody sees the document.
"""

import uuid
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import ColumnElement, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.modules.authz.openfga import RelationTuple
from needwise.modules.authz.sync import enqueue
from needwise.modules.connectors.models import IdentityMapping, Source
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.identity.models import User
from needwise.modules.knowledge.acl import (
    document_payload,
    document_principals,
    document_tuples,
)
from needwise.modules.knowledge.models import Document, DocumentGrant
from needwise.modules.retrieval.qdrant import QdrantClient, match

Origin = Literal["source", "manual"]


@dataclass(frozen=True)
class AclEntry:
    """One entry of an own list: who in NeedWise (None: unmapped, grants nothing) and how the
    source named them (None for an entry an editor set)."""

    principal: str | None
    external: str | None = None


def ordered(entries: Iterable[AclEntry]) -> list[AclEntry]:
    """Distinct entries in a stable order (either field may be None)."""
    return sorted(set(entries), key=lambda e: (e.principal or "", e.external or ""))


def normalize_external(value: str) -> str:
    return value.strip().lower()


# --- Identity mapping ---------------------------------------------------------------------------


async def resolve(
    session: AsyncSession, tenant_id: uuid.UUID, externals: Iterable[str]
) -> dict[str, str | None]:
    """The NeedWise principal for each source principal, or None (unmapped: denied).

    An administrator's mapping wins; otherwise `email:` matches the user with that address and
    `anyone` is the organization. Nothing else is guessed: a group, a domain, a Slack or GitHub
    account without a mapping stays unmapped."""
    wanted = {normalize_external(e) for e in externals}
    if not wanted:
        return {}
    mapped: dict[str, str | None] = {
        m.external: m.principal
        for m in await session.scalars(
            select(IdentityMapping).where(IdentityMapping.external.in_(sorted(wanted)))
        )
    }
    emails = {e.removeprefix("email:") for e in wanted - set(mapped) if e.startswith("email:")}
    if emails:
        users = await session.execute(
            select(User.email, User.id).where(func.lower(User.email).in_(sorted(emails)))
        )
        for email, user_id in users:
            mapped.setdefault(f"email:{email.lower()}", f"user:{user_id}")
    if "anyone" in wanted:
        mapped.setdefault("anyone", f"org:{tenant_id}")
    return {e: mapped.get(e) for e in wanted}


async def entries_for(
    session: AsyncSession, tenant_id: uuid.UUID, externals: Iterable[str]
) -> list[AclEntry]:
    """An own list from the principals a source names, each mapped (or not) to NeedWise."""
    resolved = await resolve(session, tenant_id, externals)
    return ordered(AclEntry(principal, external) for external, principal in resolved.items())


# --- Reading ------------------------------------------------------------------------------------


async def grants_of(
    session: AsyncSession, document_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[DocumentGrant]]:
    ids = sorted(set(document_ids))
    found: dict[uuid.UUID, list[DocumentGrant]] = {i: [] for i in ids}
    if ids:
        for grant in await session.scalars(
            select(DocumentGrant).where(DocumentGrant.document_id.in_(ids))
        ):
            found[grant.document_id].append(grant)
    return found


def visible_clause(principals: Collection[str]) -> ColumnElement[bool]:
    """SQL condition on Document: its own list (if any) names one of `principals`. Always
    combine it with the collection's access: the list only narrows."""
    listed = select(DocumentGrant.id).where(
        DocumentGrant.document_id == Document.id,
        DocumentGrant.principal.in_(sorted(principals)),
    )
    return or_(Document.own_acl.is_(False), listed.exists())


async def can_view(session: AsyncSession, principals: Collection[str], document: Document) -> bool:
    """The document's own list lets one of `principals` through (collection checked apart)."""
    if not document.own_acl:
        return True
    listed = await session.scalar(
        select(DocumentGrant.id)
        .where(
            DocumentGrant.document_id == document.id,
            DocumentGrant.principal.in_(sorted(principals)),
        )
        .limit(1)
    )
    return listed is not None


async def hidden_documents(
    session: AsyncSession, principals: Collection[str], document_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Among `document_ids`, those whose own list keeps every one of `principals` out."""
    ids = sorted(set(document_ids))
    if not ids:
        return set()
    visible = set(
        await session.scalars(
            select(Document.id).where(Document.id.in_(ids), visible_clause(principals))
        )
    )
    return set(ids) - visible


# --- Writing ------------------------------------------------------------------------------------


async def set_acl(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    document: Document,
    entries: Sequence[AclEntry] | None,
    *,
    origin: Origin,
    actor: Actor,
    fresh: bool = False,
) -> bool:
    """Gives the document this own list (None: none, back to the collection's access), and
    queues the OpenFGA changes in the caller's transaction. `fresh`: the document was created
    in this transaction and none of its tuples were queued yet. Returns whether anything
    changed; if so, the caller refreshes the chunks after the commit (`refresh_payload`)."""
    before_grants = [] if fresh else (await grants_of(session, [document.id]))[document.id]
    before: set[RelationTuple] = set() if fresh else document_tuples(document, before_grants)
    wanted = ordered(entries) if entries is not None else []
    current = ordered(AclEntry(g.principal, g.external) for g in before_grants)
    own = entries is not None
    if not fresh and own == document.own_acl and wanted == current:
        if own and document.acl_origin != origin:
            document.acl_origin = origin
        return False
    if before_grants:
        await session.execute(delete(DocumentGrant).where(DocumentGrant.document_id == document.id))
    grants = [
        DocumentGrant(
            tenant_id=tenant_id,
            document_id=document.id,
            principal=e.principal,
            external=e.external,
        )
        for e in wanted
    ]
    session.add_all(grants)
    document.own_acl = own
    document.acl_origin = origin if own else None
    document.acl_updated_at = datetime.now(UTC)
    await session.flush()
    after = document_tuples(document, grants)
    enqueue(session, tenant_id, writes=after - before, deletes=before - after)
    if not fresh:
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action="knowledge.document.acl_changed",
            resource_type="document",
            resource_id=str(document.id),
            details={
                "collection_id": str(document.collection_id),
                "origin": origin,
                "own_acl": own,
                "principals": len(document_principals(grants)),
                "unmapped": sum(1 for g in grants if g.principal is None),
            },
        )
    return True


async def payload_of(session: AsyncSession, document: Document) -> dict[str, Any]:
    grants = (await grants_of(session, [document.id]))[document.id]
    return document_payload(document, grants)


async def refresh_payload(
    qdrant: QdrantClient,
    collection_name: str,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    """After an own-list change: every chunk of the document carries it (lock 1)."""
    if not await qdrant.collection_exists(collection_name):
        return
    await qdrant.set_payload(
        collection_name,
        payload,
        {"must": [match("document_id", str(document_id))]},
        tenant_id=tenant_id,
    )


async def remap(
    session: AsyncSession, tenant_id: uuid.UUID, externals: Iterable[str]
) -> dict[uuid.UUID, dict[str, Any]]:
    """After a mapping was added, changed or removed: the entries naming these source principals
    point at the new NeedWise principal (or none). Queues the OpenFGA changes; returns the new
    payload of every document whose list changed, for `refresh_payload` after the commit."""
    names = sorted({normalize_external(e) for e in externals})
    resolved = await resolve(session, tenant_id, names)
    rows = (
        await session.scalars(select(DocumentGrant).where(DocumentGrant.external.in_(names)))
    ).all()
    stale = {g.document_id for g in rows if g.principal != resolved.get(g.external or "")}
    payloads: dict[uuid.UUID, dict[str, Any]] = {}
    for document_id, grants in (await grants_of(session, stale)).items():
        document = await session.get(Document, document_id)
        if document is None:
            continue
        before = document_tuples(document, grants)
        for grant in grants:
            if grant.external in resolved:
                grant.principal = resolved[grant.external]
        document.acl_updated_at = datetime.now(UTC)
        await session.flush()
        after = document_tuples(document, grants)
        enqueue(session, tenant_id, writes=after - before, deletes=before - after)
        payloads[document_id] = document_payload(document, grants)
    return payloads


@dataclass(frozen=True)
class Unmapped:
    external: str
    documents: int
    sources: list[str]  # keys of the sources whose documents name it


async def unmapped(session: AsyncSession) -> list[Unmapped]:
    """Source principals that map to nobody, for administrators: no titles, only counts."""
    rows = await session.execute(
        select(
            DocumentGrant.external,
            func.count(func.distinct(DocumentGrant.document_id)),
            func.array_agg(func.distinct(Source.key)),
        )
        .join(Document, Document.id == DocumentGrant.document_id)
        .outerjoin(Source, Source.id == Document.source_id)
        .where(DocumentGrant.principal.is_(None), DocumentGrant.external.is_not(None))
        .group_by(DocumentGrant.external)
        .order_by(DocumentGrant.external)
    )
    return [
        Unmapped(external or "", count, sorted(k for k in keys if k))
        for external, count, keys in rows
    ]

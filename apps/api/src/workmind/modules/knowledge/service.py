"""Collections and documents: visibility, uploads, versions and deletion.

Visibility uses the same principal strings as retrieval (lock 1), so "can I see this document in
the Knowledge Hub" and "can the assistant use it for me" always agree. A collection you cannot
see answers 404, never 403, so its existence is not revealed (ARCHITECTURE 7.6). A document with
its own access list (document_acl.py) is visible only to those on it who can see the
collection; to anyone else it does not exist either.
"""

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.modules.authz.sync import enqueue
from workmind.modules.governance import holds
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.knowledge import document_acl, governance
from workmind.modules.knowledge.acl import acl_principals, document_tuples
from workmind.modules.knowledge.document_acl import AclEntry, Origin
from workmind.modules.knowledge.models import (
    Chunk,
    Collection,
    CollectionGrant,
    Document,
    DocumentVersion,
)
from workmind.modules.knowledge.parsing import detect_mime
from workmind.modules.knowledge.storage import ObjectStorage, object_key


def _not_found(kind: str) -> ApiError:
    return ApiError(404, "not_found", f"{kind} not found")


@dataclass(frozen=True)
class CollectionAccess:
    collection: Collection
    grants: Sequence[CollectionGrant]
    can_view: bool
    can_edit: bool


def _access(
    ctx: RequestContext, collection: Collection, grants: Sequence[CollectionGrant]
) -> CollectionAccess:
    viewers = set(acl_principals(collection, grants))
    editors = {g.principal for g in grants if g.relation == "editor"}
    if collection.owner_id:
        editors.add(f"user:{collection.owner_id}")
    return CollectionAccess(
        collection=collection,
        grants=grants,
        can_view=bool(viewers & ctx.principals),
        can_edit=bool(editors & ctx.principals),
    )


async def collection_access(
    session: AsyncSession, ctx: RequestContext, collection_id: uuid.UUID, *, edit: bool = False
) -> CollectionAccess:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise _not_found("Collection")
    grants = (
        await session.scalars(
            select(CollectionGrant).where(CollectionGrant.collection_id == collection.id)
        )
    ).all()
    access = _access(ctx, collection, grants)
    if not access.can_view:
        raise _not_found("Collection")
    if edit and not access.can_edit:
        raise ApiError(403, "forbidden", "You can view this collection but not change it")
    return access


async def visible_collections(
    session: AsyncSession, ctx: RequestContext
) -> list[tuple[CollectionAccess, int]]:
    """Collections the user can view, with their ready-document counts."""
    visible_ids = select(CollectionGrant.collection_id).where(
        CollectionGrant.principal.in_(ctx.principals)
    )
    collections = (
        await session.scalars(
            select(Collection)
            .where(or_(Collection.id.in_(visible_ids), Collection.owner_id == ctx.user_id))
            .order_by(Collection.name)
        )
    ).all()
    ids = [c.id for c in collections]
    grants = (
        await session.scalars(select(CollectionGrant).where(CollectionGrant.collection_id.in_(ids)))
    ).all()
    rows = await session.execute(
        select(Document.collection_id, func.count())
        .where(
            Document.collection_id.in_(ids),
            Document.status == "ready",
            document_acl.visible_clause(ctx.principals),
        )
        .group_by(Document.collection_id)
    )
    # Not dict(rows): Result has .keys(), so dict() would treat it as a mapping.
    counts = {collection_id: count for collection_id, count in rows}
    return [
        (_access(ctx, c, [g for g in grants if g.collection_id == c.id]), counts.get(c.id, 0))
        for c in collections
    ]


@dataclass(frozen=True)
class SyncedItem:
    """Identity of a document that comes from a source (not an upload)."""

    source_id: uuid.UUID
    external_id: str
    title: str
    url: str | None
    meta: Mapping[str, Any] | None = None  # where it sits in the source (documents.source_meta)


@dataclass(frozen=True)
class UploadResult:
    document: Document
    version: DocumentVersion
    unchanged: bool  # same bytes as the current version: nothing to re-index
    # The new content cost the document its official status (governance.new_version_stored):
    # the search index should follow at once.
    demoted: bool = False


def _title_from(filename: str) -> str:
    return PurePath(filename).stem.replace("_", " ").replace("-", " ").strip() or filename


async def upload(
    session: AsyncSession,
    ctx: RequestContext,
    storage: ObjectStorage,
    collection: Collection,
    *,
    filename: str,
    declared_mime: str | None,
    data: bytes,
    trust: str | None = None,
    sensitivity: str | None = None,
    source: str = "upload",
    synced: SyncedItem | None = None,
    acl: Sequence[AclEntry] | None = None,
    acl_origin: Origin = "source",
) -> UploadResult:
    """Stores the file and records a new version. A file with the same name in the same
    collection is a new version of the same document (a synced item: the same source item);
    identical bytes change nothing. New content for an official document ends its official
    status, and voids a pending request for it (docs/governance.md): approvals are for the
    content the approver read.

    `acl`: a new document's own access list, set in the same transaction, so it is never
    indexed or visible with its collection's wider access first. For an existing document the
    caller changes the list with document_acl.set_acl."""
    filename = PurePath(filename).name  # never trust a client-supplied path
    try:
        mime = detect_mime(filename, declared_mime)
    except ValueError as exc:
        raise ApiError(
            415, "unsupported_type", "This file type is not supported", str(exc)
        ) from exc
    if not data:
        raise ApiError(422, "empty_file", "The file is empty")
    content_hash = hashlib.sha256(data).hexdigest()

    same_item = (
        (Document.source_id == synced.source_id) & (Document.external_id == synced.external_id)
        if synced
        else (Document.collection_id == collection.id) & (Document.filename == filename)
    )
    document = (await session.scalars(select(Document).where(same_item))).first()
    if document is not None and document.current_version_id is not None:
        current = await session.get(DocumentVersion, document.current_version_id)
        if (
            current is not None
            and current.content_hash == content_hash
            and document.status != "failed"
        ):
            if synced and synced.meta is not None:  # moved in its source: nothing to re-index
                document.source_meta = dict(synced.meta)
            return UploadResult(document, current, unchanged=True)

    key = object_key(ctx.tenant_id, content_hash)
    await storage.put(key, data)
    is_new = document is None
    if document is None:
        document = Document(
            tenant_id=ctx.tenant_id,
            collection_id=collection.id,
            title=synced.title if synced else _title_from(filename),
            filename=filename,
            mime_type=mime,
            source=source,
            source_id=synced.source_id if synced else None,
            external_id=synced.external_id if synced else None,
            url=synced.url if synced else None,
            # The collection's default trust is the admin's standing decision for what lands
            # there (seeding, connectors, uploads); changing a document to official later
            # needs an approval (governance.request_official).
            trust=trust or collection.default_trust,
            sensitivity=sensitivity or collection.default_sensitivity,
            created_by=ctx.user_id,
            owner_id=await governance.default_owner(session, ctx.user_id, collection),
        )
        governance.start_review_cycle(document, collection, governance.utcnow())
        session.add(document)
        await session.flush()
    document.status, document.error, document.mime_type = "pending", None, mime
    if synced:  # the source may rename an item or move its link
        document.title, document.url, document.filename = synced.title, synced.url, filename
        document.source_meta = dict(synced.meta) if synced.meta is not None else None
    version_no = (
        await session.scalar(
            select(func.coalesce(func.max(DocumentVersion.version_no), 0)).where(
                DocumentVersion.document_id == document.id
            )
        )
        or 0
    ) + 1
    version = DocumentVersion(
        tenant_id=ctx.tenant_id,
        document_id=document.id,
        version_no=version_no,
        content_hash=content_hash,
        storage_key=key,
        size_bytes=len(data),
    )
    session.add(version)
    await session.flush()
    demoted = False
    if is_new and acl is not None:
        await document_acl.set_acl(
            session,
            ctx.tenant_id,
            document,
            acl,
            origin=acl_origin,
            actor=Actor(type="user", id=ctx.user_id),
            fresh=True,
        )
    elif is_new:
        enqueue(session, ctx.tenant_id, writes=document_tuples(document))
    else:
        demoted = (
            await governance.new_version_stored(
                session, ctx, document, collection, version_no=version_no
            )
            is not None
        )
        # New content is an update: an archived document comes back (indexed as active).
        document.archived_at = None
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="knowledge.document.uploaded",
        resource_type="document",
        resource_id=str(document.id),
        details={
            "filename": filename,
            "version": version_no,
            "bytes": len(data),
            "collection": collection.key,
        },
    )
    return UploadResult(document, version, unchanged=False, demoted=demoted)


async def delete_document(
    session: AsyncSession,
    ctx: RequestContext,
    document: Document,
    *,
    actor: Actor | None = None,
    reason: str | None = None,
) -> list[str]:
    """Deletes the document, its versions and chunks. Returns storage keys the caller may remove
    once no other version references them (content-addressed keys can be shared).

    A document under a legal hold is never deleted (409 legal_hold). Callers check first with
    holds.covering, so the refusal is audited; this is the last line of defence."""
    if await holds.covering(session, "document", document):
        raise holds.held_error("document")
    keys = list(
        (
            await session.scalars(
                select(DocumentVersion.storage_key).where(
                    DocumentVersion.document_id == document.id
                )
            )
        ).all()
    )
    grants = (await document_acl.grants_of(session, [document.id]))[document.id]
    enqueue(session, ctx.tenant_id, deletes=document_tuples(document, grants))
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    await session.delete(document)
    await session.flush()
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=actor or Actor(type="user", id=ctx.user_id),
        action="knowledge.document.deleted",
        resource_type="document",
        resource_id=str(document.id),
        details={"filename": document.filename, **({"reason": reason} if reason else {})},
    )
    still_used = set(
        (
            await session.scalars(
                select(DocumentVersion.storage_key).where(DocumentVersion.storage_key.in_(keys))
            )
        ).all()
    )
    return [k for k in keys if k not in still_used]

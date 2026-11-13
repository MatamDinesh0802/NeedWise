"""Knowledge Hub API: collections, documents, uploads (ARCHITECTURE sections 10, 18).

Reading needs the `knowledge:read` scope (browser sessions have every scope); changing anything
needs a browser session. Uploads send the raw file as the request body:

    POST /api/v1/collections/{id}/documents?filename=handbook.pdf   (Content-Type: application/pdf)
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, cast
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import (
    SessionContext,
    rate_limit,
    require_scope,
    resources_of,
    settings_of,
)
from workmind.api.v1.admin import AdminContext, PersonRef
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.authz.sync import enqueue
from workmind.modules.governance import holds
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import Department, Group, User
from workmind.modules.knowledge import document_acl, governance, service
from workmind.modules.knowledge.acl import collection_tuples, document_payload
from workmind.modules.knowledge.models import (
    AUTO_DEMOTE_DAYS_MAX,
    ESCALATION_DAYS_MAX,
    Chunk,
    Collection,
    CollectionGrant,
    Document,
    DocumentGrant,
    DocumentVersion,
)
from workmind.modules.knowledge.pipeline import (
    delete_document_points,
    refresh_collection_acl,
    set_document_labels,
)
from workmind.modules.knowledge.storage import owned_key

router = APIRouter(tags=["knowledge"])
ReadContext = Annotated[RequestContext, require_scope("knowledge:read")]


class CollectionOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str
    scope: str
    default_trust: str
    default_sensitivity: str
    document_count: int
    can_edit: bool
    owner: PersonRef | None
    review_interval_days: int | None


class GrantOut(BaseModel):
    principal: str
    relation: str


class CollectionDetail(CollectionOut):
    grants: list[GrantOut]


class OfficialRequestOut(BaseModel):
    """A pending request for official status (docs/governance.md)."""

    requested_by: PersonRef | None
    requested_at: datetime
    note: str | None
    approvers: list[PersonRef]


class DocumentOut(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    filename: str
    mime_type: str
    trust: str
    sensitivity: str
    sensitivity_manual: bool  # set by an editor; otherwise suggested by the scan
    status: str
    error: str | None
    chunk_count: int
    size_bytes: int
    version: int
    updated_at: datetime
    # Archived by a document retention rule: kept, but out of search and answers until restored.
    archived_at: datetime | None = None
    # Governance: who answers for it, and its review cycle.
    owner: PersonRef | None = None
    reviewed_at: datetime | None = None
    reviewed_by: PersonRef | None = None
    next_review_at: datetime | None = None
    review_overdue: bool = False
    # How far an overdue review of this official document has escalated this cycle:
    # collection_owner, admins or demoted (docs/governance.md).
    review_escalation: str | None = None
    official_request: OfficialRequestOut | None = None
    # The document has its own access list (from its source, or set by an editor), narrower
    # than its collection's: docs/permission-sync.md.
    own_acl: bool = False


class VersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    content_hash: str
    size_bytes: int
    chunk_count: int
    embedding_model: str | None
    created_at: datetime
    processed_at: datetime | None


class DocumentDetail(DocumentOut):
    versions: list[VersionOut]
    can_edit: bool
    # May mark it reviewed (the owner or an editor) and may hand it to another owner.
    can_review: bool
    can_change_owner: bool


class ChunkOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    heading_path: list[str]
    page: int | None
    token_count: int
    text: str


class UploadOut(BaseModel):
    document: DocumentOut
    unchanged: bool


class Items[T](BaseModel):
    items: list[T]


def _person(people: dict[uuid.UUID, User], user_id: uuid.UUID | None) -> PersonRef | None:
    user = people.get(user_id) if user_id else None
    return PersonRef(id=user.id, display_name=user.display_name) if user else None


async def _people(session: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, User]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {u.id: u for u in await session.scalars(select(User).where(User.id.in_(wanted)))}


def _collection_out(
    access: service.CollectionAccess, count: int, people: dict[uuid.UUID, User] | None = None
) -> CollectionOut:
    c = access.collection
    return CollectionOut(
        id=c.id,
        key=c.key,
        name=c.name,
        description=c.description,
        scope=c.scope,
        default_trust=c.default_trust,
        default_sensitivity=c.default_sensitivity,
        document_count=count,
        can_edit=access.can_edit,
        owner=_person(people or {}, c.owner_id),
        review_interval_days=c.review_interval_days,
    )


async def _governed(
    session: AsyncSession, outs: list[tuple[Document, DocumentOut]]
) -> list[DocumentOut]:
    """Adds owners, review state and pending official requests to documents being returned."""
    requests = await governance.pending_official_requests(session, [d.id for d, _ in outs])
    ids: set[uuid.UUID | None] = set()
    for document, _ in outs:
        ids.update((document.owner_id, document.reviewed_by))
    for request in requests.values():
        ids.update([request.requested_by, *request.approvers])
    people = await _people(session, ids)
    now = governance.utcnow()
    for document, out in outs:
        out.owner = _person(people, document.owner_id)
        out.reviewed_at = document.reviewed_at
        out.reviewed_by = _person(people, document.reviewed_by)
        out.next_review_at = document.next_review_at
        out.review_overdue = governance.review_overdue(document, now)
        out.review_escalation = document.review_escalation
        request = requests.get(document.id)
        out.official_request = (
            OfficialRequestOut(
                requested_by=_person(people, request.requested_by),
                requested_at=request.requested_at,
                note=request.note,
                approvers=[p for a in request.approvers if (p := _person(people, a))],
            )
            if request
            else None
        )
    return [out for _, out in outs]


def _document_out(document: Document, version: DocumentVersion | None) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        collection_id=document.collection_id,
        title=document.title,
        filename=document.filename,
        mime_type=document.mime_type,
        trust=document.trust,
        sensitivity=document.sensitivity,
        sensitivity_manual=document.sensitivity_manual,
        status=document.status,
        error=document.error,
        chunk_count=version.chunk_count if version else 0,
        size_bytes=version.size_bytes if version else 0,
        version=version.version_no if version else 0,
        updated_at=document.updated_at,
        own_acl=document.own_acl,
        archived_at=document.archived_at,
    )


async def _latest_versions(
    session: AsyncSession, documents: list[Document]
) -> dict[uuid.UUID, DocumentVersion]:
    ids = [d.id for d in documents]
    versions = (
        await session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id.in_(ids))
            .order_by(DocumentVersion.version_no)
        )
    ).all()
    return {v.document_id: v for v in versions}  # the last (highest) version wins


async def _document_for(
    session: AsyncSession, ctx: RequestContext, document_id: uuid.UUID, *, edit: bool = False
) -> tuple[Document, service.CollectionAccess]:
    document = await session.get(Document, document_id)
    if document is None:
        raise ApiError(404, "not_found", "Document not found")
    try:
        access = await service.collection_access(session, ctx, document.collection_id)
    except ApiError as exc:
        if exc.status == 404:
            raise ApiError(
                404, "not_found", "Document not found"
            ) from exc  # same answer as missing
        raise
    # Its own access list (if any) must name the caller too; otherwise the document does not
    # exist for them, even for the collection's editors (ADR 0012).
    if not await document_acl.can_view(session, ctx.principals, document):
        raise ApiError(404, "not_found", "Document not found")
    if edit and not access.can_edit:
        raise ApiError(403, "forbidden", "You can view this collection but not change it")
    return document, access


@router.get("/collections")
async def list_collections(ctx: ReadContext, request: Request) -> Items[CollectionOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        visible = await service.visible_collections(session, ctx)
        people = await _people(session, {a.collection.owner_id for a, _ in visible})
    return Items(items=[_collection_out(access, count, people) for access, count in visible])


@router.get("/collections/{collection_id}")
async def get_collection(
    ctx: ReadContext, request: Request, collection_id: uuid.UUID
) -> CollectionDetail:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        access = await service.collection_access(session, ctx, collection_id)
        visible = {a.collection.id: n for a, n in await service.visible_collections(session, ctx)}
        people = await _people(session, {access.collection.owner_id})
    base = _collection_out(access, visible.get(collection_id, 0), people)
    grants = (
        [GrantOut(principal=g.principal, relation=g.relation) for g in access.grants]
        if access.can_edit
        else []
    )
    return CollectionDetail(**base.model_dump(), grants=grants)


@router.get("/collections/{collection_id}/documents")
async def list_documents(
    ctx: ReadContext, request: Request, collection_id: uuid.UUID
) -> Items[DocumentOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await service.collection_access(session, ctx, collection_id)
        documents = list(
            (
                await session.scalars(
                    select(Document)
                    .where(
                        Document.collection_id == collection_id,
                        document_acl.visible_clause(ctx.principals),
                    )
                    .order_by(Document.title)
                )
            ).all()
        )
        versions = await _latest_versions(session, documents)
        items = await _governed(
            session, [(d, _document_out(d, versions.get(d.id))) for d in documents]
        )
    return Items(items=items)


@router.post(
    "/collections/{collection_id}/documents",
    status_code=202,
    dependencies=[rate_limit("ingest")],
)
async def upload_document(
    ctx: SessionContext,
    request: Request,
    collection_id: uuid.UUID,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> UploadOut:
    settings, resources = settings_of(request), resources_of(request)
    declared = int(request.headers.get("content-length") or 0)
    if declared > settings.max_upload_bytes:
        raise ApiError(
            413, "too_large", "The file is too large", f"Limit: {settings.max_upload_bytes} bytes."
        )
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > settings.max_upload_bytes:
            raise ApiError(413, "too_large", "The file is too large")
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        access = await service.collection_access(session, ctx, collection_id, edit=True)
        result = await service.upload(
            session,
            ctx,
            resources.storage,
            access.collection,
            filename=filename,
            declared_mime=request.headers.get("content-type"),
            data=bytes(data),
        )
        out = _document_out(result.document, result.version)
    if result.demoted:  # the earlier content's chunks stop counting as official at once
        await set_document_labels(
            resources.qdrant,
            resources.collection_name,
            ctx.tenant_id,
            result.document.id,
            {"trust": result.document.trust},
        )
    if not result.unchanged:
        await resources.ingestion.dispatch(ctx.tenant_id, result.document.id, result.version.id)
        out.status = "pending"
    return UploadOut(document=out, unchanged=result.unchanged)


@router.get("/documents/{document_id}")
async def get_document(
    ctx: ReadContext, request: Request, document_id: uuid.UUID
) -> DocumentDetail:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        document, access = await _document_for(session, ctx, document_id)
        return await _governance_detail(session, ctx, document, access)


def _detail(
    base: DocumentOut,
    document: Document,
    access: service.CollectionAccess,
    ctx: RequestContext,
    versions: Sequence[DocumentVersion],
) -> DocumentDetail:
    return DocumentDetail(
        **base.model_dump(),
        versions=[VersionOut.model_validate(v, from_attributes=True) for v in versions],
        can_edit=access.can_edit,
        can_review=access.can_edit or document.owner_id == ctx.user_id,
        can_change_owner=access.can_edit or governance.is_org_admin(ctx),
    )


async def _governance_detail(
    session: AsyncSession,
    ctx: RequestContext,
    document: Document,
    access: service.CollectionAccess,
) -> DocumentDetail:
    versions = (
        await session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_no.desc())
        )
    ).all()
    (base,) = await _governed(
        session, [(document, _document_out(document, versions[0] if versions else None))]
    )
    return _detail(base, document, access, ctx, versions)


@router.get("/documents/{document_id}/chunks")
async def get_chunks(ctx: ReadContext, request: Request, document_id: uuid.UUID) -> Items[ChunkOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await _document_for(session, ctx, document_id)
        chunks = (
            await session.scalars(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ordinal)
            )
        ).all()
    return Items(items=[ChunkOut.model_validate(c, from_attributes=True) for c in chunks])


@router.get("/documents/{document_id}/file")
async def download_document(
    ctx: ReadContext,
    request: Request,
    document_id: uuid.UUID,
    inline: Annotated[bool, Query(description="open PDFs in the browser (for #page=N)")] = False,
) -> Response:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        document, _ = await _document_for(session, ctx, document_id)
        version = (
            await session.get(DocumentVersion, document.current_version_id)
            if document.current_version_id
            else None
        )
        if version is None:
            version = (await _latest_versions(session, [document])).get(document.id)
        if version is None:
            raise ApiError(404, "not_found", "Document has no stored file")
        filename, mime, key = document.filename, document.mime_type, version.storage_key
    data = await resources.storage.get(owned_key(ctx.tenant_id, key))
    # RFC 6266: an ASCII fallback plus the exact UTF-8 name (RFC 5987) for non-ASCII names.
    fallback = filename.encode("ascii", "replace").decode().replace('"', "").replace("?", "_")
    # Only PDFs open inline: uploaded HTML or SVG shown on our origin could run scripts.
    show_inline = inline and mime == "application/pdf"
    kind = "inline" if show_inline else "attachment"
    disposition = f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
    headers = {"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff"}
    if show_inline:
        headers["Content-Security-Policy"] = "sandbox"
    return Response(content=data, media_type=mime, headers=headers)


@router.post(
    "/documents/{document_id}/reindex", status_code=202, dependencies=[rate_limit("ingest")]
)
async def reindex_document(
    ctx: SessionContext, request: Request, document_id: uuid.UUID
) -> DocumentOut:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        document, _ = await _document_for(session, ctx, document_id, edit=True)
        version = (await _latest_versions(session, [document])).get(document.id)
        if version is None:
            raise ApiError(409, "no_version", "Nothing to re-index")
        document.status, document.error = "pending", None
        out = _document_out(document, version)
    await resources.ingestion.dispatch(ctx.tenant_id, document.id, version.id)
    return out


class LabelsUpdate(BaseModel):
    trust: Literal["official", "reference", "discussion"] | None = None
    sensitivity: Literal["internal", "confidential", "restricted"] | None = None
    note: str | None = Field(default=None, max_length=2000, description="why, when demoting")


def official_requires_approval() -> ApiError:
    return ApiError(
        403,
        "official_requires_approval",
        "Official status needs an approval",
        "Request it with POST /documents/{id}/official: the collection's owner or an "
        "administrator decides.",
    )


@router.patch("/documents/{document_id}")
async def update_labels(
    ctx: SessionContext, request: Request, document_id: uuid.UUID, body: LabelsUpdate
) -> DocumentOut:
    """Editors correct the trust level or sensitivity label (ARCHITECTURE 7.3). A label set here
    is kept on re-index. The index is updated at once, so model routing follows immediately.

    Trust "official" is never set here: it needs an approval (POST /documents/{id}/official).
    Taking it away (demoting) is immediate, audited, and the owner is told."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        document, _ = await _document_for(session, ctx, document_id, edit=True)
        before = {"trust": document.trust, "sensitivity": document.sensitivity}
        if body.trust == "official" and document.trust != "official":
            raise official_requires_approval()
        if body.trust and body.trust != "official" and document.trust == "official":
            await governance.demote(session, ctx, document, trust=body.trust, note=body.note)
        elif body.trust:
            document.trust = body.trust
        if body.sensitivity:
            document.sensitivity, document.sensitivity_manual = body.sensitivity, True
        after = {"trust": document.trust, "sensitivity": document.sensitivity}
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="knowledge.document.labels_changed",
            resource_type="document",
            resource_id=str(document_id),
            details={"before": before, "after": after},
        )
        version = (await _latest_versions(session, [document])).get(document.id)
        (out,) = await _governed(session, [(document, _document_out(document, version))])
    await set_document_labels(
        resources.qdrant, resources.collection_name, ctx.tenant_id, document_id, after
    )
    return out


# --- Governance: owners, reviews, official status (docs/governance.md) ------------------------


class OwnerUpdate(BaseModel):
    owner_id: uuid.UUID


class OwnerOut(BaseModel):
    document_id: uuid.UUID
    owner: PersonRef


class ReviewIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class OfficialIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000, description="why it should be official")


@router.put("/documents/{document_id}/owner")
async def change_owner(
    ctx: SessionContext, request: Request, document_id: uuid.UUID, body: OwnerUpdate
) -> OwnerOut:
    """Collection editors and org admins hand a document to another owner. Admins may do it in
    collections they cannot read (separation of duties), so the answer holds no content."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        document = await session.get(Document, document_id)
        if document is not None and governance.is_org_admin(ctx):
            collection = await session.get(Collection, document.collection_id)
            assert collection is not None  # noqa: S101 (foreign key)
        else:
            document, access = await _document_for(session, ctx, document_id, edit=True)
            collection = access.collection
        owner = await governance.set_owner(session, ctx, document, collection, body.owner_id)
    return OwnerOut(
        document_id=document_id, owner=PersonRef(id=owner.id, display_name=owner.display_name)
    )


@router.post("/documents/{document_id}/review")
async def review_document(
    ctx: SessionContext, request: Request, document_id: uuid.UUID, body: ReviewIn
) -> DocumentDetail:
    """The owner or a collection editor confirms the content is still correct."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        document, access = await _document_for(session, ctx, document_id)
        if not (access.can_edit or document.owner_id == ctx.user_id):
            raise ApiError(
                403,
                "forbidden",
                "Only the document's owner or the collection's editors can mark it reviewed",
            )
        await governance.mark_reviewed(session, ctx, document, access.collection, note=body.note)
        return await _governance_detail(session, ctx, document, access)


@router.post("/documents/{document_id}/official")
async def request_official(
    ctx: SessionContext, request: Request, document_id: uuid.UUID, body: OfficialIn
) -> DocumentDetail:
    """A collection editor asks for official status. The collection's owner decides, or the
    org admins when the owner is asking or there is none; nobody approves their own request."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        document, access = await _document_for(session, ctx, document_id, edit=True)
        await governance.request_official(session, ctx, document, access.collection, note=body.note)
        return await _governance_detail(session, ctx, document, access)


@router.post("/documents/{document_id}/unarchive")
async def unarchive_document(
    ctx: SessionContext, request: Request, document_id: uuid.UUID
) -> DocumentOut:
    """Editors bring back a document a retention rule archived: it is searchable again at once,
    and retention counts from now, as if it had been updated (docs/enterprise.md)."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        document, _ = await _document_for(session, ctx, document_id, edit=True)
        if document.archived_at is None:
            raise ApiError(409, "not_archived", "This document is not archived")
        archived_at = document.archived_at
        document.archived_at, document.restored_at = None, governance.utcnow()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="knowledge.document.unarchived",
            resource_type="document",
            resource_id=str(document_id),
            details={
                "collection_id": str(document.collection_id),
                "archived_at": archived_at.isoformat(),
            },
        )
        version = (await _latest_versions(session, [document])).get(document.id)
        (out,) = await _governed(session, [(document, _document_out(document, version))])
    await set_document_labels(
        resources.qdrant, resources.collection_name, ctx.tenant_id, document_id, {"is_active": True}
    )
    return out


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    ctx: SessionContext, request: Request, document_id: uuid.UUID
) -> Response:
    """Editors delete a document, its versions and its search entries. A document under a legal
    hold is refused (409 legal_hold) and the attempt is audited."""
    resources = resources_of(request)
    orphaned: list[str] = []
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        document, _ = await _document_for(session, ctx, document_id, edit=True)
        blocking = await holds.covering(session, "document", document)
        if not blocking:
            orphaned = await service.delete_document(session, ctx, document)
    if blocking:
        raise await holds.refuse(
            resources.db,
            ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            kind="document",
            resource_id=document_id,
            holds=blocking,
        )
    await delete_document_points(
        resources.qdrant, resources.collection_name, ctx.tenant_id, document_id
    )
    for key in orphaned:
        await resources.storage.delete(owned_key(ctx.tenant_id, key))
    return Response(status_code=204)


# --- Administration of collections ------------------------------------------------------------

Principal = Annotated[str, Field(pattern=r"^(user|group|dept|dept-manager|org):[0-9a-f-]{36}$")]


class CollectionCreate(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    scope: Literal["department", "enterprise", "restricted"]
    department_id: uuid.UUID | None = None
    default_trust: Literal["official", "reference", "discussion"] = "reference"
    default_sensitivity: Literal["internal", "confidential", "restricted"] = "internal"
    viewers: list[Principal] = []
    editors: list[Principal] = []
    review_interval_days: int | None = Field(default=None, ge=1, le=3650)


class GrantsUpdate(BaseModel):
    viewers: list[Principal]
    editors: list[Principal]


admin_router = APIRouter(prefix="/admin/collections", tags=["admin"])


def baseline_viewers(tenant_id: uuid.UUID, scope: str, department_id: uuid.UUID | None) -> set[str]:
    """The audience a scope promises. Sharing can add people but never drops these: narrowing
    an enterprise collection means changing its scope, not quietly editing grants."""
    if scope == "enterprise":
        return {f"org:{tenant_id}"}
    if scope == "department" and department_id:
        return {f"dept:{department_id}"}
    return set()


async def check_principals(
    session: AsyncSession, tenant_id: uuid.UUID, principals: Sequence[str]
) -> None:
    """Every principal must name someone of this tenant. The id format alone says nothing: a
    user, group or department id of another tenant (or another tenant's org) is refused."""
    wanted: dict[str, set[uuid.UUID]] = {"user": set(), "group": set(), "dept": set()}
    for principal in principals:
        kind, _, raw = principal.partition(":")
        subject = uuid.UUID(raw)  # the Principal pattern already checked the format
        if kind == "org":
            if subject != tenant_id:
                raise _unknown_principal(principal)
            continue
        wanted["dept" if kind == "dept-manager" else kind].add(subject)
    for kind, model in (("user", User), ("group", Group), ("dept", Department)):
        if not wanted[kind]:
            continue
        found = set(await session.scalars(select(model.id).where(model.id.in_(wanted[kind]))))
        if missing := wanted[kind] - found:
            raise _unknown_principal(f"{kind}:{min(missing)}")


def _unknown_principal(principal: str) -> ApiError:
    return ApiError(422, "unknown_principal", "Unknown person, group or department", principal)


def resolve_grants(
    baseline: set[str], viewers: list[str], editors: list[str]
) -> tuple[list[str], list[str]]:
    """Editing implies viewing, so an editor is never also stored as a viewer."""
    editor_set = set(editors)
    return sorted((set(viewers) | baseline) - editor_set), sorted(editor_set)


class AdminCollectionOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str
    scope: str
    department_id: uuid.UUID | None
    default_trust: str
    default_sensitivity: str
    document_count: int
    grants: list[GrantOut]
    owner: PersonRef | None
    review_interval_days: int | None
    review_escalation_days: int
    review_auto_demote_days: int | None


class CollectionGovernanceUpdate(BaseModel):
    """Fields left out stay as they are; null clears them (the escalation period cannot be
    cleared: every collection has one)."""

    owner_id: uuid.UUID | None = None
    review_interval_days: int | None = Field(default=None, ge=1, le=3650)
    review_escalation_days: int = Field(
        default=14,
        ge=1,
        le=ESCALATION_DAYS_MAX,
        description="days an official document's review may be overdue before the collection's "
        "owner is told; as many days later, the admins",
    )
    review_auto_demote_days: int | None = Field(
        default=None,
        ge=1,
        le=AUTO_DEMOTE_DAYS_MAX,
        description="days after the admins were told before it is demoted to reference "
        "(null: never)",
    )


def _admin_collection_out(
    c: Collection, count: int, grants: Sequence[CollectionGrant], people: dict[uuid.UUID, User]
) -> AdminCollectionOut:
    return AdminCollectionOut(
        id=c.id,
        key=c.key,
        name=c.name,
        description=c.description,
        scope=c.scope,
        department_id=c.department_id,
        default_trust=c.default_trust,
        default_sensitivity=c.default_sensitivity,
        document_count=count,
        grants=[
            GrantOut(principal=g.principal, relation=g.relation)
            for g in grants
            if g.collection_id == c.id
        ],
        owner=_person(people, c.owner_id),
        review_interval_days=c.review_interval_days,
        review_escalation_days=c.review_escalation_days,
        review_auto_demote_days=c.review_auto_demote_days,
    )


@admin_router.get("")
async def list_all_collections(ctx: AdminContext, request: Request) -> Items[AdminCollectionOut]:
    """Every collection with its sharing, for administrators. Content stays out of reach:
    listing a collection here does not let an admin read its documents."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        collections = (await session.scalars(select(Collection).order_by(Collection.name))).all()
        grants = (await session.scalars(select(CollectionGrant))).all()
        rows = await session.execute(
            select(Document.collection_id, func.count()).group_by(Document.collection_id)
        )
        counts = {collection_id: count for collection_id, count in rows}
        people = await _people(session, {c.owner_id for c in collections})
    return Items(
        items=[_admin_collection_out(c, counts.get(c.id, 0), grants, people) for c in collections]
    )


@admin_router.patch("/{collection_id}")
async def update_collection_governance(
    ctx: AdminContext, request: Request, collection_id: uuid.UUID, body: CollectionGovernanceUpdate
) -> AdminCollectionOut:
    """The collection's owner (who approves official status and sees its governance overview)
    and its review interval. The owner can view and edit the collection, so changing the owner
    updates both permission locks like a sharing change does."""
    resources = resources_of(request)
    sent = body.model_fields_set
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        collection = await session.get(Collection, collection_id)
        if collection is None:
            raise ApiError(404, "not_found", "Collection not found")
        grants = list(
            (
                await session.scalars(
                    select(CollectionGrant).where(CollectionGrant.collection_id == collection_id)
                )
            ).all()
        )

        def governance_state() -> dict[str, object]:
            return {
                "owner_id": str(collection.owner_id) if collection.owner_id else None,
                "review_interval_days": collection.review_interval_days,
                "review_escalation_days": collection.review_escalation_days,
                "review_auto_demote_days": collection.review_auto_demote_days,
            }

        before = governance_state()
        owner_changed = "owner_id" in sent and body.owner_id != collection.owner_id
        rerouted = 0
        if owner_changed:
            if body.owner_id is not None:
                owner = await session.get(User, body.owner_id)
                if owner is None or owner.status != "active":
                    raise ApiError(422, "unknown_user", "Choose an active person as the owner")
            old_tuples = collection_tuples(ctx.tenant_id, collection, grants)
            collection.owner_id = body.owner_id
            new_tuples = collection_tuples(ctx.tenant_id, collection, grants)
            enqueue(
                session,
                ctx.tenant_id,
                writes=new_tuples - old_tuples,
                deletes=old_tuples - new_tuples,
            )
            await session.flush()
            # Pending official-status requests go to whoever decides them now.
            rerouted = await governance.reroute_official_requests(session, ctx, collection)
        rescheduled = 0
        if (
            "review_interval_days" in sent
            and body.review_interval_days != collection.review_interval_days
        ):
            collection.review_interval_days = body.review_interval_days
            await session.flush()
            rescheduled = await governance.reschedule_collection(session, collection)
        # Escalation periods apply from the next daily run; stages already reached stay.
        if "review_escalation_days" in sent:
            collection.review_escalation_days = body.review_escalation_days
        if "review_auto_demote_days" in sent:
            collection.review_auto_demote_days = body.review_auto_demote_days
        await session.flush()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.collection.governance_changed",
            resource_type="collection",
            resource_id=str(collection_id),
            details={
                "before": before,
                "after": governance_state(),
                "documents_rescheduled": rescheduled,
                "official_requests_rerouted": rerouted,
            },
        )
        count = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.collection_id == collection_id)
        )
        people = await _people(session, {collection.owner_id})
        out = _admin_collection_out(collection, count or 0, grants, people)
    if owner_changed:
        await refresh_collection_acl(
            resources.qdrant, resources.collection_name, ctx.tenant_id, collection, grants
        )
    return out


@admin_router.post("", status_code=201)
async def create_collection(
    ctx: AdminContext, request: Request, body: CollectionCreate
) -> CollectionDetail:
    if body.scope == "department" and body.department_id is None:
        raise ApiError(422, "department_required", "A department collection needs a department")
    viewers, editors = resolve_grants(
        baseline_viewers(ctx.tenant_id, body.scope, body.department_id), body.viewers, body.editors
    )
    if body.scope == "restricted" and not (viewers or editors):
        raise ApiError(422, "viewers_required", "A restricted collection needs explicit viewers")
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        if (
            body.department_id is not None
            and await session.get(Department, body.department_id) is None
        ):
            raise ApiError(422, "unknown_department", "Unknown department")
        await check_principals(session, ctx.tenant_id, [*body.viewers, *body.editors])
        if (await session.scalars(select(Collection).where(Collection.key == body.key))).first():
            raise ApiError(409, "slug_taken", "A collection with this key already exists")
        collection = Collection(
            tenant_id=ctx.tenant_id,
            key=body.key,
            name=body.name,
            description=body.description,
            scope=body.scope,
            department_id=body.department_id,
            default_trust=body.default_trust,
            default_sensitivity=body.default_sensitivity,
            review_interval_days=body.review_interval_days,
        )
        session.add(collection)
        await session.flush()
        grants = [
            CollectionGrant(
                tenant_id=ctx.tenant_id, collection_id=collection.id, principal=p, relation="viewer"
            )
            for p in viewers
        ]
        grants += [
            CollectionGrant(
                tenant_id=ctx.tenant_id, collection_id=collection.id, principal=p, relation="editor"
            )
            for p in editors
        ]
        session.add_all(grants)
        await session.flush()
        enqueue(session, ctx.tenant_id, writes=collection_tuples(ctx.tenant_id, collection, grants))
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.collection.created",
            resource_type="collection",
            resource_id=str(collection.id),
            details={
                "key": body.key,
                "scope": body.scope,
                "viewers": viewers,
                "editors": editors,
            },
        )
        access = service.CollectionAccess(collection, grants, can_view=True, can_edit=True)
        base = _collection_out(access, 0)
    return CollectionDetail(
        **base.model_dump(),
        grants=[GrantOut(principal=g.principal, relation=g.relation) for g in grants],
    )


@admin_router.put("/{collection_id}/grants")
async def replace_grants(
    ctx: AdminContext, request: Request, collection_id: uuid.UUID, body: GrantsUpdate
) -> Items[GrantOut]:
    """Admins manage sharing without needing to read the collection (separation of duties)."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        collection = await session.get(Collection, collection_id)
        if collection is None:
            raise ApiError(404, "not_found", "Collection not found")
        old = list(
            (
                await session.scalars(
                    select(CollectionGrant).where(CollectionGrant.collection_id == collection_id)
                )
            ).all()
        )
        # New principals only: a grant that is already there may stay as it is.
        granted = {g.principal for g in old}
        added = [p for p in (*body.viewers, *body.editors) if p not in granted]
        await check_principals(session, ctx.tenant_id, added)
        viewers, editors = resolve_grants(
            baseline_viewers(ctx.tenant_id, collection.scope, collection.department_id),
            body.viewers,
            body.editors,
        )
        new = [
            CollectionGrant(
                tenant_id=ctx.tenant_id, collection_id=collection_id, principal=p, relation=relation
            )
            for relation, principals in (("viewer", viewers), ("editor", editors))
            for p in principals
        ]
        if collection.scope == "restricted" and not new:
            raise ApiError(
                422, "viewers_required", "A restricted collection needs explicit viewers"
            )
        old_tuples = collection_tuples(ctx.tenant_id, collection, old)
        for grant in old:
            await session.delete(grant)
        await session.flush()
        session.add_all(new)
        await session.flush()
        new_tuples = collection_tuples(ctx.tenant_id, collection, new)
        enqueue(
            session, ctx.tenant_id, writes=new_tuples - old_tuples, deletes=old_tuples - new_tuples
        )
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.collection.grants_replaced",
            resource_type="collection",
            resource_id=str(collection_id),
            details={"viewers": viewers, "editors": editors},
        )
    await refresh_collection_acl(
        resources.qdrant, resources.collection_name, ctx.tenant_id, collection, new
    )
    return Items(items=[GrantOut(principal=g.principal, relation=g.relation) for g in new])


# --- A document's own access list (docs/permission-sync.md) ----------------------------------

AclOrigin = Literal["source", "manual"]


class AclEntryOut(BaseModel):
    principal: str | None  # None: the source named someone WorkMind could not map (no access)
    external: str | None  # how the source named them; None for an entry an editor set


class DocumentAclOut(BaseModel):
    document_id: uuid.UUID
    own_acl: bool
    origin: AclOrigin | None
    updated_at: datetime | None
    entries: list[AclEntryOut]


class DocumentAclUpdate(BaseModel):
    """`viewers`: who may see the document, within the collection's access (null: no list of
    its own, back to the collection's access)."""

    viewers: list[Principal] | None = Field(max_length=500)


def _acl_out(document: Document, grants: Sequence[DocumentGrant]) -> DocumentAclOut:
    return DocumentAclOut(
        document_id=document.id,
        own_acl=document.own_acl,
        origin=cast(AclOrigin | None, document.acl_origin),
        updated_at=document.acl_updated_at,
        entries=[
            AclEntryOut(principal=g.principal, external=g.external)
            for g in sorted(grants, key=lambda g: (g.external or "", g.principal or ""))
        ],
    )


@router.get("/documents/{document_id}/acl")
async def get_document_acl(
    ctx: ReadContext, request: Request, document_id: uuid.UUID
) -> DocumentAclOut:
    """The document's own access list, for the collection's editors who can see it."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        document, _ = await _document_for(session, ctx, document_id, edit=True)
        grants = (await document_acl.grants_of(session, [document.id]))[document.id]
        return _acl_out(document, grants)


@router.put("/documents/{document_id}/acl")
async def set_document_acl(
    ctx: SessionContext, request: Request, document_id: uuid.UUID, body: DocumentAclUpdate
) -> DocumentAclOut:
    """A collection editor limits an uploaded document to some of the collection's viewers, or
    lifts the limit. Synced documents follow their source (permission sync) and meeting
    documents their meeting, so neither is changed here. The list only narrows: someone on it
    who cannot view the collection still cannot see the document. The editor must stay on it,
    so nobody locks themselves out by mistake."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        document, _ = await _document_for(session, ctx, document_id, edit=True)
        if document.source_id is not None or document.source == "meeting":
            raise ApiError(
                409,
                "acl_from_source",
                "This document's access follows its source",
                "Synced documents take their access from the source (docs/permission-sync.md), "
                "meeting documents from their meeting.",
            )
        entries: list[document_acl.AclEntry] | None = None
        if body.viewers is not None:
            viewers = sorted(set(body.viewers))
            await check_principals(session, ctx.tenant_id, viewers)
            if not set(viewers) & ctx.principals:
                raise ApiError(
                    422,
                    "would_lose_access",
                    "You would lose access to this document",
                    "Include yourself, or a group or department you belong to.",
                )
            entries = [document_acl.AclEntry(p) for p in viewers]
        changed = await document_acl.set_acl(
            session,
            ctx.tenant_id,
            document,
            entries,
            origin="manual",
            actor=Actor(type="user", id=ctx.user_id),
        )
        grants = (await document_acl.grants_of(session, [document.id]))[document.id]
        payload = document_payload(document, grants)
        out = _acl_out(document, grants)
    if changed:
        await document_acl.refresh_payload(
            resources.qdrant, resources.collection_name, ctx.tenant_id, document_id, payload
        )
    return out

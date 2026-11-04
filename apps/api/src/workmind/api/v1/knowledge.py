"""Knowledge Hub API: collections, documents, uploads (ARCHITECTURE sections 10, 18).

Reading needs the `knowledge:read` scope (browser sessions have every scope); changing anything
needs a browser session. Uploads send the raw file as the request body:

    POST /api/v1/collections/{id}/documents?filename=handbook.pdf   (Content-Type: application/pdf)
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import SessionContext, require_scope, resources_of, settings_of
from workmind.api.v1.admin import AdminContext
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.authz.sync import enqueue
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.knowledge import service
from workmind.modules.knowledge.acl import collection_tuples
from workmind.modules.knowledge.models import (
    Chunk,
    Collection,
    CollectionGrant,
    Document,
    DocumentVersion,
)
from workmind.modules.knowledge.pipeline import delete_document_points, refresh_collection_acl

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


class GrantOut(BaseModel):
    principal: str
    relation: str


class CollectionDetail(CollectionOut):
    grants: list[GrantOut]


class DocumentOut(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    filename: str
    mime_type: str
    trust: str
    sensitivity: str
    status: str
    error: str | None
    chunk_count: int
    size_bytes: int
    version: int
    updated_at: datetime


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


def _collection_out(access: service.CollectionAccess, count: int) -> CollectionOut:
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
    )


def _document_out(document: Document, version: DocumentVersion | None) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        collection_id=document.collection_id,
        title=document.title,
        filename=document.filename,
        mime_type=document.mime_type,
        trust=document.trust,
        sensitivity=document.sensitivity,
        status=document.status,
        error=document.error,
        chunk_count=version.chunk_count if version else 0,
        size_bytes=version.size_bytes if version else 0,
        version=version.version_no if version else 0,
        updated_at=document.updated_at,
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
        access = await service.collection_access(session, ctx, document.collection_id, edit=edit)
    except ApiError as exc:
        if exc.status == 404:
            raise ApiError(
                404, "not_found", "Document not found"
            ) from exc  # same answer as missing
        raise
    return document, access


@router.get("/collections")
async def list_collections(ctx: ReadContext, request: Request) -> Items[CollectionOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        visible = await service.visible_collections(session, ctx)
    return Items(items=[_collection_out(access, count) for access, count in visible])


@router.get("/collections/{collection_id}")
async def get_collection(
    ctx: ReadContext, request: Request, collection_id: uuid.UUID
) -> CollectionDetail:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        access = await service.collection_access(session, ctx, collection_id)
        visible = {a.collection.id: n for a, n in await service.visible_collections(session, ctx)}
    base = _collection_out(access, visible.get(collection_id, 0))
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
                    .where(Document.collection_id == collection_id)
                    .order_by(Document.title)
                )
            ).all()
        )
        versions = await _latest_versions(session, documents)
    return Items(items=[_document_out(d, versions.get(d.id)) for d in documents])


@router.post("/collections/{collection_id}/documents", status_code=202)
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
        versions = (
            await session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.version_no.desc())
            )
        ).all()
    base = _document_out(document, versions[0] if versions else None)
    return DocumentDetail(
        **base.model_dump(),
        versions=[VersionOut.model_validate(v, from_attributes=True) for v in versions],
        can_edit=access.can_edit,
    )


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
async def download_document(ctx: ReadContext, request: Request, document_id: uuid.UUID) -> Response:
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
    data = await resources.storage.get(key)
    safe_name = filename.replace('"', "")
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/documents/{document_id}/reindex", status_code=202)
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


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    ctx: SessionContext, request: Request, document_id: uuid.UUID
) -> Response:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        document, _ = await _document_for(session, ctx, document_id, edit=True)
        orphaned = await service.delete_document(session, ctx, document)
    await delete_document_points(
        resources.qdrant, resources.collection_name, ctx.tenant_id, document_id
    )
    for key in orphaned:
        await resources.storage.delete(key)
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


class GrantsUpdate(BaseModel):
    viewers: list[Principal]
    editors: list[Principal]


admin_router = APIRouter(prefix="/admin/collections", tags=["admin"])


def _default_viewers(ctx: RequestContext, body: CollectionCreate) -> list[str]:
    if body.scope == "enterprise":
        return [f"org:{ctx.tenant_id}"]
    if body.scope == "department" and body.department_id:
        return [f"dept:{body.department_id}"]
    return []


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
    return Items(
        items=[
            AdminCollectionOut(
                id=c.id,
                key=c.key,
                name=c.name,
                description=c.description,
                scope=c.scope,
                department_id=c.department_id,
                default_trust=c.default_trust,
                default_sensitivity=c.default_sensitivity,
                document_count=counts.get(c.id, 0),
                grants=[
                    GrantOut(principal=g.principal, relation=g.relation)
                    for g in grants
                    if g.collection_id == c.id
                ],
            )
            for c in collections
        ]
    )


@admin_router.post("", status_code=201)
async def create_collection(
    ctx: AdminContext, request: Request, body: CollectionCreate
) -> CollectionDetail:
    if body.scope == "department" and body.department_id is None:
        raise ApiError(422, "department_required", "A department collection needs a department")
    viewers = sorted(set(body.viewers) | set(_default_viewers(ctx, body)))
    if body.scope == "restricted" and not viewers:
        raise ApiError(422, "viewers_required", "A restricted collection needs explicit viewers")
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
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
            for p in sorted(set(body.editors))
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
                "editors": body.editors,
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
        new = [
            CollectionGrant(
                tenant_id=ctx.tenant_id, collection_id=collection_id, principal=p, relation="viewer"
            )
            for p in sorted(set(body.viewers))
        ]
        new += [
            CollectionGrant(
                tenant_id=ctx.tenant_id, collection_id=collection_id, principal=p, relation="editor"
            )
            for p in sorted(set(body.editors))
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
            details={"viewers": body.viewers, "editors": body.editors},
        )
    await refresh_collection_acl(
        resources.qdrant, resources.collection_name, ctx.tenant_id, collection, new
    )
    return Items(items=[GrantOut(principal=g.principal, relation=g.relation) for g in new])

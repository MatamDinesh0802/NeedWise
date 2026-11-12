"""Sources (connectors), for administrators. Credentials are write-only: sealed on the way in,
never returned. Every synced document lands in the source's collection with its access."""

import asyncio
import base64
import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import rate_limit, resources_of, settings_of
from workmind.api.v1.admin import AdminContext
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_key, tenant_transaction
from workmind.modules.connectors.models import Source
from workmind.modules.connectors.registry import CREDENTIALS, build_connector, validate_config
from workmind.modules.connectors.secrets import SecretBox
from workmind.modules.connectors.sync import SyncDeps, sync_source
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import SLUG_PATTERN
from workmind.modules.knowledge import service
from workmind.modules.knowledge.models import Collection, Document
from workmind.modules.knowledge.pipeline import delete_document_points
from workmind.modules.knowledge.storage import owned_key
from workmind.providers.factory import secret

router = APIRouter(prefix="/admin/sources", tags=["admin"])
oauth_router = APIRouter(prefix="/oauth", tags=["admin"])
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"  # noqa: S105  (an endpoint)
GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
OAUTH_TTL_S = 600


class SourceCreate(BaseModel):
    key: str = Field(pattern=SLUG_PATTERN, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["web", "github", "slack", "gdrive"]
    collection: str = Field(description="key of the collection documents are synced into")
    config: dict[str, Any]
    credentials: dict[str, str] | None = None
    interval_minutes: int = Field(default=60, ge=5, le=10080)
    enabled: bool = True


class SourceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any]
    interval_minutes: int = Field(ge=5, le=10080)
    enabled: bool


class SourceOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    kind: str
    collection: str
    config: dict[str, Any]
    has_credentials: bool
    needs: list[str]  # credential fields this kind expects
    interval_minutes: int
    enabled: bool
    status: str
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    stats: dict[str, Any]
    documents: int


def _secrets(request: Request) -> SecretBox:
    return SecretBox(settings_of(request).secrets_key.get_secret_value())


def _out(s: Source, collection_key: str, documents: int) -> SourceOut:
    return SourceOut(
        id=s.id,
        key=s.key,
        name=s.name,
        kind=s.kind,
        collection=collection_key,
        config=s.config,
        has_credentials=s.credentials is not None,
        needs=list(CREDENTIALS.get(s.kind, ())),
        interval_minutes=s.interval_minutes,
        enabled=s.enabled,
        status=s.status,
        last_sync_at=s.last_sync_at,
        last_success_at=s.last_success_at,
        last_error=s.last_error,
        stats=s.stats,
        documents=documents,
    )


def _config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_config(kind, config)
    except (ValueError, ValidationError) as exc:
        raise ApiError(
            422, "invalid_config", "The source configuration is invalid", str(exc)[:500]
        ) from exc


async def _source(session: AsyncSession, key: str) -> Source:
    source = await session.scalar(select(Source).where(Source.key == key))
    if source is None:
        raise ApiError(404, "not_found", "Source not found")
    return source


async def _audit(session: AsyncSession, ctx: RequestContext, action: str, source: Source) -> None:
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action=action,
        resource_type="source",
        resource_id=source.key,
        details={"kind": source.kind, "enabled": source.enabled},
    )


@router.get("")
async def list_sources(ctx: AdminContext, request: Request) -> list[SourceOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        sources = (await session.scalars(select(Source).order_by(Source.name))).all()
        keys = {c.id: c.key for c in await session.scalars(select(Collection))}
        rows = await session.execute(
            select(Document.source_id, func.count())
            .where(Document.source_id.is_not(None))
            .group_by(Document.source_id)
        )
        counts = {source_id: count for source_id, count in rows}
    return [_out(s, keys.get(s.collection_id, ""), counts.get(s.id, 0)) for s in sources]


@router.post("", status_code=201)
async def create_source(ctx: AdminContext, request: Request, body: SourceCreate) -> SourceOut:
    config = _config(body.kind, body.config)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        collection = await session.scalar(
            select(Collection).where(Collection.key == body.collection)
        )
        if collection is None:
            raise ApiError(422, "unknown_collection", "Unknown collection", body.collection)
        if await session.scalar(select(Source.id).where(Source.key == body.key)):
            raise ApiError(409, "slug_taken", "A source with this key already exists")
        source = Source(
            tenant_id=ctx.tenant_id,
            key=body.key,
            name=body.name,
            kind=body.kind,
            collection_id=collection.id,
            owner_id=ctx.user_id,
            config=config,
            interval_minutes=body.interval_minutes,
            enabled=body.enabled,
        )
        session.add(source)
        await session.flush()
        if body.credentials:
            source.credentials = _secrets(request).seal(source.id, body.credentials)
        await _audit(session, ctx, "admin.source.created", source)
        return _out(source, collection.key, 0)


@router.put("/{key}")
async def update_source(
    ctx: AdminContext, request: Request, key: str, body: SourceUpdate
) -> SourceOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        source = await _source(session, key)
        source.name, source.interval_minutes, source.enabled = (
            body.name,
            body.interval_minutes,
            body.enabled,
        )
        source.config = _config(source.kind, body.config)
        collection = await session.get(Collection, source.collection_id)
        await _audit(session, ctx, "admin.source.updated", source)
        count = await session.scalar(
            select(func.count()).select_from(Document).where(Document.source_id == source.id)
        )
        return _out(source, collection.key if collection else "", count or 0)


@router.put("/{key}/credentials", status_code=204)
async def set_credentials(
    ctx: AdminContext, request: Request, key: str, body: dict[str, str]
) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        source = await _source(session, key)
        source.credentials = _secrets(request).seal(source.id, body) if body else None
        await _audit(session, ctx, "admin.source.credentials_changed", source)
    return Response(status_code=204)


@router.delete("/{key}", status_code=204)
async def delete_source(ctx: AdminContext, request: Request, key: str) -> Response:
    """Removes the source and every document it synced (and their search index entries)."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        source = await _source(session, key)
        documents = (
            await session.scalars(select(Document).where(Document.source_id == source.id))
        ).all()
        orphaned: list[str] = []
        for document in documents:
            orphaned += await service.delete_document(session, ctx, document)
        await _audit(session, ctx, "admin.source.deleted", source)
        await session.delete(source)
        ids = [d.id for d in documents]
    for document_id in ids:
        await delete_document_points(
            resources.qdrant, resources.collection_name, ctx.tenant_id, document_id
        )
    for storage_key in set(orphaned):
        await resources.storage.delete(owned_key(ctx.tenant_id, storage_key))
    return Response(status_code=204)


def sync_deps(request: Request) -> SyncDeps:
    resources, settings = resources_of(request), settings_of(request)
    return SyncDeps(
        sessions=resources.db,
        storage=resources.storage,
        ingestion=resources.ingestion,
        qdrant=resources.qdrant,
        collection_name=resources.collection_name,
        secrets=_secrets(request),
        connectors=lambda source, credentials: build_connector(
            source,
            credentials,
            http=resources.http,
            google_client=(settings.google_client_id, secret(settings.google_client_secret)),
        ),
        max_bytes=settings.max_upload_bytes,
    )


@router.post("/{key}/sync", status_code=202, dependencies=[rate_limit("sync")])
async def sync_now(ctx: AdminContext, request: Request, key: str) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        source = await _source(session, key)
        source_id = source.id
    if settings_of(request).ingestion_mode == "celery":
        from workmind.workers.celery_app import SYNC_TASK, app

        args = [str(ctx.tenant_id), str(source_id)]
        await asyncio.to_thread(app.send_task, SYNC_TASK, args=args)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  (Celery is untyped)
    else:  # development and tests: in the API process
        resources_of(request).runs.start(
            _sync_in_process(sync_deps(request), ctx.tenant_id, source_id)
        )
    return Response(status_code=202)


async def _sync_in_process(deps: SyncDeps, tenant_id: uuid.UUID, source_id: uuid.UUID) -> None:
    await sync_source(deps, tenant_id, source_id)


# --- Google Drive OAuth (authorization code + PKCE) ------------------------------------------


def _oauth_key(ctx: RequestContext, state: str) -> str:
    return tenant_key(ctx.tenant_id, "oauth", "google", state)


@router.get("/{key}/oauth/google/start")
async def google_start(ctx: AdminContext, request: Request, key: str) -> RedirectResponse:
    settings, resources = settings_of(request), resources_of(request)
    if not settings.google_client_id:
        raise ApiError(
            409, "oauth_not_configured", "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first"
        )
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        source = await _source(session, key)
        if source.kind != "gdrive":
            raise ApiError(409, "wrong_kind", "Only Google Drive sources use Google OAuth")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    flow = {
        "tenant_id": str(ctx.tenant_id),
        "user_id": str(ctx.user_id),
        "source": key,
        "verifier": verifier,
    }
    # Under the tenant's prefix: a callback in another tenant's session never even finds it.
    await resources.valkey.set(_oauth_key(ctx, state), json.dumps(flow), ex=OAUTH_TTL_S)
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": f"{settings.public_url}/api/v1/oauth/google/callback",
            "response_type": "code",
            "scope": GOOGLE_SCOPE,
            "access_type": "offline",  # a refresh token, so syncs keep working
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return RedirectResponse(f"{GOOGLE_AUTH}?{query}", status_code=302)


@oauth_router.get("/google/callback")
async def google_callback(
    ctx: AdminContext, request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    settings, resources = settings_of(request), resources_of(request)
    raw = await resources.valkey.getdel(_oauth_key(ctx, state)) if state else None
    if error or not code or raw is None:
        return RedirectResponse("/admin/sources?oauth=failed", status_code=303)
    flow = json.loads(raw)
    if flow["tenant_id"] != str(ctx.tenant_id) or flow["user_id"] != str(ctx.user_id):
        return RedirectResponse("/admin/sources?oauth=failed", status_code=303)  # another session
    response = await resources.http.post(
        GOOGLE_TOKEN,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{settings.public_url}/api/v1/oauth/google/callback",
            "client_id": settings.google_client_id or "",
            "client_secret": secret(settings.google_client_secret) or "",
            "code_verifier": flow["verifier"],
        },
        timeout=20,
    )
    if response.is_error:
        return RedirectResponse("/admin/sources?oauth=failed", status_code=303)
    token = response.json()
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        source = await _source(session, flow["source"])
        source.credentials = _secrets(request).seal(
            source.id,
            {
                "refresh_token": token.get("refresh_token", ""),
                "access_token": token.get("access_token", ""),
            },
        )
        await _audit(session, ctx, "admin.source.connected", source)
    return RedirectResponse(
        f"/admin/sources?oauth=connected&source={flow['source']}", status_code=303
    )


class StaleDocument(BaseModel):
    id: uuid.UUID
    title: str
    collection: str
    reason: Literal["expired", "not_updated"]
    updated_at: datetime
    valid_until: datetime | None


@router.get("/stale", tags=["admin"])
async def stale_documents(
    ctx: AdminContext, request: Request, days: int = 365
) -> list[StaleDocument]:
    """Documents past their `valid_until`, or not updated for `days`: candidates for review.
    Admins see titles only, never content."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        keys = {c.id: c.key for c in await session.scalars(select(Collection))}
        rows = (
            await session.scalars(
                select(Document)
                .where((Document.valid_until < func.now()) | (Document.updated_at < cutoff))
                .order_by(Document.updated_at)
                .limit(500)
            )
        ).all()
    now = datetime.now(UTC)
    return [
        StaleDocument(
            id=d.id,
            title=d.title,
            collection=keys.get(d.collection_id, ""),
            reason="expired" if d.valid_until and d.valid_until < now else "not_updated",
            updated_at=d.updated_at,
            valid_until=d.valid_until,
        )
        for d in rows
    ]

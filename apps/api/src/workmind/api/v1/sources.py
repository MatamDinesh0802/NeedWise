"""Sources (connectors) and their health (docs/connectors.md). Credentials are write-only:
sealed on the way in, never returned. Every synced document lands in the source's collection
with its access.

Who manages what (checked here, on every request): org owners and admins manage every source;
the owner of a collection manages the sources that sync into it, and adds new ones to it.
Someone who owns no collection gets 403; a source outside the caller's collections is 404.
Managing sources needs a browser session: access tokens cannot.
"""

import asyncio
import base64
import hashlib
import json
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import CurrentContext, rate_limit, resources_of, settings_of
from workmind.api.v1.admin import AdminContext, PersonRef
from workmind.core.config import Settings
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.resources import Resources
from workmind.core.tenancy import tenant_key, tenant_transaction
from workmind.modules.connectors import oauth
from workmind.modules.connectors.base import ConnectorError
from workmind.modules.connectors.health import (
    DEFAULT_STALE_DAYS,
    Health,
    StaleReason,
    explain,
    health,
    next_sync_at,
)
from workmind.modules.connectors.models import Source, SourceRun
from workmind.modules.connectors.oauth import OAuthApp, OAuthApps
from workmind.modules.connectors.registry import (
    CREDENTIALS,
    KINDS,
    build_connector,
    validate_config,
)
from workmind.modules.connectors.reminders import stale_condition, stale_reason
from workmind.modules.connectors.secrets import SecretBox
from workmind.modules.connectors.sync import SyncDeps, sync_source
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import SLUG_PATTERN, Department, Group, User
from workmind.modules.knowledge import document_acl, governance, service
from workmind.modules.knowledge.acl import acl_principals
from workmind.modules.knowledge.governance import is_org_admin
from workmind.modules.knowledge.models import Collection, CollectionGrant, Document
from workmind.modules.knowledge.pipeline import delete_document_points
from workmind.modules.knowledge.storage import owned_key
from workmind.providers.factory import secret

log = structlog.get_logger("workmind.sources")
router = APIRouter(prefix="/admin/sources", tags=["admin"])
oauth_router = APIRouter(prefix="/oauth", tags=["admin"])
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"  # noqa: S105  (an endpoint)
GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SLACK_AUTH = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN = "https://slack.com/api/oauth.v2.access"  # noqa: S105  (an endpoint)
SLACK_SCOPES = "channels:history,channels:read,groups:history,groups:read"
OAUTH_TTL_S = 600
Kind = Literal["web", "github", "slack", "gdrive", "confluence", "sharepoint"]
OAuthProvider = Literal["google", "slack", "atlassian", "microsoft"]
OAUTH_KIND: dict[str, str] = {
    "google": "gdrive",
    "slack": "slack",
    "atlassian": "confluence",
    "microsoft": "sharepoint",
}
KIND_OAUTH: dict[str, OAuthProvider] = {
    "gdrive": "google",
    "slack": "slack",
    "confluence": "atlassian",
    "sharepoint": "microsoft",
}
PROVIDER_NAMES: dict[str, str] = {
    "google": "Google Drive",
    "slack": "Slack",
    "atlassian": "Confluence",
    "microsoft": "SharePoint/OneDrive",
}
OAUTH_SETTINGS: dict[str, str] = {
    "google": "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET",
    "slack": "SLACK_CLIENT_ID and SLACK_CLIENT_SECRET",
    "atlassian": "ATLASSIAN_CLIENT_ID and ATLASSIAN_CLIENT_SECRET",
    "microsoft": "MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET",
}
# Where the browser goes back to after the provider's consent screen.
BACK_PAGES: dict[str, str] = {"admin": "/admin/sources", "knowledge": "/knowledge/sources"}
Back = Literal["admin", "knowledge"]


def default_stale_after(kind: str) -> int | None:
    """Slack history is an archive (a month that ended never changes): no staleness."""
    return None if kind == "slack" else DEFAULT_STALE_DAYS


async def _manager(ctx: CurrentContext) -> RequestContext:
    # Like administration: credentials and syncs need a browser session, never a token.
    if not ctx.has_scope("admin"):
        raise ApiError(403, "scope_required", "Access tokens cannot manage sources")
    return ctx


ManagerContext = Annotated[RequestContext, Depends(_manager)]


@dataclass(frozen=True)
class Scope:
    everything: bool  # org admins: every source; others: the sources of collections they own
    collections: dict[uuid.UUID, Collection]


async def _scope(session: AsyncSession, ctx: RequestContext) -> Scope:
    everything = is_org_admin(ctx)
    query = select(Collection).order_by(Collection.name)
    if not everything:
        query = query.where(Collection.owner_id == ctx.user_id)
    collections = {c.id: c for c in (await session.scalars(query)).all()}
    if not everything and not collections:
        raise ApiError(
            403,
            "forbidden",
            "Not allowed",
            "Sources are managed by org admins and by the owners of the collections they "
            "sync into.",
        )
    return Scope(everything, collections)


# --- Schemas ----------------------------------------------------------------------------------


class SourceCreate(BaseModel):
    key: str = Field(pattern=SLUG_PATTERN, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    kind: Kind
    collection: str = Field(description="key of the collection documents are synced into")
    config: dict[str, Any]
    credentials: dict[str, str] | None = None
    interval_minutes: int = Field(default=60, ge=5, le=10080)
    enabled: bool = True
    stale_after_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="days without a change in the source before a document is stale; null: "
        "never. Left out: 180, or never for Slack.",
    )


class SourceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any]
    interval_minutes: int = Field(ge=5, le=10080)
    enabled: bool
    stale_after_days: int | None = Field(
        default=None, ge=1, le=3650, description="left out: unchanged; null: never stale"
    )


class AccessOut(BaseModel):
    """Who can see what this source syncs: the collection's access, as names."""

    principal: str
    relation: Literal["owner", "editor", "viewer"]
    label: str


class RunError(BaseModel):
    item: str | None  # the item's id in the source (a URL, a path, a file id), or the whole sync
    message: str  # what the source or the engine said
    plain: str  # what it means and what to do


class RunOut(BaseModel):
    id: uuid.UUID
    trigger: Literal["schedule", "manual"]
    requested_by: PersonRef | None
    status: Literal["running", "ok", "error"]
    started_at: datetime
    finished_at: datetime | None
    complete: bool | None
    stats: dict[str, Any]
    errors: list[RunError]


class StaleCounts(BaseModel):
    not_updated: int
    missing_upstream: int


class SourceOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    kind: str
    collection: str  # the collection's key
    collection_id: uuid.UUID
    collection_name: str
    collection_scope: str
    access: list[AccessOut]
    owner: PersonRef | None
    config: dict[str, Any]
    has_credentials: bool
    needs: list[str]  # credential fields this kind expects
    oauth: OAuthProvider | None  # the provider "Connect" uses for this kind
    interval_minutes: int
    enabled: bool
    status: str  # what the engine recorded: idle, syncing, ok, error
    health: Health
    syncing: bool
    consecutive_failures: int
    failure_alerted_at: datetime | None
    last_sync_at: datetime | None
    last_success_at: datetime | None
    next_sync_at: datetime | None
    last_error: str | None
    last_error_plain: str | None
    last_run: RunOut | None
    stats: dict[str, Any]
    documents: int
    stale_after_days: int | None
    stale: StaleCounts


class KindOption(BaseModel):
    kind: Kind
    label: str
    description: str
    credentials: list[str]
    oauth: OAuthProvider | None
    oauth_configured: bool  # the deployment has the provider's OAuth client
    default_stale_after_days: int | None


class CollectionOption(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    scope: str


class SourceOptions(BaseModel):
    scope: Literal["all", "owned"]
    kinds: list[KindOption]
    collections: list[CollectionOption]  # where the caller may add a source
    failure_alert_after: int


class StaleSyncedDocument(BaseModel):
    id: uuid.UUID
    # None when the caller cannot read the document: its collection is closed to them (an
    # admin), or its own access list leaves them out (docs/permission-sync.md).
    title: str | None
    url: str | None  # the original in the source; withheld like the title
    collection_id: uuid.UUID
    collection_name: str
    source: str
    source_name: str
    reason: StaleReason
    changed_at: datetime | None  # last change in the source
    missing_since: datetime | None
    owner: PersonRef | None
    reminded_at: datetime | None


class StaleDocument(BaseModel):
    id: uuid.UUID
    # None when the caller cannot read the document (a restricted collection, or a document
    # whose own access list leaves them out): admins manage, they do not read everything.
    title: str | None
    collection: str
    reason: Literal["expired", "not_updated"]
    updated_at: datetime
    valid_until: datetime | None


# --- Helpers ----------------------------------------------------------------------------------


def _secrets(request: Request) -> SecretBox:
    return SecretBox(settings_of(request).secrets_key.get_secret_value())


def _config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_config(kind, config)
    except (ValueError, ValidationError) as exc:
        raise ApiError(
            422, "invalid_config", "The source configuration is invalid", str(exc)[:500]
        ) from exc


async def _source(session: AsyncSession, scope: Scope, key: str) -> Source:
    source = await session.scalar(select(Source).where(Source.key == key))
    if source is None or (not scope.everything and source.collection_id not in scope.collections):
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


def _person(people: dict[uuid.UUID, User], user_id: uuid.UUID | None) -> PersonRef | None:
    user = people.get(user_id) if user_id else None
    return PersonRef(id=user.id, display_name=user.display_name) if user else None


def _run_out(run: SourceRun, people: dict[uuid.UUID, User]) -> RunOut:
    errors: list[RunError] = []
    for e in run.errors:
        message = str(e.get("message") or "")
        item = e.get("item")
        errors.append(
            RunError(
                item=str(item) if item else None,
                message=message,
                plain=explain(message) or "",
            )
        )
    return RunOut(
        id=run.id,
        trigger="manual" if run.trigger == "manual" else "schedule",
        requested_by=_person(people, run.requested_by),
        status=run.status,  # pyright: ignore[reportArgumentType]  (checked by the table)
        started_at=run.started_at,
        finished_at=run.finished_at,
        complete=run.complete,
        stats=run.stats,
        errors=errors,
    )


async def _people(session: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, User]:
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return {}
    return {u.id: u for u in await session.scalars(select(User).where(User.id.in_(wanted)))}


async def _access(
    session: AsyncSession, collections: Sequence[Collection]
) -> dict[uuid.UUID, list[AccessOut]]:
    """Each collection's access as readable names (the permission mapping of its sources)."""
    ids = [c.id for c in collections]
    grants: Sequence[CollectionGrant] = (
        (
            await session.scalars(
                select(CollectionGrant).where(CollectionGrant.collection_id.in_(ids))
            )
        ).all()
        if ids
        else []
    )
    kinds: dict[str, set[uuid.UUID]] = {"user": set(), "group": set(), "dept": set()}
    for g in grants:
        kind, _, raw = g.principal.partition(":")
        kinds.setdefault("dept" if kind == "dept-manager" else kind, set()).add(uuid.UUID(raw))
    for c in collections:
        if c.owner_id:
            kinds["user"].add(c.owner_id)
    users = await _people(session, set(kinds["user"]))
    groups = (
        {
            g.id: g.name
            for g in await session.scalars(select(Group).where(Group.id.in_(kinds["group"])))
        }
        if kinds["group"]
        else {}
    )
    departments = (
        {
            d.id: d.name
            for d in await session.scalars(
                select(Department).where(Department.id.in_(kinds["dept"]))
            )
        }
        if kinds["dept"]
        else {}
    )

    def label(principal: str) -> str:
        kind, _, raw = principal.partition(":")
        ident = uuid.UUID(raw)
        match kind:
            case "user":
                user = users.get(ident)
                return user.display_name if user else "A former colleague"
            case "group":
                return f"{groups.get(ident, 'A deleted group')} (group)"
            case "dept":
                return f"{departments.get(ident, 'A deleted department')} (department)"
            case "dept-manager":
                return f"Managers of {departments.get(ident, 'a deleted department')}"
            case _:
                return "Everyone in the organization"

    out: dict[uuid.UUID, list[AccessOut]] = {}
    for c in collections:
        entries: list[AccessOut] = []
        if c.owner_id:
            principal = f"user:{c.owner_id}"
            entries.append(AccessOut(principal=principal, relation="owner", label=label(principal)))
        mine = sorted(
            (g for g in grants if g.collection_id == c.id),
            key=lambda g: (g.relation != "editor", g.principal),
        )
        for g in mine:
            entries.append(
                AccessOut(
                    principal=g.principal,
                    relation="editor" if g.relation == "editor" else "viewer",
                    label=label(g.principal),
                )
            )
        out[c.id] = entries
    return out


async def _outputs(
    session: AsyncSession, sources: Sequence[Source], now: datetime
) -> list[SourceOut]:
    if not sources:
        return []
    ids = [s.id for s in sources]
    collections = {
        c.id: c
        for c in await session.scalars(
            select(Collection).where(Collection.id.in_({s.collection_id for s in sources}))
        )
    }
    access = await _access(session, list(collections.values()))
    counts = {
        source_id: count
        for source_id, count in await session.execute(
            select(Document.source_id, func.count())
            .where(Document.source_id.in_(ids))
            .group_by(Document.source_id)
        )
    }
    stale: dict[uuid.UUID, StaleCounts] = {}
    for source_id, missing, not_updated in await session.execute(
        select(
            Document.source_id,
            func.count().filter(Document.source_missing_since.is_not(None)),
            func.count().filter(Document.source_missing_since.is_(None)),
        )
        .where(stale_condition(sources, now))
        .group_by(Document.source_id)
    ):
        if source_id is not None:
            stale[source_id] = StaleCounts(not_updated=not_updated, missing_upstream=missing)
    last_runs = {
        run.source_id: run
        for run in await session.scalars(
            select(SourceRun)
            .where(SourceRun.source_id.in_(ids))
            .ext(distinct_on(SourceRun.source_id))
            .order_by(SourceRun.source_id, SourceRun.started_at.desc())
        )
    }
    people = await _people(
        session,
        {s.owner_id for s in sources} | {r.requested_by for r in last_runs.values()},
    )
    out: list[SourceOut] = []
    for s in sources:
        collection = collections.get(s.collection_id)
        run = last_runs.get(s.id)
        out.append(
            SourceOut(
                id=s.id,
                key=s.key,
                name=s.name,
                kind=s.kind,
                collection=collection.key if collection else "",
                collection_id=s.collection_id,
                collection_name=collection.name if collection else "",
                collection_scope=collection.scope if collection else "",
                access=access.get(s.collection_id, []),
                owner=_person(people, s.owner_id),
                config=s.config,
                has_credentials=s.credentials is not None,
                needs=list(CREDENTIALS.get(s.kind, ())),
                oauth=KIND_OAUTH.get(s.kind),
                interval_minutes=s.interval_minutes,
                enabled=s.enabled,
                status=s.status,
                health=health(s),
                syncing=s.status == "syncing",
                consecutive_failures=s.consecutive_failures,
                failure_alerted_at=s.failure_alerted_at,
                last_sync_at=s.last_sync_at,
                last_success_at=s.last_success_at,
                next_sync_at=next_sync_at(s, now),
                last_error=s.last_error,
                last_error_plain=explain(s.last_error),
                last_run=_run_out(run, people) if run else None,
                stats=s.stats,
                documents=counts.get(s.id, 0),
                stale_after_days=s.stale_after_days,
                stale=stale.get(s.id, StaleCounts(not_updated=0, missing_upstream=0)),
            )
        )
    return out


async def _output(session: AsyncSession, source: Source) -> SourceOut:
    await session.flush()
    await session.refresh(source)
    return (await _outputs(session, [source], datetime.now(UTC)))[0]


# --- Sources ----------------------------------------------------------------------------------


@router.get("")
async def list_sources(ctx: ManagerContext, request: Request) -> list[SourceOut]:
    """Org admins: every source. Collection owners: the sources of the collections they own.
    Anyone else: 403."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        scope = await _scope(session, ctx)
        query = select(Source).order_by(Source.name)
        if not scope.everything:
            query = query.where(Source.collection_id.in_(list(scope.collections)))
        sources = (await session.scalars(query)).all()
        return await _outputs(session, sources, datetime.now(UTC))


@router.get("/options")
async def source_options(ctx: ManagerContext, request: Request) -> SourceOptions:
    """What the "New source" form offers this caller: every kind (with whether its OAuth is set
    up on this deployment) and the collections they may sync into."""
    settings = settings_of(request)
    apps = OAuthApps.from_settings(settings)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        scope = await _scope(session, ctx)
    return SourceOptions(
        scope="all" if scope.everything else "owned",
        kinds=[
            KindOption(
                kind=info.kind,  # pyright: ignore[reportArgumentType]  (KINDS keys are Kind)
                label=info.label,
                description=info.description,
                credentials=list(CREDENTIALS.get(info.kind, ())),
                oauth=KIND_OAUTH.get(info.kind),
                oauth_configured=info.oauth is not None and _app(apps, info.oauth).configured,
                default_stale_after_days=default_stale_after(info.kind),
            )
            for info in KINDS.values()
        ],
        collections=[
            CollectionOption(id=c.id, key=c.key, name=c.name, scope=c.scope)
            for c in scope.collections.values()
        ],
        failure_alert_after=settings.source_failure_alert_after,
    )


class CredentialFieldOut(BaseModel):
    name: str
    label: str
    hint: str
    optional: bool


class OAuthOut(BaseModel):
    provider: OAuthProvider
    # Navigate the browser here to connect: it redirects to the provider's consent screen.
    start_path: str  # with {key} for the source's key
    redirect_uri: str  # what the operator registers with the provider
    configured: bool  # the operator set the client id and secret


class SourceKindOut(BaseModel):
    kind: str
    label: str
    description: str
    auth: Literal["none", "token", "oauth", "oauth_or_token"]
    credentials: list[CredentialFieldOut]  # typed by an admin; write-only
    oauth: OAuthOut | None
    config_schema: dict[str, Any]  # JSON Schema of `config`


@router.get("/kinds")
async def source_kinds(ctx: ManagerContext, request: Request) -> list[SourceKindOut]:
    """Every connector this deployment has: what it is, how it signs in, what to configure.
    For the people who manage sources (org admins, collection owners)."""
    settings = settings_of(request)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await _scope(session, ctx)
    apps = OAuthApps.from_settings(settings)
    return [
        SourceKindOut(
            kind=info.kind,
            label=info.label,
            description=info.description,
            auth=info.auth,
            credentials=[
                CredentialFieldOut(name=c.name, label=c.label, hint=c.hint, optional=c.optional)
                for c in info.credentials
            ],
            oauth=OAuthOut(
                provider=info.oauth,
                start_path=f"/api/v1/admin/sources/{{key}}/oauth/{info.oauth}/start",
                redirect_uri=_redirect_uri(settings.public_url, info.oauth),
                configured=_app(apps, info.oauth).configured,
            )
            if info.oauth
            else None,
            config_schema=info.config.model_json_schema(),
        )
        for info in KINDS.values()
    ]


def _app(apps: OAuthApps, provider: str) -> OAuthApp:
    return {
        "google": apps.google,
        "slack": apps.slack,
        "atlassian": apps.atlassian,
        "microsoft": apps.microsoft,
    }[provider]


def _redirect_uri(public_url: str, provider: str) -> str:
    return f"{public_url.rstrip('/')}/api/v1/oauth/{provider}/callback"


@router.post("", status_code=201)
async def create_source(ctx: ManagerContext, request: Request, body: SourceCreate) -> SourceOut:
    config = _config(body.kind, body.config)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        scope = await _scope(session, ctx)
        collection = await session.scalar(
            select(Collection).where(Collection.key == body.collection)
        )
        if collection is None or (not scope.everything and collection.id not in scope.collections):
            # The same answer for a collection that does not exist and one the caller does not
            # own: a source can only be added by the collection's owner or an org admin.
            raise ApiError(
                422,
                "unknown_collection",
                "Unknown collection",
                "Choose a collection you own (org admins: any collection).",
            )
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
            stale_after_days=body.stale_after_days
            if "stale_after_days" in body.model_fields_set
            else default_stale_after(body.kind),
        )
        session.add(source)
        await session.flush()
        if body.credentials:
            source.credentials = _secrets(request).seal(source.id, body.credentials)
        await _audit(session, ctx, "admin.source.created", source)
        return await _output(session, source)


@router.put("/{key}")
async def update_source(
    ctx: ManagerContext, request: Request, key: str, body: SourceUpdate
) -> SourceOut:
    """What the source syncs (its configuration), the schedule, enabled, and the staleness
    threshold. The collection it syncs into is fixed (docs/connectors.md)."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        source = await _source(session, await _scope(session, ctx), key)
        source.name, source.interval_minutes, source.enabled = (
            body.name,
            body.interval_minutes,
            body.enabled,
        )
        source.config = _config(source.kind, body.config)
        if "stale_after_days" in body.model_fields_set:
            source.stale_after_days = body.stale_after_days
        await _audit(session, ctx, "admin.source.updated", source)
        return await _output(session, source)


async def _set_enabled(ctx: RequestContext, request: Request, key: str, enabled: bool) -> SourceOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        source = await _source(session, await _scope(session, ctx), key)
        source.enabled = enabled
        await _audit(
            session, ctx, "admin.source.resumed" if enabled else "admin.source.paused", source
        )
        return await _output(session, source)


@router.post("/{key}/pause")
async def pause_source(ctx: ManagerContext, request: Request, key: str) -> SourceOut:
    """No scheduled syncs (and no "Sync now") until resumed. The documents stay searchable."""
    return await _set_enabled(ctx, request, key, False)


@router.post("/{key}/resume")
async def resume_source(ctx: ManagerContext, request: Request, key: str) -> SourceOut:
    """Scheduled syncs again; one is due at once when the interval has passed while paused."""
    return await _set_enabled(ctx, request, key, True)


@router.put("/{key}/credentials", status_code=204)
async def set_credentials(
    ctx: ManagerContext, request: Request, key: str, body: dict[str, str]
) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        source = await _source(session, await _scope(session, ctx), key)
        source.credentials = _secrets(request).seal(source.id, body) if body else None
        await _audit(session, ctx, "admin.source.credentials_changed", source)
    return Response(status_code=204)


@router.delete("/{key}", status_code=204)
async def delete_source(ctx: ManagerContext, request: Request, key: str) -> Response:
    """Disconnects the source: removes it, its credentials, its run history and every document
    it synced (and their search index entries)."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        source = await _source(session, await _scope(session, ctx), key)
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
    return sync_deps_from(resources_of(request), settings_of(request))


def sync_deps_from(resources: Resources, settings: Settings) -> SyncDeps:
    """What a sync needs, from the process's resources (the API, Enterprise tasks)."""
    return SyncDeps(
        sessions=resources.db,
        storage=resources.storage,
        ingestion=resources.ingestion,
        qdrant=resources.qdrant,
        collection_name=resources.collection_name,
        secrets=SecretBox(settings.secrets_key.get_secret_value()),
        connectors=lambda source, credentials: build_connector(
            source, credentials, http=resources.http, apps=OAuthApps.from_settings(settings)
        ),
        max_bytes=settings.max_upload_bytes,
        failure_alert_after=settings.source_failure_alert_after,
        extensions=resources.extensions,
    )


@router.post("/{key}/sync", status_code=202, dependencies=[rate_limit("sync")])
async def sync_now(ctx: ManagerContext, request: Request, key: str) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        source = await _source(session, await _scope(session, ctx), key)
        source_id = source.id
        if not source.enabled:  # the scheduler and workers skip it: say so instead of a no-op
            raise ApiError(409, "source_paused", "The source is paused", "Resume it to sync.")
    if settings_of(request).ingestion_mode == "celery":
        from workmind.workers.celery_app import SYNC_TASK, app

        args = [str(ctx.tenant_id), str(source_id)]
        kwargs = {"trigger": "manual", "requested_by": str(ctx.user_id)}
        await asyncio.to_thread(app.send_task, SYNC_TASK, args=args, kwargs=kwargs)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  (Celery is untyped)
    else:  # development and tests: in the API process
        resources_of(request).runs.start(
            _sync_in_process(sync_deps(request), ctx.tenant_id, source_id, ctx.user_id)
        )
    return Response(status_code=202)


async def _sync_in_process(
    deps: SyncDeps, tenant_id: uuid.UUID, source_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await sync_source(deps, tenant_id, source_id, trigger="manual", requested_by=user_id)


@router.get("/{key}/runs")
async def source_runs(
    ctx: ManagerContext,
    request: Request,
    key: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RunOut]:
    """The source's run history, newest first (the newest 100 runs are kept)."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        source = await _source(session, await _scope(session, ctx), key)
        runs = (
            await session.scalars(
                select(SourceRun)
                .where(SourceRun.source_id == source.id)
                .order_by(SourceRun.started_at.desc())
                .limit(limit)
            )
        ).all()
        people = await _people(session, {r.requested_by for r in runs})
    return [_run_out(r, people) for r in runs]


@router.get("/stale-documents")
async def stale_synced_documents(
    ctx: ManagerContext,
    request: Request,
    source: Annotated[str | None, Query(description="one source's key")] = None,
    days: Annotated[
        int | None,
        Query(ge=1, le=3650, description="not changed for this long; default: each source's own"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StaleSyncedDocument]:
    """Synced documents that look stale: missing upstream (a scan cut short no longer lists
    them; kept until a complete scan decides), or not changed in the source for `days` (default:
    the source's own threshold). Missing ones first, then the oldest. Like the governance
    overview, an admin who cannot open a collection sees that its documents are stale, never
    their titles or links."""
    now = datetime.now(UTC)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        scope = await _scope(session, ctx)
        query = select(Source)
        if not scope.everything:
            query = query.where(Source.collection_id.in_(list(scope.collections)))
        if source is not None:
            query = query.where(Source.key == source)
        sources = {s.id: s for s in await session.scalars(query)}
        if source is not None and not sources:
            raise ApiError(404, "not_found", "Source not found")
        documents = (
            await session.scalars(
                select(Document)
                .where(stale_condition(list(sources.values()), now, days))
                .order_by(
                    Document.source_missing_since.is_(None),
                    Document.source_changed_at.nulls_first(),
                    Document.title,
                )
                .limit(limit)
            )
        ).all()
        collections = {
            c.id: c
            for c in await session.scalars(
                select(Collection).where(Collection.id.in_({d.collection_id for d in documents}))
            )
        }
        grants = (
            await session.scalars(
                select(CollectionGrant).where(CollectionGrant.collection_id.in_(list(collections)))
            )
        ).all()
        viewable = {
            c.id
            for c in collections.values()
            if set(acl_principals(c, [g for g in grants if g.collection_id == c.id]))
            & ctx.principals
        }
        hidden = await document_acl.hidden_documents(
            session, ctx.principals, [d.id for d in documents]
        )
        people = await _people(session, {d.owner_id for d in documents})
    out: list[StaleSyncedDocument] = []
    for d in documents:
        s = sources.get(d.source_id) if d.source_id else None
        if s is None:
            continue
        collection = collections.get(d.collection_id)
        visible = d.collection_id in viewable and d.id not in hidden
        out.append(
            StaleSyncedDocument(
                id=d.id,
                title=d.title if visible else None,
                url=d.url if visible else None,
                collection_id=d.collection_id,
                collection_name=collection.name if collection else "",
                source=s.key,
                source_name=s.name,
                reason=stale_reason(d),
                changed_at=d.source_changed_at,
                missing_since=d.source_missing_since,
                owner=_person(people, d.owner_id),
                reminded_at=d.stale_reminded_at,
            )
        )
    return out


@router.get("/stale", tags=["admin"])
async def stale_documents(
    ctx: AdminContext, request: Request, days: int = 365
) -> list[StaleDocument]:
    """Every document (uploads too) past its `valid_until`, or not updated in WorkMind for
    `days`: candidates for review. Org admins only; titles only, never content, and only of
    documents they can read themselves. Synced documents that are stale in their source:
    `GET /admin/sources/stale-documents`."""
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
        withheld = await governance.unreadable_documents(session, ctx, {d.id for d in rows})
    now = datetime.now(UTC)
    return [
        StaleDocument(
            id=d.id,
            title=None if d.id in withheld else d.title,
            collection=keys.get(d.collection_id, ""),
            reason="expired" if d.valid_until and d.valid_until < now else "not_updated",
            updated_at=d.updated_at,
            valid_until=d.valid_until,
        )
        for d in rows
    ]


# --- OAuth: Google Drive (authorization code + PKCE) and Slack (OAuth v2) ---------------------


def _oauth_key(ctx: RequestContext, provider: str, state: str) -> str:
    return tenant_key(ctx.tenant_id, "oauth", provider, state)


def _back_page(ctx: RequestContext, back: str | None) -> str:
    if back in BACK_PAGES:
        return BACK_PAGES[back]
    return BACK_PAGES["admin" if is_org_admin(ctx) else "knowledge"]


async def _oauth_source(
    ctx: RequestContext, request: Request, key: str, provider: OAuthProvider
) -> dict[str, Any]:
    """Checks the caller manages the source and that it uses this provider; its config."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        source = await _source(session, await _scope(session, ctx), key)
        if source.kind != OAUTH_KIND[provider]:
            name = PROVIDER_NAMES[provider]
            raise ApiError(409, "wrong_kind", f"Only {name} sources connect this way")
        return dict(source.config)


async def _start_flow(
    ctx: RequestContext,
    request: Request,
    key: str,
    provider: OAuthProvider,
    back: Back,
    **extra: str,
) -> tuple[str, str]:
    """Checks the caller manages the source and that it uses this provider, then stores the
    flow (single use, ten minutes, under the tenant's prefix: a callback in another tenant's
    session never even finds it). Returns (state, PKCE verifier)."""
    await _oauth_source(ctx, request, key, provider)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    flow = {
        "tenant_id": str(ctx.tenant_id),
        "user_id": str(ctx.user_id),
        "source": key,
        "verifier": verifier,
        "back": back,
        **extra,
    }
    await resources_of(request).valkey.set(
        _oauth_key(ctx, provider, state), json.dumps(flow), ex=OAUTH_TTL_S
    )
    return state, verifier


async def _finish_flow(
    ctx: RequestContext, request: Request, provider: OAuthProvider, state: str
) -> dict[str, Any] | None:
    """The stored flow, once, if it was started in this session's tenant by this person."""
    raw = await resources_of(request).valkey.getdel(_oauth_key(ctx, provider, state))
    if raw is None:
        return None
    flow: dict[str, Any] = json.loads(raw)
    if flow["tenant_id"] != str(ctx.tenant_id) or flow["user_id"] != str(ctx.user_id):
        return None  # another session
    return flow


async def _connect(
    ctx: RequestContext,
    request: Request,
    flow: dict[str, Any],
    provider: OAuthProvider,
    credentials: dict[str, Any],
) -> bool:
    """Stores the tokens, if the person still manages the source."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        try:
            source = await _source(session, await _scope(session, ctx), str(flow["source"]))
        except ApiError:
            return False
        if source.kind != OAUTH_KIND[provider]:
            return False
        source.credentials = _secrets(request).seal(source.id, credentials)
        await _audit(session, ctx, "admin.source.connected", source)
    return True


def _redirect(ctx: RequestContext, back: str | None, **query: str) -> RedirectResponse:
    return RedirectResponse(f"{_back_page(ctx, back)}?{urlencode(query)}", status_code=303)


@router.get("/{key}/oauth/google/start")
async def google_start(
    ctx: ManagerContext, request: Request, key: str, back: Back = "admin"
) -> RedirectResponse:
    settings = settings_of(request)
    if not settings.google_client_id:
        raise ApiError(
            409, "oauth_not_configured", "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first"
        )
    state, verifier = await _start_flow(ctx, request, key, "google", back)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
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
    ctx: ManagerContext, request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    settings, resources = settings_of(request), resources_of(request)
    flow = await _finish_flow(ctx, request, "google", state) if state else None
    if error or not code or flow is None:
        return _redirect(ctx, flow["back"] if flow else None, oauth="failed", provider="google")
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
        return _redirect(ctx, flow["back"], oauth="failed", provider="google")
    token = response.json()
    stored = await _connect(
        ctx,
        request,
        flow,
        "google",
        {
            "refresh_token": token.get("refresh_token", ""),
            "access_token": token.get("access_token", ""),
        },
    )
    if not stored:
        return _redirect(ctx, flow["back"], oauth="failed", provider="google")
    return _redirect(
        ctx, flow["back"], oauth="connected", provider="google", source=str(flow["source"])
    )


@router.get("/{key}/oauth/slack/start")
async def slack_start(
    ctx: ManagerContext, request: Request, key: str, back: Back = "admin"
) -> RedirectResponse:
    """Slack's consent screen for the workspace: the bot token it grants (read-only channel
    history) is stored encrypted, like a pasted one."""
    settings = settings_of(request)
    if not (settings.slack_client_id and settings.slack_client_secret):
        raise ApiError(
            409, "oauth_not_configured", "Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET first"
        )
    state, _ = await _start_flow(ctx, request, key, "slack", back)
    query = urlencode(
        {
            "client_id": settings.slack_client_id,
            "scope": SLACK_SCOPES,
            "redirect_uri": f"{settings.public_url}/api/v1/oauth/slack/callback",
            "state": state,
        }
    )
    return RedirectResponse(f"{SLACK_AUTH}?{query}", status_code=302)


@oauth_router.get("/slack/callback")
async def slack_callback(
    ctx: ManagerContext, request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    settings, resources = settings_of(request), resources_of(request)
    flow = await _finish_flow(ctx, request, "slack", state) if state else None
    if error or not code or flow is None:
        return _redirect(ctx, flow["back"] if flow else None, oauth="failed", provider="slack")
    response = await resources.http.post(
        SLACK_TOKEN,
        data={
            "code": code,
            "client_id": settings.slack_client_id or "",
            "client_secret": secret(settings.slack_client_secret) or "",
            "redirect_uri": f"{settings.public_url}/api/v1/oauth/slack/callback",
        },
        timeout=20,
    )
    try:
        body: dict[str, Any] = response.json()
    except ValueError:
        body = {}
    token = body.get("access_token")
    if response.is_error or not body.get("ok") or not isinstance(token, str) or not token:
        return _redirect(ctx, flow["back"], oauth="failed", provider="slack")
    if not await _connect(ctx, request, flow, "slack", {"bot_token": token}):
        return _redirect(ctx, flow["back"], oauth="failed", provider="slack")
    return _redirect(
        ctx, flow["back"], oauth="connected", provider="slack", source=str(flow["source"])
    )


# --- Confluence (Atlassian 3LO) and SharePoint/OneDrive (Microsoft, code + PKCE) -------------


def _pkce_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


@router.get("/{key}/oauth/atlassian/start")
async def atlassian_start(
    ctx: ManagerContext, request: Request, key: str, back: Back = "admin"
) -> RedirectResponse:
    """Connects a Confluence source: redirects to Atlassian's consent screen."""
    settings = settings_of(request)
    await _oauth_source(ctx, request, key, "atlassian")
    app = OAuthApps.from_settings(settings).atlassian
    if not app.configured:
        raise ApiError(409, "oauth_not_configured", f"Set {OAUTH_SETTINGS['atlassian']} first")
    state, _ = await _start_flow(ctx, request, key, "atlassian", back)
    query = {
        "audience": "api.atlassian.com",
        "client_id": app.client_id or "",
        "scope": " ".join(oauth.ATLASSIAN_SCOPES),
        "redirect_uri": _redirect_uri(settings.public_url, "atlassian"),
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return RedirectResponse(f"{oauth.ATLASSIAN_AUTHORIZE}?{urlencode(query)}", status_code=302)


@router.get("/{key}/oauth/microsoft/start")
async def microsoft_start(
    ctx: ManagerContext, request: Request, key: str, back: Back = "admin"
) -> RedirectResponse:
    """Connects a SharePoint/OneDrive source: redirects to Microsoft's sign-in. It asks only
    for the permission the source needs."""
    settings = settings_of(request)
    config = await _oauth_source(ctx, request, key, "microsoft")
    app = OAuthApps.from_settings(settings).microsoft
    if not app.configured:
        raise ApiError(409, "oauth_not_configured", f"Set {OAUTH_SETTINGS['microsoft']} first")
    scope = " ".join(oauth.microsoft_scopes(config))
    state, verifier = await _start_flow(ctx, request, key, "microsoft", back, scope=scope)
    query = {
        "client_id": app.client_id or "",
        "response_type": "code",
        "redirect_uri": _redirect_uri(settings.public_url, "microsoft"),
        "response_mode": "query",
        "scope": scope,
        "state": state,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    url = oauth.microsoft_authorize_url(app.tenant)
    return RedirectResponse(f"{url}?{urlencode(query)}", status_code=302)


async def _app_callback(
    ctx: RequestContext,
    request: Request,
    provider: Literal["atlassian", "microsoft"],
    code: str,
    state: str,
    error: str,
) -> RedirectResponse:
    settings, resources = settings_of(request), resources_of(request)
    flow = await _finish_flow(ctx, request, provider, state) if state else None
    if error or not code or flow is None:
        return _redirect(ctx, flow["back"] if flow else None, oauth="failed", provider=provider)
    try:
        config = await _oauth_source(ctx, request, str(flow["source"]), provider)
    except ApiError:
        return _redirect(ctx, flow["back"], oauth="failed", provider=provider)
    apps = OAuthApps.from_settings(settings)
    redirect_uri = _redirect_uri(settings.public_url, provider)
    try:
        if provider == "atlassian":
            credentials = await oauth.atlassian_connect(
                resources.http,
                apps.atlassian,
                code=code,
                redirect_uri=redirect_uri,
                site_url=str(config.get("site_url", "")),
            )
        else:
            credentials = await oauth.microsoft_connect(
                resources.http,
                apps.microsoft,
                code=code,
                redirect_uri=redirect_uri,
                verifier=str(flow["verifier"]),
                scope=str(flow.get("scope", "")),
            )
    except ConnectorError as exc:
        log.info("oauth_failed", provider=provider, source=flow["source"], reason=str(exc))
        return _redirect(ctx, flow["back"], oauth="failed", provider=provider)
    if not await _connect(ctx, request, flow, provider, credentials):
        return _redirect(ctx, flow["back"], oauth="failed", provider=provider)
    return _redirect(
        ctx, flow["back"], oauth="connected", provider=provider, source=str(flow["source"])
    )


@oauth_router.get("/atlassian/callback")
async def atlassian_callback(
    ctx: ManagerContext, request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    return await _app_callback(ctx, request, "atlassian", code, state, error)


@oauth_router.get("/microsoft/callback")
async def microsoft_callback(
    ctx: ManagerContext, request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    return await _app_callback(ctx, request, "microsoft", code, state, error)

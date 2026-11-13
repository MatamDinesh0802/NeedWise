"""Document retention (Enterprise feature "retention_policies"; docs/enterprise.md).

Per collection, an org owner or admin sets how many days (30 to 36,500) a document may go without
an update before it is deleted or archived. Updated means new content (a new version), a review
(Mark reviewed, or an approval of official status) or a restore from the archive, whichever is
latest; a document never updated counts from when it was added.

Never deleted or archived by a rule, and counted as kept with the reason:

- `legal_hold`: a legal hold covers it (workmind.modules.governance.holds);
- `official`: it is official, or waiting for an official-status approval. Approved content
  leaves through a demotion (by an editor, or an overdue review), never through a timer;
- with `delete` only, `meeting_transcript` (it goes with its meeting) and `synced` (its
  source would bring it back at the next sync; deletion propagation removes it when the source
  does).

Archive keeps the document, its versions, files and chunks, and takes it out of search and
answers (`is_active` false in the index); an editor restores it from its page, and new content
brings it back. Delete does what an editor's delete does: the rows, the search entries and the
files nothing else uses. The preview and the run select with the same query, so their counts
match. Each document deleted or archived is audited, and so is each run.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workmind.api.deps import resources_of
from workmind.api.v1.admin import AdminContext, PersonRef
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance import holds
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.governance.models import (
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    DocumentRetentionRule,
)
from workmind.modules.identity.models import User
from workmind.modules.knowledge import service
from workmind.modules.knowledge.governance import OFFICIAL_KIND
from workmind.modules.knowledge.models import Collection, Document, DocumentVersion
from workmind.modules.knowledge.pipeline import delete_document_points, set_document_labels
from workmind.modules.knowledge.storage import ObjectStorage, owned_key
from workmind.modules.meetings.models import Meeting
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.modules.work.models import Approval

log = structlog.get_logger("workmind_ee.document_retention")

Action = Literal["delete", "archive"]
MAX_RULES: Final = 1000


class RuleIn(BaseModel):
    collection_id: uuid.UUID
    days: int = Field(ge=MIN_RETENTION_DAYS, le=MAX_RETENTION_DAYS)
    action: Action


class RulesIn(BaseModel):
    """Every rule of the organization: collections left out keep their documents forever."""

    rules: list[RuleIn] = Field(max_length=MAX_RULES)

    @model_validator(mode="after")
    def one_rule_per_collection(self) -> "RulesIn":
        ids = [r.collection_id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("one rule per collection")
        return self


class RuleOut(RuleIn):
    collection: str
    updated_at: datetime
    updated_by: PersonRef | None
    last_run_at: datetime | None
    last_run: dict[str, Any]


class RulesOut(BaseModel):
    minimum_days: int = MIN_RETENTION_DAYS
    rules: list[RuleOut]


class Kept(BaseModel):
    """Older than the rule allows, but not deleted or archived, by reason."""

    legal_hold: int = 0
    official: int = 0
    meeting_transcript: int = 0
    synced: int = 0


class CollectionOutcome(BaseModel):
    collection_id: uuid.UUID
    collection: str
    days: int
    action: Action
    # Documents not updated since this are deleted or archived.
    cutoff: datetime
    count: int  # deleted or archived (preview: would be)
    kept: Kept


class PreviewOut(BaseModel):
    as_of: datetime
    collections: list[CollectionOutcome]
    would_delete: int
    would_archive: int
    kept: Kept


class RunOut(BaseModel):
    as_of: datetime
    collections: list[CollectionOutcome]
    deleted: int
    archived: int
    kept: Kept
    files_deleted: int


class ConfirmIn(BaseModel):
    confirm: Literal["delete"] = Field(description='"delete": deleting cannot be undone')


# --- The selection -------------------------------------------------------------------------------


def last_updated() -> Any:
    """New content, a review or a restore, whichever is latest; else when it was added.
    (GREATEST ignores NULLs in PostgreSQL.)"""
    latest_version = (
        select(func.max(DocumentVersion.created_at))
        .where(DocumentVersion.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    return func.greatest(
        func.coalesce(latest_version, Document.created_at),
        Document.reviewed_at,
        Document.restored_at,
    )


@dataclass(frozen=True)
class Rule:
    collection_id: uuid.UUID
    days: int
    action: Action

    def cutoff(self, now: datetime) -> datetime:
        return now - timedelta(days=self.days)


@dataclass
class Selection:
    ids: list[uuid.UUID] = field(default_factory=list[uuid.UUID])
    kept: Kept = field(default_factory=Kept)


async def select_documents(session: AsyncSession, rule: Rule, now: datetime) -> Selection:
    """What `rule` deletes or archives at `now`, and what it keeps and why. The preview and the
    run both call this, so they agree."""
    transcripts = select(Meeting.document_id).where(Meeting.document_id.is_not(None))
    pending_official = select(Approval.origin["document_id"].astext).where(
        Approval.tool == OFFICIAL_KIND, Approval.status == "pending"
    )
    query = select(
        Document.id,
        holds.document_held().label("held"),
        or_(Document.trust == "official", cast(Document.id, Text).in_(pending_official)).label(
            "official"
        ),
        Document.id.in_(transcripts).label("transcript"),
        Document.source_id.is_not(None).label("synced"),
    ).where(Document.collection_id == rule.collection_id, last_updated() < rule.cutoff(now))
    if rule.action == "archive":
        query = query.where(Document.archived_at.is_(None))
    selection = Selection()
    for row in await session.execute(query.order_by(Document.created_at, Document.id)):
        if row.held:
            selection.kept.legal_hold += 1
        elif row.official:
            selection.kept.official += 1
        elif rule.action == "delete" and row.transcript:
            selection.kept.meeting_transcript += 1
        elif rule.action == "delete" and row.synced:
            selection.kept.synced += 1
        else:
            selection.ids.append(row.id)
    return selection


def _add(total: Kept, more: Kept) -> None:
    for name in Kept.model_fields:
        setattr(total, name, getattr(total, name) + getattr(more, name))


async def _names(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(
        select(Collection.id, Collection.name).where(Collection.id.in_(ids))
    )
    return {collection_id: name for collection_id, name in rows.all()}


def _rule(row: DocumentRetentionRule | RuleIn) -> Rule:
    action: Action = "delete" if row.action == "delete" else "archive"
    return Rule(row.collection_id, row.days, action)


async def preview_rules(session: AsyncSession, rules: list[Rule], now: datetime) -> PreviewOut:
    names = await _names(session, [r.collection_id for r in rules])
    outcomes: list[CollectionOutcome] = []
    kept = Kept()
    for rule in sorted(rules, key=lambda r: names.get(r.collection_id, "")):
        selection = await select_documents(session, rule, now)
        _add(kept, selection.kept)
        outcomes.append(
            CollectionOutcome(
                collection_id=rule.collection_id,
                collection=names.get(rule.collection_id, ""),
                days=rule.days,
                action=rule.action,
                cutoff=rule.cutoff(now),
                count=len(selection.ids),
                kept=selection.kept,
            )
        )
    return PreviewOut(
        as_of=now,
        collections=outcomes,
        would_delete=sum(o.count for o in outcomes if o.action == "delete"),
        would_archive=sum(o.count for o in outcomes if o.action == "archive"),
        kept=kept,
    )


def _context(tenant_id: uuid.UUID, actor: Actor) -> RequestContext:
    """The context deletions run in. Who acted is passed to them as `actor` (the system, or the
    admin who ran it); this only carries the tenant."""
    return RequestContext(
        request_id="retention",
        tenant_id=tenant_id,
        user_id=actor.id or uuid.UUID(int=0),
        org_role="owner",
        principals=frozenset(),
        auth_method="service",
    )


async def apply_rules(
    sessions: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    qdrant: QdrantClient,
    collection_name: str,
    tenant_id: uuid.UUID,
    *,
    actor: Actor,
    trigger: Literal["manual", "schedule"],
    now: datetime | None = None,
) -> tuple[RunOut, bool]:
    """Deletes or archives what the tenant's rules select, records each rule's run and the run
    in the audit log, then updates the search index and deletes the freed files. Returns (result,
    whether any rule exists)."""
    now = now or datetime.now(UTC)
    deleted: list[uuid.UUID] = []
    archived: list[uuid.UUID] = []
    orphaned: list[str] = []
    async with tenant_transaction(sessions, tenant_id) as session:
        rows = list((await session.scalars(select(DocumentRetentionRule))).all())
        if not rows:
            empty = RunOut(
                as_of=now, collections=[], deleted=0, archived=0, kept=Kept(), files_deleted=0
            )
            return empty, False
        ctx = _context(tenant_id, actor)
        names = await _names(session, [r.collection_id for r in rows])
        outcomes: list[CollectionOutcome] = []
        kept = Kept()
        for row in sorted(rows, key=lambda r: names.get(r.collection_id, "")):
            rule = _rule(row)
            selection = await select_documents(session, rule, now)
            _add(kept, selection.kept)
            for document_id in selection.ids:
                document = await session.get(Document, document_id)
                if document is None:
                    continue
                if rule.action == "delete":
                    orphaned += await service.delete_document(
                        session, ctx, document, actor=actor, reason="retention"
                    )
                    deleted.append(document_id)
                else:
                    document.archived_at = now
                    await record_event(
                        session,
                        tenant_id=tenant_id,
                        actor=actor,
                        action="knowledge.document.archived",
                        resource_type="document",
                        resource_id=str(document_id),
                        details={
                            "collection_id": str(rule.collection_id),
                            "reason": "retention",
                            "days": rule.days,
                            "trigger": trigger,
                        },
                    )
                    archived.append(document_id)
            outcome = CollectionOutcome(
                collection_id=rule.collection_id,
                collection=names.get(rule.collection_id, ""),
                days=rule.days,
                action=rule.action,
                cutoff=rule.cutoff(now),
                count=len(selection.ids),
                kept=selection.kept,
            )
            outcomes.append(outcome)
            row.last_run_at = now
            row.last_run = {
                "trigger": trigger,
                "action": rule.action,
                "count": outcome.count,
                "kept": selection.kept.model_dump(),
            }
        result = RunOut(
            as_of=now,
            collections=outcomes,
            deleted=len(deleted),
            archived=len(archived),
            kept=kept,
            files_deleted=0,
        )
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action="admin.retention.documents_run",
            resource_type="tenant",
            resource_id=str(tenant_id),
            details={
                "trigger": trigger,
                "deleted": result.deleted,
                "archived": result.archived,
                "kept": kept.model_dump(),
                "collections": [
                    {
                        "collection_id": str(o.collection_id),
                        "days": o.days,
                        "action": o.action,
                        "count": o.count,
                        "kept": o.kept.model_dump(),
                    }
                    for o in outcomes
                ],
            },
        )
    # After the commit: the rows no longer point at these entries and files.
    for document_id in deleted:
        await delete_document_points(qdrant, collection_name, tenant_id, document_id)
    for document_id in archived:
        await set_document_labels(
            qdrant, collection_name, tenant_id, document_id, {"is_active": False}
        )
    for key in sorted(set(orphaned)):
        try:
            await storage.delete(owned_key(tenant_id, key))
            result.files_deleted += 1
        except OSError as exc:
            log.warning("retention_file_not_deleted", key=key, error=type(exc).__name__)
    return result, True


# --- HTTP (owners and admins; mounted behind sign-in and the license) ----------------------------

router = APIRouter(prefix="/admin/retention/documents", tags=["enterprise"])


async def _rules_out(session: AsyncSession) -> RulesOut:
    rows = list((await session.scalars(select(DocumentRetentionRule))).all())
    names = await _names(session, [r.collection_id for r in rows])
    people = {
        u.id: u
        for u in await session.scalars(
            select(User).where(User.id.in_([r.updated_by for r in rows if r.updated_by]))
        )
    }
    return RulesOut(
        rules=[
            RuleOut(
                collection_id=r.collection_id,
                collection=names.get(r.collection_id, ""),
                days=r.days,
                action=_rule(r).action,
                updated_at=r.updated_at,
                updated_by=PersonRef(id=u.id, display_name=u.display_name)
                if r.updated_by and (u := people.get(r.updated_by))
                else None,
                last_run_at=r.last_run_at,
                last_run=r.last_run,
            )
            for r in sorted(rows, key=lambda r: names.get(r.collection_id, ""))
        ]
    )


async def _check_collections(session: AsyncSession, body: RulesIn) -> None:
    wanted = {r.collection_id for r in body.rules}
    found = set(await _names(session, list(wanted)))
    if wanted - found:
        raise ApiError(422, "unknown_collection", "Unknown collection", str(min(wanted - found)))


@router.get("")
async def get_rules(ctx: AdminContext, request: Request) -> RulesOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        return await _rules_out(session)


@router.put("")
async def put_rules(ctx: AdminContext, request: Request, body: RulesIn) -> RulesOut:
    """Replaces every rule (audited as admin.retention.documents_updated, before and after)."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await _check_collections(session, body)
        existing = {
            r.collection_id: r for r in await session.scalars(select(DocumentRetentionRule))
        }
        before = [
            {"collection_id": str(r.collection_id), "days": r.days, "action": r.action}
            for r in existing.values()
        ]
        wanted = {r.collection_id: r for r in body.rules}
        for collection_id, row in existing.items():
            if collection_id not in wanted:
                await session.delete(row)
        for collection_id, rule in wanted.items():
            row = existing.get(collection_id)
            if row is None:
                session.add(
                    DocumentRetentionRule(
                        tenant_id=ctx.tenant_id,
                        collection_id=collection_id,
                        days=rule.days,
                        action=rule.action,
                        updated_by=ctx.user_id,
                    )
                )
            elif (row.days, row.action) != (rule.days, rule.action):
                row.days, row.action, row.updated_by = rule.days, rule.action, ctx.user_id
        await session.flush()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.retention.documents_updated",
            resource_type="tenant",
            resource_id=str(ctx.tenant_id),
            details={
                "before": before,
                "after": [
                    {"collection_id": str(r.collection_id), "days": r.days, "action": r.action}
                    for r in body.rules
                ],
            },
        )
        session.expire_all()
        return await _rules_out(session)


@router.post("/preview")
async def preview(ctx: AdminContext, request: Request, body: RulesIn | None = None) -> PreviewOut:
    """What a run would delete and archive now, per collection, and what it would keep and
    why. Changes nothing. Without a body: the saved rules; with one: that draft."""
    now = datetime.now(UTC)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        if body is None:
            rules = [_rule(r) for r in await session.scalars(select(DocumentRetentionRule))]
        else:
            await _check_collections(session, body)
            rules = [_rule(r) for r in body.rules]
        return await preview_rules(session, rules, now)


@router.post("/run")
async def run(ctx: AdminContext, request: Request, body: ConfirmIn) -> RunOut:
    """Applies the saved rules now (the daily task does the same). Deleting cannot be undone;
    audited."""
    del body  # its only field is the confirmation
    resources = resources_of(request)
    result, had_rules = await apply_rules(
        resources.db,
        resources.storage,
        resources.qdrant,
        resources.collection_name,
        ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        trigger="manual",
    )
    if not had_rules:
        raise ApiError(409, "no_rules", "Set a document retention rule first")
    return result

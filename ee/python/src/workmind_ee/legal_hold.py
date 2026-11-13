"""Legal hold (Enterprise feature "legal_hold"; docs/enterprise.md#legal-hold).

Org owners and admins place a hold on a document, a collection, a person (their conversations,
the meetings they uploaded and their Insights questions), a meeting or an Insights question,
naming the matter and saying why. While a hold is active nothing it covers is deleted: not by
the people who can otherwise delete it, not by a connector's deletion propagation, not by a
retention run, and not by the tenant's deletion without an explicit override. The core enforces
that on every deletion path (workmind.modules.governance.holds), whatever the license says, so
a license that lapses never quietly releases a hold. Releasing one says why.

Placing and releasing are audited (`legal_hold.placed`, `legal_hold.released`), and so is every
deletion a hold refused (`legal_hold.deletion_blocked`). The other owners, admins and auditors
are notified when a hold is placed or released (category "legal_holds", so it also goes by email
or Slack to those who chose that); the notification names the matter and the kind of thing held,
never its title. The people whose data is held are not told. Owners, admins and auditors can
read the holds; what a hold names is described without content the reader cannot open (separation of
duties, ARCHITECTURE 7.1): no conversation or Insights question text, and no titles from
collections they cannot open.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from workmind.api.deps import resources_of
from workmind.api.v1.admin import AdminContext, AuditContext, PersonRef
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.analytics.models import AnalyticsRun
from workmind.modules.conversations.models import Conversation
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.governance.holds import HoldTarget
from workmind.modules.governance.models import LegalHold
from workmind.modules.identity.models import User
from workmind.modules.knowledge.governance import unreadable_collections, unreadable_documents
from workmind.modules.knowledge.models import Collection, Document
from workmind.modules.meetings.models import Meeting
from workmind.modules.work.links import legal_holds_link
from workmind.modules.work.notifications import notify

FEATURE: Final = "legal_hold"
LIST_LIMIT: Final = 500
# Who is told when a hold is placed or released: the people who may read holds.
TOLD_ROLES: Final = ("owner", "admin", "auditor")
# What a hold names, in a notification: the kind of thing, never its title or text.
HELD: Final[dict[str, str]] = {
    "document": "a document",
    "collection": "a collection",
    "user": "a person's conversations, uploaded meetings and Insights questions",
    "meeting": "a meeting",
    "analytics_run": "an Insights question",
}

Matter = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class HoldIn(BaseModel):
    matter: Matter = Field(description="the case or investigation, e.g. Smith v. Acme")
    reason: Reason
    target_type: HoldTarget
    target_id: uuid.UUID


class ReleaseIn(BaseModel):
    reason: Reason


class Covers(BaseModel):
    """What the hold keeps now (it also keeps what is added later: a collection's new documents,
    a person's new conversations)."""

    documents: int = 0
    conversations: int = 0
    meetings: int = 0
    analytics_runs: int = 0


class TargetOut(BaseModel):
    type: HoldTarget
    id: uuid.UUID
    # What it is, in words the reader may see: no conversation or question text, and no title
    # from a collection the reader cannot open.
    label: str
    exists: bool


class HoldOut(BaseModel):
    id: uuid.UUID
    matter: str
    reason: str
    target: TargetOut
    covers: Covers
    placed_by: PersonRef | None
    placed_at: datetime
    active: bool
    released_at: datetime | None
    released_by: PersonRef | None
    release_reason: str | None


class HoldsOut(BaseModel):
    items: list[HoldOut]


# --- What a hold names and covers ---------------------------------------------------------------


async def _tell(
    session: AsyncSession, ctx: RequestContext, hold: LegalHold, *, kind: str, title: str
) -> None:
    """Notifies the other active owners, admins and auditors (not the person who acted)."""
    actor = await session.get(User, ctx.user_id)
    who = actor.display_name if actor is not None else "Someone"
    what = HELD.get(hold.target_type, "something")
    verb = "placed" if kind == "legal_hold.placed" else "released"
    people = await session.scalars(
        select(User.id).where(
            User.org_role.in_(TOLD_ROLES), User.status == "active", User.id != ctx.user_id
        )
    )
    for user_id in people.all():
        await notify(
            session,
            ctx.tenant_id,
            user_id,
            kind=kind,
            title=title,
            body=f"{who} {verb} the hold for “{hold.matter[:200]}” on {what}.",
            link=legal_holds_link(),
            thing=f"legal_hold:{hold.id}",
        )


async def _count(session: AsyncSession, model: type[object], *where: object) -> int:
    query = select(func.count()).select_from(model).where(*where)  # type: ignore[arg-type]
    return int(await session.scalar(query) or 0)


async def _target(
    session: AsyncSession, ctx: RequestContext, target_type: str, target_id: uuid.UUID
) -> TargetOut:
    kind: HoldTarget = target_type  # type: ignore[assignment]  (checked by the database)

    async def in_collection(
        collection_id: uuid.UUID, title: str, noun: str, document_id: uuid.UUID | None = None
    ) -> str:
        collection = await session.get(Collection, collection_id)
        name = collection.name if collection else "a collection"
        # A document is unreadable when its collection is closed to the caller or its own
        # access list leaves them out (ADR 0012): its title is withheld from them either way.
        closed = (
            await unreadable_documents(session, ctx, {document_id})
            if document_id is not None
            else await unreadable_collections(session, ctx, {collection_id})
        )
        if closed:
            return f"A {noun} in “{name}” you can't open"
        return f"{title} ({name})"

    label: str | None = None
    if kind == "document":
        document = await session.get(Document, target_id)
        if document is not None:
            label = await in_collection(
                document.collection_id, document.title, "document", document.id
            )
    elif kind == "collection":
        collection = await session.get(Collection, target_id)
        label = f"Collection “{collection.name}”" if collection else None
    elif kind == "user":
        user = await session.get(User, target_id)
        label = f"{user.display_name} ({user.email})" if user else None
    elif kind == "meeting":
        meeting = await session.get(Meeting, target_id)
        if meeting is not None:
            label = await in_collection(meeting.collection_id, meeting.title, "meeting")
    else:
        run = await session.get(AnalyticsRun, target_id)
        if run is not None:
            asker = await session.get(User, run.user_id)
            who = asker.display_name if asker else "someone"
            label = f"An Insights question by {who}, {run.created_at.date().isoformat()}"
    return TargetOut(
        type=kind, id=target_id, label=label or "No longer exists", exists=label is not None
    )


async def _covers(session: AsyncSession, hold: LegalHold) -> Covers:
    kind, target = hold.target_type, hold.target_id
    if kind == "document":
        return Covers(
            documents=await _count(session, Document, Document.id == target),
            meetings=await _count(session, Meeting, Meeting.document_id == target),
        )
    if kind == "collection":
        return Covers(
            documents=await _count(session, Document, Document.collection_id == target),
            meetings=await _count(session, Meeting, Meeting.collection_id == target),
        )
    if kind == "user":
        return Covers(
            conversations=await _count(session, Conversation, Conversation.user_id == target),
            meetings=await _count(session, Meeting, Meeting.owner_id == target),
            # Their meetings' transcripts.
            documents=await _count(
                session,
                Document,
                Document.id.in_(
                    select(Meeting.document_id).where(
                        Meeting.owner_id == target, Meeting.document_id.is_not(None)
                    )
                ),
            ),
            analytics_runs=await _count(session, AnalyticsRun, AnalyticsRun.user_id == target),
        )
    if kind == "meeting":
        meeting = await session.get(Meeting, target)
        return Covers(
            meetings=1 if meeting else 0,
            documents=1 if meeting and meeting.document_id else 0,
        )
    return Covers(analytics_runs=await _count(session, AnalyticsRun, AnalyticsRun.id == target))


async def _out(session: AsyncSession, ctx: RequestContext, hold: LegalHold) -> HoldOut:
    people = {
        u.id: u
        for u in await session.scalars(
            select(User).where(User.id.in_([i for i in (hold.placed_by, hold.released_by) if i]))
        )
    }

    def person(user_id: uuid.UUID | None) -> PersonRef | None:
        user = people.get(user_id) if user_id else None
        return PersonRef(id=user.id, display_name=user.display_name) if user else None

    return HoldOut(
        id=hold.id,
        matter=hold.matter,
        reason=hold.reason,
        target=await _target(session, ctx, hold.target_type, hold.target_id),
        covers=await _covers(session, hold),
        placed_by=person(hold.placed_by),
        placed_at=hold.created_at,
        active=hold.released_at is None,
        released_at=hold.released_at,
        released_by=person(hold.released_by),
        release_reason=hold.release_reason,
    )


# --- HTTP ----------------------------------------------------------------------------------------

router = APIRouter(prefix="/admin/legal-holds", tags=["enterprise"])


@router.get("")
async def list_holds(
    ctx: AuditContext,
    request: Request,
    status: Annotated[Literal["active", "released", "all"], Query()] = "active",
) -> HoldsOut:
    """Owners, admins and auditors: the holds, newest first (at most 500)."""
    query = select(LegalHold)
    if status == "active":
        query = query.where(LegalHold.released_at.is_(None))
    elif status == "released":
        query = query.where(LegalHold.released_at.is_not(None))
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        found = (
            await session.scalars(query.order_by(LegalHold.created_at.desc()).limit(LIST_LIMIT))
        ).all()
        return HoldsOut(items=[await _out(session, ctx, h) for h in found])


@router.post("", status_code=201)
async def place_hold(ctx: AdminContext, request: Request, body: HoldIn) -> HoldOut:
    """Owners and admins place a hold. What it names must exist in this organization; the same
    matter cannot hold the same thing twice."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        target = await _target(session, ctx, body.target_type, body.target_id)
        if not target.exists:
            raise ApiError(
                422, "unknown_target", f"No {body.target_type.replace('_', ' ')} with this id"
            )
        duplicate = await session.scalar(
            select(LegalHold.id).where(
                LegalHold.released_at.is_(None),
                LegalHold.target_type == body.target_type,
                LegalHold.target_id == body.target_id,
                func.lower(LegalHold.matter) == body.matter.lower(),
            )
        )
        if duplicate is not None:
            raise ApiError(409, "already_held", "This matter already holds it")
        hold = LegalHold(
            tenant_id=ctx.tenant_id,
            matter=body.matter,
            reason=body.reason,
            target_type=body.target_type,
            target_id=body.target_id,
            placed_by=ctx.user_id,
        )
        session.add(hold)
        await session.flush()
        await session.refresh(hold)
        out = await _out(session, ctx, hold)
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="legal_hold.placed",
            resource_type="legal_hold",
            resource_id=str(hold.id),
            details={
                "matter": hold.matter,
                "reason": hold.reason,
                "target_type": hold.target_type,
                "target_id": str(hold.target_id),
                "covers": out.covers.model_dump(),
            },
        )
        await _tell(
            session, ctx, hold, kind="legal_hold.placed", title=f"Legal hold placed: {hold.matter}"
        )
        return out


@router.post("/{hold_id}/release")
async def release_hold(
    ctx: AdminContext, request: Request, hold_id: uuid.UUID, body: ReleaseIn
) -> HoldOut:
    """Owners and admins release a hold, saying why. What it covered can be deleted again
    (unless another hold covers it); the hold stays listed as released."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        hold = await session.get(LegalHold, hold_id, with_for_update=True)
        if hold is None:
            raise ApiError(404, "not_found", "Legal hold not found")
        if hold.released_at is not None:
            raise ApiError(409, "already_released", "This hold was already released")
        now = datetime.now(UTC)
        hold.released_at, hold.released_by, hold.release_reason = now, ctx.user_id, body.reason
        await session.flush()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="legal_hold.released",
            resource_type="legal_hold",
            resource_id=str(hold.id),
            details={
                "matter": hold.matter,
                "target_type": hold.target_type,
                "target_id": str(hold.target_id),
                "reason": body.reason,
                "placed_at": hold.created_at.isoformat(),
            },
        )
        await _tell(
            session,
            ctx,
            hold,
            kind="legal_hold.released",
            title=f"Legal hold released: {hold.matter}",
        )
        return await _out(session, ctx, hold)

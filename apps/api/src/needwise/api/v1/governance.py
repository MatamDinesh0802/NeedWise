"""Knowledge governance overview (docs/governance.md): what needs attention.

Org owners and admins see every collection; anyone else sees the collections they own (an
empty overview if they own none). Separation of duties (ARCHITECTURE 7.1): an admin who cannot
open a collection sees that its documents need attention, never their titles. The same holds
for a document whose own access list leaves the caller out (ADR 0012).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from needwise.api.deps import require_scope, resources_of
from needwise.api.v1.admin import PersonRef
from needwise.core.context import RequestContext
from needwise.core.tenancy import tenant_transaction
from needwise.modules.identity.models import User
from needwise.modules.knowledge import governance
from needwise.modules.knowledge.models import Document

router = APIRouter(prefix="/governance", tags=["governance"])
ReadContext = Annotated[RequestContext, require_scope("knowledge:read")]


class CollectionRef(BaseModel):
    id: uuid.UUID
    name: str
    can_view: bool
    owner_id: uuid.UUID | None
    review_interval_days: int | None
    review_escalation_days: int
    review_auto_demote_days: int | None


class GovernedDocument(BaseModel):
    id: uuid.UUID
    # None when the caller cannot open the collection (an admin, for a restricted one).
    title: str | None
    collection_id: uuid.UUID
    trust: str
    owner: PersonRef | None
    owner_inactive: bool  # the owner left (disabled): someone else should take it over
    reviewed_at: datetime | None
    reviewed_by: PersonRef | None
    next_review_at: datetime | None
    # How far an overdue official review escalated this cycle, and when that stage was reached.
    review_escalation: str | None
    review_escalated_at: datetime | None


class PendingOfficial(BaseModel):
    document: GovernedDocument
    requested_by: PersonRef | None
    requested_at: datetime
    note: str | None  # None, like the title, when the caller cannot open the collection
    approvers: list[PersonRef]


class Demotion(BaseModel):
    document_id: uuid.UUID
    title: str | None
    collection_id: uuid.UUID
    by: PersonRef | None  # None: the system (an automatic demotion)
    at: datetime
    to: str
    note: str | None
    # Why, when it was not an editor's choice: new_version or review_overdue.
    reason: str | None = None


class OverviewOut(BaseModel):
    scope: Literal["all", "owned"]
    collections: list[CollectionRef]
    overdue: list[GovernedDocument]
    due_soon: list[GovernedDocument]
    without_owner: list[GovernedDocument]
    pending_official: list[PendingOfficial]
    recent_demotions: list[Demotion]


def _person(people: dict[uuid.UUID, User], user_id: uuid.UUID | None) -> PersonRef | None:
    user = people.get(user_id) if user_id else None
    return PersonRef(id=user.id, display_name=user.display_name) if user else None


@router.get("/overview")
async def get_overview(ctx: ReadContext, request: Request) -> OverviewOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        found = await governance.overview(session, ctx)
    scope, people = found.scope, found.people

    def readable(collection_id: uuid.UUID, document_id: uuid.UUID) -> bool:
        return collection_id in scope.viewable and document_id not in found.hidden

    def document_out(document: Document) -> GovernedDocument:
        owner = people.get(document.owner_id) if document.owner_id else None
        return GovernedDocument(
            id=document.id,
            title=document.title if readable(document.collection_id, document.id) else None,
            collection_id=document.collection_id,
            trust=document.trust,
            owner=_person(people, document.owner_id),
            owner_inactive=owner is not None and owner.status != "active",
            reviewed_at=document.reviewed_at,
            reviewed_by=_person(people, document.reviewed_by),
            next_review_at=document.next_review_at,
            review_escalation=document.review_escalation,
            review_escalated_at=document.review_escalated_at,
        )

    demotions: list[Demotion] = []
    for event in found.demotions:
        collection_id = uuid.UUID(str(event.details["collection_id"]))
        document_id = uuid.UUID(str(event.resource_id))
        shown = readable(collection_id, document_id)
        demotions.append(
            Demotion(
                document_id=document_id,
                title=str(event.details.get("title") or "") if shown else None,
                collection_id=collection_id,
                by=_person(people, event.actor_id),
                at=event.occurred_at,
                to=str(event.details.get("after") or ""),
                note=event.details.get("note") if shown else None,
                reason=event.details.get("reason"),
            )
        )
    return OverviewOut(
        scope="all" if scope.everything else "owned",
        collections=[
            CollectionRef(
                id=c.id,
                name=c.name,
                can_view=c.id in scope.viewable,
                owner_id=c.owner_id,
                review_interval_days=c.review_interval_days,
                review_escalation_days=c.review_escalation_days,
                review_auto_demote_days=c.review_auto_demote_days,
            )
            for c in scope.collections.values()
        ],
        overdue=[document_out(d) for d in found.overdue],
        due_soon=[document_out(d) for d in found.due_soon],
        without_owner=[document_out(d) for d in found.without_owner],
        pending_official=[
            PendingOfficial(
                document=document_out(found.pending_documents[document_id]),
                requested_by=_person(people, request.requested_by),
                requested_at=request.requested_at,
                note=request.note
                if readable(found.pending_documents[document_id].collection_id, document_id)
                else None,
                approvers=[p for a in request.approvers if (p := _person(people, a))],
            )
            for document_id, request in found.pending.items()
        ],
        recent_demotions=demotions,
    )

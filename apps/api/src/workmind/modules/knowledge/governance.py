"""Knowledge governance (docs/governance.md, ARCHITECTURE 7.3): document owners, review cycles
and the "official" approval flow.

- Every document has an owner: the person who answers for its content. By default the uploader,
  else the collection's owner. Collection editors and org admins can hand it to someone else.
- A collection may set a review interval. Each document is then due for review that many days
  after its last review. A daily task reminds the owner once per review cycle (a notification
  and a task); marking the document reviewed starts the next cycle and closes the task.
- Trust "official" is reached only through an approval: an editor asks, the collection's owner
  (or, when the owner is the one asking or there is none, the org admins who can open the
  collection) decides. Nobody ever approves their own request, and nobody approves what they
  cannot read. The request is pinned to the document's version: new content needs a new
  approval, and a new version of an official document is no longer official. Demoting an
  official document is immediate, audited, and the owner is told.

Permissions are checked here and in the API, never by a model.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.governance.models import AuditEvent
from workmind.modules.identity.models import User
from workmind.modules.identity.service import load_identity
from workmind.modules.knowledge.acl import acl_principals
from workmind.modules.knowledge.models import (
    Collection,
    CollectionGrant,
    Document,
    DocumentVersion,
)
from workmind.modules.work.approvals import ApprovalKind, propose
from workmind.modules.work.links import approval_link, document_link
from workmind.modules.work.models import Approval, Task
from workmind.modules.work.notifications import notify

# Approval.tool of an official-status request. Not a tool name: tools use snake_case, so no
# agent or workflow can ever propose one.
OFFICIAL_KIND: Final = "knowledge.official_status"
OFFICIAL_TTL: Final = timedelta(days=14)
REVIEW_TASK_DUE: Final = timedelta(days=14)
DUE_SOON: Final = timedelta(days=30)
RECENT: Final = timedelta(days=30)
ADMIN_ROLES: Final = ("owner", "admin")
OVERVIEW_LIMIT: Final = 200

# Called after a document's trust changed, so the search index follows at once.
TrustChanged = Callable[[uuid.UUID, uuid.UUID, str], Awaitable[None]]


def utcnow() -> datetime:
    return datetime.now(UTC)


def is_org_admin(ctx: RequestContext) -> bool:
    return ctx.has_role(*ADMIN_ROLES)


def next_review(after: datetime, interval_days: int | None) -> datetime | None:
    return after + timedelta(days=interval_days) if interval_days else None


def review_overdue(document: Document, now: datetime) -> bool:
    """Past its review date. The Sources panel shows this for official documents."""
    return document.next_review_at is not None and document.next_review_at < now


async def _grants(session: AsyncSession, collection_id: uuid.UUID) -> Sequence[CollectionGrant]:
    return (
        await session.scalars(
            select(CollectionGrant).where(CollectionGrant.collection_id == collection_id)
        )
    ).all()


async def can_view_as(
    session: AsyncSession,
    user_id: uuid.UUID,
    collection: Collection,
    grants: Sequence[CollectionGrant] | None = None,
) -> bool:
    """Whether another person (an owner to be) can open the collection, with their access now."""
    identity = await load_identity(session, user_id)
    if identity is None or identity.user.status != "active":
        return False
    if grants is None:
        grants = await _grants(session, collection.id)
    return bool(set(acl_principals(collection, grants)) & identity.principals)


# --- Owners ------------------------------------------------------------------------------------


async def default_owner(
    session: AsyncSession, uploader_id: uuid.UUID | None, collection: Collection
) -> uuid.UUID | None:
    """The uploader, else the collection's owner."""
    if uploader_id is not None and await session.get(User, uploader_id) is not None:
        return uploader_id
    return collection.owner_id


def start_review_cycle(document: Document, collection: Collection, now: datetime) -> None:
    """A new document is first due one interval after it was added."""
    document.next_review_at = next_review(now, collection.review_interval_days)


async def set_owner(
    session: AsyncSession,
    ctx: RequestContext,
    document: Document,
    collection: Collection,
    owner_id: uuid.UUID,
) -> User:
    """The caller must be a collection editor or an org admin (checked by the API)."""
    owner = await session.get(User, owner_id)
    if owner is None or owner.status != "active":
        raise ApiError(422, "unknown_user", "Choose an active colleague as the owner")
    if not await can_view_as(session, owner_id, collection):
        raise ApiError(
            422,
            "owner_cannot_view",
            "This person cannot open the collection",
            "An owner reviews the document, so they must be able to read it. Share the "
            "collection with them first.",
        )
    before = document.owner_id
    if before == owner_id:
        return owner
    document.owner_id = owner_id
    review_task = await _hand_over_review_task(session, document, owner_id)
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="knowledge.document.owner_changed",
        resource_type="document",
        resource_id=str(document.id),
        details={
            "before": str(before) if before else None,
            "after": str(owner_id),
            "collection_id": str(collection.id),
            "review_task": str(review_task) if review_task else None,
        },
    )
    if owner_id != ctx.user_id:
        await notify(
            session,
            ctx.tenant_id,
            owner_id,
            kind="document.owner",
            title=f"You now own “{document.title[:150]}”",
            body="You answer for its content and its reviews."
            + (" Its review is due: the review task is now yours." if review_task else ""),
            link=document_link(document.id),
        )
    return owner


async def _hand_over_review_task(
    session: AsyncSession, document: Document, owner_id: uuid.UUID
) -> uuid.UUID | None:
    """The reminder of the current review cycle follows the document to its new owner: an open
    task is reassigned (the former owner no longer answers for it). A cycle whose task was
    closed without a review is forgotten, so the next daily run reminds the new owner."""
    if document.review_task_id is None:
        return None
    task = await session.get(Task, document.review_task_id)
    if task is None or task.status not in ("open", "in_progress"):
        document.review_task_id = None
        return None
    task.assignee_id = owner_id
    return task.id


# --- Reviews -----------------------------------------------------------------------------------


async def reschedule_collection(session: AsyncSession, collection: Collection) -> int:
    """After the collection's review interval changed: every document's next review is one
    interval after its last review (or after it was added). No interval: no reviews due."""
    interval = collection.review_interval_days
    value = (
        func.coalesce(Document.reviewed_at, Document.created_at) + timedelta(days=interval)
        if interval
        else None
    )
    result = await session.execute(
        update(Document)
        .where(Document.collection_id == collection.id)
        .values(next_review_at=value)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def mark_reviewed(
    session: AsyncSession,
    ctx: RequestContext,
    document: Document,
    collection: Collection,
    *,
    note: str | None,
    now: datetime | None = None,
) -> None:
    """The owner or a collection editor confirms the content is still right (checked by the
    API). Starts the next cycle and closes this cycle's reminder task."""
    now = now or utcnow()
    document.reviewed_at, document.reviewed_by = now, ctx.user_id
    document.next_review_at = next_review(now, collection.review_interval_days)
    closed = await _close_review_task(session, document)
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="knowledge.document.reviewed",
        resource_type="document",
        resource_id=str(document.id),
        details={
            "collection_id": str(collection.id),
            "note": (note or "").strip()[:2000] or None,
            "next_review_at": document.next_review_at.isoformat()
            if document.next_review_at
            else None,
            "closed_task": str(closed) if closed else None,
        },
    )


async def _close_review_task(session: AsyncSession, document: Document) -> uuid.UUID | None:
    task_id = document.review_task_id
    document.review_task_id = None
    if task_id is None:
        return None
    task = await session.get(Task, task_id)
    if task is not None and task.status in ("open", "in_progress"):
        task.status = "done"
    return task_id


@dataclass
class ReminderReport:
    reminded: list[uuid.UUID] = field(default_factory=list[uuid.UUID])
    without_owner: int = 0


async def send_review_reminders(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> ReminderReport:
    """Daily (Celery beat). For each document past its review date, once per review cycle: a
    task for the owner ("Review <title>", due in 14 days) and a notification. Idempotent: the
    cycle's task is remembered on the document until it is reviewed, and rows are locked so
    two runs at once cannot both remind."""
    now = now or utcnow()
    report = ReminderReport()
    async with tenant_transaction(sessions, tenant_id) as session:
        due = (
            await session.scalars(
                select(Document)
                .where(
                    Document.next_review_at.is_not(None),
                    Document.next_review_at <= now,
                    Document.review_task_id.is_(None),
                )
                .order_by(Document.next_review_at)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for document in due:
            if document.owner_id is None:
                report.without_owner += 1  # listed in the governance overview instead
                continue
            owner = await session.get(User, document.owner_id)
            if owner is None or owner.status != "active":
                report.without_owner += 1
                continue
            assert document.next_review_at is not None  # noqa: S101 (filtered above)
            title = f"Review {document.title}"[:200]
            task = Task(
                tenant_id=tenant_id,
                title=title,
                description=(
                    f"“{document.title}” was due for review on "
                    f"{document.next_review_at.date().isoformat()}. Check that it is still "
                    "correct, then open the document and choose Mark reviewed.\n\n"
                    f"Document: {document_link(document.id)}"
                ),
                assignee_id=document.owner_id,
                due_at=now + REVIEW_TASK_DUE,
                origin={
                    "via": "governance",
                    "kind": "document_review",
                    "document_id": str(document.id),
                    "link": document_link(document.id),
                    "cycle": document.next_review_at.isoformat(),
                },
            )
            session.add(task)
            await session.flush()
            document.review_task_id = task.id
            await notify(
                session,
                tenant_id,
                document.owner_id,
                kind="document.review_due",
                title=f"Review due: {document.title[:150]}",
                body="Check that it is still correct, then mark it reviewed.",
                link=document_link(document.id),
            )
            await record_event(
                session,
                tenant_id=tenant_id,
                actor=Actor.system(),
                action="knowledge.document.review_reminded",
                resource_type="document",
                resource_id=str(document.id),
                details={
                    "collection_id": str(document.collection_id),
                    "owner": str(document.owner_id),
                    "task_id": str(task.id),
                    "cycle": document.next_review_at.isoformat(),
                },
            )
            report.reminded.append(document.id)
    return report


# --- Official status ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OfficialRequest:
    request_id: str
    requested_by: uuid.UUID
    requested_at: datetime
    note: str | None
    approvers: list[uuid.UUID]


async def pending_official_requests(
    session: AsyncSession, document_ids: Sequence[uuid.UUID] | None = None
) -> dict[uuid.UUID, OfficialRequest]:
    """Pending official-status requests by document (several approvers: one request)."""
    query = select(Approval).where(Approval.tool == OFFICIAL_KIND, Approval.status == "pending")
    if document_ids is not None:
        if not document_ids:
            return {}
        query = query.where(
            Approval.origin["document_id"].astext.in_([str(d) for d in document_ids])
        )
    requests: dict[uuid.UUID, OfficialRequest] = {}
    for approval in (await session.scalars(query.order_by(Approval.created_at))).all():
        document_id = uuid.UUID(str(approval.origin["document_id"]))
        existing = requests.get(document_id)
        if existing is None:
            requests[document_id] = OfficialRequest(
                request_id=str(approval.origin.get("request_id") or approval.id),
                requested_by=approval.requested_by,
                requested_at=approval.created_at,
                note=approval.arguments.get("note"),
                approvers=[approval.approver_id],
            )
        else:
            existing.approvers.append(approval.approver_id)
    return requests


async def _official_approvers(
    session: AsyncSession, collection: Collection, requester_id: uuid.UUID
) -> list[uuid.UUID]:
    """The collection's owner; when the owner is the one asking, or there is none (or they
    left), every active org admin who can open the collection, except the requester. Never the
    requester, and never someone who cannot read the document they would approve."""
    grants = await _grants(session, collection.id)
    owner = collection.owner_id
    if (
        owner is not None
        and owner != requester_id
        and await can_view_as(session, owner, collection, grants)
    ):
        return [owner]
    admins = await session.scalars(
        select(User.id)
        .where(User.org_role.in_(ADMIN_ROLES), User.status == "active", User.id != requester_id)
        .order_by(User.display_name)
    )
    return [a for a in admins.all() if await can_view_as(session, a, collection, grants)]


async def decider_problem(
    session: AsyncSession, approval: Approval, decider: RequestContext
) -> str | None:
    """Why `decider` may not decide this official-status request now, or None. Checked before
    any decision (approve or reject) is recorded, and again when approving runs: the approver
    must still be the collection's owner or an org admin, must not be the requester, and must
    be able to open the collection."""
    document_id = uuid.UUID(str(approval.arguments["document_id"]))
    document = await session.get(Document, document_id)
    if document is None:
        return None  # nothing to protect: approving fails with "the document was deleted"
    collection = await session.get(Collection, document.collection_id)
    if collection is None:
        return None
    if decider.user_id == approval.requested_by:
        return "Nobody approves their own request."
    approver = await session.get(User, decider.user_id)
    if approver is None or not (
        approver.id == collection.owner_id or approver.org_role in ADMIN_ROLES
    ):
        return "Only the collection's owner or an administrator decides this request."
    if not await can_view_as(session, decider.user_id, collection):
        return "You cannot open this collection, so you cannot decide on its documents."
    return None


async def unreadable_collections(
    session: AsyncSession, ctx: RequestContext, collection_ids: set[uuid.UUID]
) -> set[uuid.UUID]:
    """The collections among `collection_ids` the caller cannot open: what is said about their
    documents leaves out titles and notes (separation of duties, ARCHITECTURE 7.1)."""
    if not collection_ids:
        return set()
    collections = (
        await session.scalars(select(Collection).where(Collection.id.in_(collection_ids)))
    ).all()
    grants = (
        await session.scalars(
            select(CollectionGrant).where(CollectionGrant.collection_id.in_(collection_ids))
        )
    ).all()
    readable = {
        c.id
        for c in collections
        if set(acl_principals(c, [g for g in grants if g.collection_id == c.id])) & ctx.principals
    }
    return collection_ids - readable


def withhold_official_request(arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """An official-status request as shown to someone who cannot open its collection: which
    collection, not which document or why. Returns the arguments and the summary."""
    collection = str(arguments.get("collection") or "a collection")
    keys = ("document_id", "collection_id", "collection", "version")
    kept = {k: arguments[k] for k in keys if k in arguments}
    return {**kept, "withheld": True}, f"Make a document in “{collection[:150]}” official"


async def latest_version(session: AsyncSession, document_id: uuid.UUID) -> DocumentVersion | None:
    return (
        await session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no.desc())
            .limit(1)
        )
    ).first()


async def request_official(
    session: AsyncSession,
    ctx: RequestContext,
    document: Document,
    collection: Collection,
    *,
    note: str,
) -> OfficialRequest:
    """A collection editor asks for official status (checked by the API)."""
    if document.trust == "official":
        raise ApiError(409, "already_official", "This document is already official")
    if document.id in await pending_official_requests(session, [document.id]):
        raise ApiError(
            409, "official_pending", "Official status was already requested for this document"
        )
    approvers = await _official_approvers(session, collection, ctx.user_id)
    if not approvers:
        raise ApiError(
            409,
            "no_approver",
            "Nobody else can approve this",
            "The collection's owner, or another administrator who can open the collection, "
            "must approve official status, and there is none. An administrator can make "
            "someone else the collection's owner, or share the collection with an "
            "administrator.",
        )
    version = await latest_version(session, document.id)
    if version is None:
        raise ApiError(409, "no_version", "This document has no content to approve yet")
    request_id = str(uuid.uuid4())
    clean_note = note.strip()[:2000]
    summary = f"Make “{document.title[:150]}” official"
    for approver in approvers:
        await propose(
            session,
            ctx,
            tool=OFFICIAL_KIND,
            arguments={
                "document_id": str(document.id),
                "title": document.title,
                "collection_id": str(collection.id),
                "collection": collection.name,
                "note": clean_note,
                # What the approver reads is what becomes official: a new version voids this.
                "version_id": str(version.id),
                "version": version.version_no,
            },
            summary=summary,
            approver_id=approver,
            origin={
                "via": "governance",
                "kind": "official_status",
                "request_id": request_id,
                "document_id": str(document.id),
                "collection_id": str(collection.id),
            },
            ttl=OFFICIAL_TTL,
        )
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="knowledge.document.official_requested",
        resource_type="document",
        resource_id=str(document.id),
        details={
            "collection_id": str(collection.id),
            "request_id": request_id,
            "approvers": [str(a) for a in approvers],
            "note": clean_note,
            "version": version.version_no,
        },
    )
    return OfficialRequest(
        request_id=request_id,
        requested_by=ctx.user_id,
        requested_at=utcnow(),
        note=clean_note,
        approvers=approvers,
    )


def official_status_kind(
    sessions: async_sessionmaker[AsyncSession], on_trust_changed: TrustChanged
) -> ApprovalKind:
    """What approving an official-status request does: trust becomes official, and the approval
    counts as a review (ARCHITECTURE 7.3: official means approved, with a reviewer and a date).
    Who may decide is checked before the decision is recorded (`check`) and again when it runs,
    not only when the request was made. The approved content is the version the request named:
    if the document changed since, nothing happens and the editor asks again."""

    async def check(
        session: AsyncSession, approval: Approval, decider: RequestContext
    ) -> str | None:
        return await decider_problem(session, approval, decider)

    async def run(approval: Approval, decider: RequestContext) -> tuple[str, dict[str, Any]]:
        if approval.origin.get("kind") != "official_status":
            return "failed", {"error": "not an official-status request"}
        document_id = uuid.UUID(str(approval.arguments["document_id"]))
        async with tenant_transaction(sessions, approval.tenant_id) as session:
            document = await session.get(Document, document_id)
            if document is None:
                return "failed", {"error": "the document was deleted"}
            collection = await session.get(Collection, document.collection_id)
            assert collection is not None  # noqa: S101 (foreign key)
            if await decider_problem(session, approval, decider):
                return "failed", {"error": "the approver may no longer approve this"}
            current = await latest_version(session, document.id)
            if current is None or str(current.id) != approval.arguments.get("version_id"):
                return "failed", {
                    "error": "the document changed after the request: ask again, so the "
                    "approver sees the new content"
                }
            before = document.trust
            now = utcnow()
            document.trust = "official"
            document.reviewed_at, document.reviewed_by = now, decider.user_id
            document.next_review_at = next_review(now, collection.review_interval_days)
            await _close_review_task(session, document)
            await record_event(
                session,
                tenant_id=approval.tenant_id,
                actor=Actor(type="user", id=decider.user_id),
                action="knowledge.document.official_approved",
                resource_type="document",
                resource_id=str(document.id),
                details={
                    "collection_id": str(collection.id),
                    "approval_id": str(approval.id),
                    "requested_by": str(approval.requested_by),
                    "before": before,
                    "version": current.version_no,
                },
            )
        await on_trust_changed(approval.tenant_id, document_id, "official")
        return "executed", {"trust": "official", "document_id": str(document_id)}

    return ApprovalKind(run=run, check=check)


def _close_official_requests(
    approvals: Sequence[Approval],
    *,
    status: Literal["failed", "superseded"],
    reason: str,
    actor: uuid.UUID | None,
) -> None:
    now = utcnow()
    for approval in approvals:
        approval.status, approval.decided_at, approval.decided_by = status, now, actor
        approval.result = {"error" if status == "failed" else "reason": reason}


async def _pending_official(session: AsyncSession, *conditions: Any) -> list[Approval]:
    return list(
        (
            await session.scalars(
                select(Approval)
                .where(Approval.tool == OFFICIAL_KIND, Approval.status == "pending", *conditions)
                .order_by(Approval.created_at)
                .with_for_update()
            )
        ).all()
    )


async def new_version_stored(
    session: AsyncSession,
    ctx: RequestContext,
    document: Document,
    collection: Collection,
    *,
    version_no: int,
) -> str | None:
    """New content for an existing document (an upload with the same name, or a connector
    sync). Official status and the last review were given to the old content, so:

    - an official document drops to the collection's default trust (`reference` when that
      default is official), its review record is cleared, and this is audited as a demotion
      and told to the owner; making it official again needs a new approval;
    - a pending official-status request is closed: its approver would approve content they
      never saw. The requester is told to ask again.

    Returns the trust the document had before, when it was demoted."""
    pending = await _pending_official(
        session, Approval.origin["document_id"].astext == str(document.id)
    )
    if pending:
        _close_official_requests(
            pending,
            status="failed",
            reason=f"version {version_no} was uploaded after the request: ask again, so the "
            "approver sees the new content",
            actor=None,
        )
        told: set[uuid.UUID] = set()
        for approval in pending:
            if approval.requested_by in told:
                continue
            told.add(approval.requested_by)
            await notify(
                session,
                ctx.tenant_id,
                approval.requested_by,
                kind="approval.failed",
                title=f"Ask again: {approval.summary[:120]}",
                body=f"Version {version_no} of the document was uploaded after your request.",
                link=document_link(document.id),
            )
    if document.trust != "official":
        return None
    after = collection.default_trust if collection.default_trust != "official" else "reference"
    document.trust = after
    document.reviewed_at, document.reviewed_by = None, None
    note = (
        f"Version {version_no} was uploaded. Official status and the last review were for "
        "the earlier content: the new content needs a new approval."
    )
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="knowledge.document.demoted",
        resource_type="document",
        resource_id=str(document.id),
        details={
            "collection_id": str(collection.id),
            "title": document.title,
            "before": "official",
            "after": after,
            "note": note,
            "reason": "new_version",
            "version": version_no,
        },
    )
    if document.owner_id is not None and document.owner_id != ctx.user_id:
        await notify(
            session,
            ctx.tenant_id,
            document.owner_id,
            kind="document.demoted",
            title=f"No longer official: {document.title[:150]}",
            body=note,
            link=document_link(document.id),
        )
    return "official"


async def reroute_official_requests(
    session: AsyncSession, ctx: RequestContext, collection: Collection
) -> int:
    """After a collection's owner changed: each pending official-status request goes to whoever
    decides it now (the new owner, or the admins who can open the collection). Copies held by
    people who no longer decide are closed ("superseded", with the reason); new deciders get a
    copy with the same deadline. With nobody left to decide, the request fails and the
    requester is told. Returns the number of requests whose deciders changed."""
    pending = await _pending_official(
        session, Approval.origin["collection_id"].astext == str(collection.id)
    )
    by_request: dict[str, list[Approval]] = {}
    for approval in pending:
        by_request.setdefault(str(approval.origin.get("request_id") or approval.id), []).append(
            approval
        )
    changed = 0
    for request_id, copies in by_request.items():
        first = copies[0]
        deciders = await _official_approvers(session, collection, first.requested_by)
        holders = {a.approver_id for a in copies}
        if set(deciders) == holders:
            continue
        changed += 1
        if not deciders:
            _close_official_requests(
                copies,
                status="failed",
                reason="nobody can decide this request any more: the collection's owner changed",
                actor=ctx.user_id,
            )
            await notify(
                session,
                ctx.tenant_id,
                first.requested_by,
                kind="approval.failed",
                title=f"Nobody can decide: {first.summary[:120]}",
                body="The collection's owner changed and nobody else can approve it.",
                link=document_link(uuid.UUID(str(first.arguments["document_id"]))),
            )
        else:
            _close_official_requests(
                [a for a in copies if a.approver_id not in deciders],
                status="superseded",
                reason="The collection's owner changed: the request went to whoever decides now.",
                actor=ctx.user_id,
            )
            for approver in [d for d in deciders if d not in holders]:
                copy = Approval(
                    tenant_id=first.tenant_id,
                    tool=first.tool,
                    arguments=dict(first.arguments),
                    summary=first.summary,
                    requested_by=first.requested_by,
                    approver_id=approver,
                    expires_at=first.expires_at,
                    origin=dict(first.origin),
                )
                session.add(copy)
                await session.flush()
                await notify(
                    session,
                    ctx.tenant_id,
                    approver,
                    kind="approval.requested",
                    title=f"Approval needed: {first.summary[:120]}",
                    link=approval_link(copy.id),
                )
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="knowledge.document.official_rerouted",
            resource_type="document",
            resource_id=str(first.arguments.get("document_id")),
            details={
                "collection_id": str(collection.id),
                "request_id": request_id,
                "before": sorted(str(a) for a in holders),
                "after": [str(a) for a in deciders],
            },
        )
    return changed


async def demote(
    session: AsyncSession,
    ctx: RequestContext,
    document: Document,
    *,
    trust: Literal["reference", "discussion"],
    note: str | None,
) -> None:
    """An editor takes official status away (checked by the API): immediate, audited, and the
    owner is told."""
    document.trust = trust
    clean_note = (note or "").strip()[:2000] or None
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="knowledge.document.demoted",
        resource_type="document",
        resource_id=str(document.id),
        details={
            "collection_id": str(document.collection_id),
            "title": document.title,
            "before": "official",
            "after": trust,
            "note": clean_note,
        },
    )
    if document.owner_id is not None and document.owner_id != ctx.user_id:
        await notify(
            session,
            ctx.tenant_id,
            document.owner_id,
            kind="document.demoted",
            title=f"No longer official: {document.title[:150]}",
            body=clean_note or "",
            link=document_link(document.id),
        )


# --- Overview ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    everything: bool  # org admins see every collection; owners see the ones they own
    collections: dict[uuid.UUID, Collection]
    viewable: set[uuid.UUID]  # collections the caller can open (others: titles withheld)


async def overview_scope(session: AsyncSession, ctx: RequestContext) -> Scope:
    everything = is_org_admin(ctx)
    query = select(Collection).order_by(Collection.name)
    if not everything:
        query = query.where(Collection.owner_id == ctx.user_id)
    collections = {c.id: c for c in (await session.scalars(query)).all()}
    grants = (
        await session.scalars(
            select(CollectionGrant).where(CollectionGrant.collection_id.in_(list(collections)))
        )
    ).all()
    viewable = {
        c.id
        for c in collections.values()
        if set(acl_principals(c, [g for g in grants if g.collection_id == c.id])) & ctx.principals
    }
    return Scope(everything=everything, collections=collections, viewable=viewable)


@dataclass(frozen=True)
class Overview:
    scope: Scope
    overdue: list[Document]
    due_soon: list[Document]
    without_owner: list[Document]
    pending: dict[uuid.UUID, OfficialRequest]
    pending_documents: dict[uuid.UUID, Document]
    demotions: list[AuditEvent]
    people: dict[uuid.UUID, User]


async def overview(
    session: AsyncSession, ctx: RequestContext, *, now: datetime | None = None
) -> Overview:
    now = now or utcnow()
    scope = await overview_scope(session, ctx)
    ids = list(scope.collections)

    async def documents(*conditions: Any) -> list[Document]:
        if not ids:
            return []
        rows = await session.scalars(
            select(Document)
            .where(Document.collection_id.in_(ids), *conditions)
            .order_by(Document.next_review_at.nulls_last(), Document.title)
            .limit(OVERVIEW_LIMIT)
        )
        return list(rows.all())

    overdue = await documents(Document.next_review_at < now)
    due_soon = await documents(
        Document.next_review_at >= now, Document.next_review_at < now + DUE_SOON
    )
    inactive = select(User.id).where(User.status != "active")
    without_owner = await documents(
        or_(Document.owner_id.is_(None), Document.owner_id.in_(inactive))
    )
    pending: dict[uuid.UUID, OfficialRequest] = {}
    pending_documents: dict[uuid.UUID, Document] = {}
    if ids:
        for document_id, request in (await pending_official_requests(session)).items():
            document = await session.get(Document, document_id)
            if document is not None and document.collection_id in scope.collections:
                pending[document_id] = request
                pending_documents[document_id] = document
    demotions: list[AuditEvent] = []
    if ids:
        events = await session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.action == "knowledge.document.demoted",
                AuditEvent.occurred_at >= now - RECENT,
                AuditEvent.details["collection_id"].astext.in_([str(i) for i in ids]),
            )
            .order_by(AuditEvent.seq.desc())
            .limit(OVERVIEW_LIMIT)
        )
        demotions = list(events.all())
    person_ids: set[uuid.UUID] = set()
    for document in [*overdue, *due_soon, *without_owner, *pending_documents.values()]:
        person_ids.update(i for i in (document.owner_id, document.reviewed_by) if i)
    for request in pending.values():
        person_ids.update([request.requested_by, *request.approvers])
    person_ids.update(e.actor_id for e in demotions if e.actor_id)
    people = (
        {u.id: u for u in (await session.scalars(select(User).where(User.id.in_(person_ids))))}
        if person_ids
        else {}
    )
    return Overview(
        scope=scope,
        overdue=overdue,
        due_soon=due_soon,
        without_owner=without_owner,
        pending=pending,
        pending_documents=pending_documents,
        demotions=demotions,
        people=people,
    )

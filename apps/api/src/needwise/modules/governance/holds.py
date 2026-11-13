"""Legal holds, as every deletion path sees them (docs/enterprise.md#legal-hold).

Placing and releasing a hold is the Enterprise feature "legal_hold" (needwise_ee.legal_hold).
Enforcement is here, in the core, and does not ask the license: a hold is an obligation to
preserve, so an expired license must never quietly let held data go. Every path that deletes a
document, a conversation, a meeting or an Insights question asks this module first:

- people (documents, conversations, meetings, a source with its documents): refused with
  `409 legal_hold`, and the attempt is audited (`legal_hold.deletion_blocked`);
- connectors' deletion propagation: the held documents stay, one audit event per sync run;
- retention runs: held items are kept and counted, in the preview too;
- tenant deletion: refused while any hold is active, unless the operator types the override.

What a hold covers (HOLD_TARGETS):

| Hold on       | Keeps                                                                         |
|---------------|-------------------------------------------------------------------------------|
| document      | that document                                                                 |
| collection    | its documents and its meetings                                                |
| user          | their conversations, the meetings they uploaded, their Insights questions     |
| meeting       | that meeting and its transcript document                                      |
| analytics_run | that Insights question                                                        |

A meeting's transcript is a document: holding the meeting holds the transcript, and holding the
transcript (or its collection) holds the meeting, since deleting a meeting deletes its
transcript. `covering` answers for one item; the `*_held` clauses select the same items in bulk
(retention), and a test checks that the two agree.
"""

import uuid
from collections.abc import Sequence
from typing import Any, Final, Literal, get_args

from sqlalchemy import ColumnElement, Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.errors import ApiError
from needwise.core.tenancy import tenant_transaction
from needwise.modules.analytics.models import AnalyticsRun
from needwise.modules.conversations.models import Conversation
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.governance.models import HOLD_TARGETS, LegalHold
from needwise.modules.knowledge.models import Document
from needwise.modules.meetings.models import Meeting

HoldTarget = Literal["document", "collection", "user", "meeting", "analytics_run"]
# What can be deleted, and so blocked.
Held = Literal["document", "conversation", "meeting", "analytics_run"]
# Who or what tried: a person, a connector's sync, a retention run, a tenant's deletion.
Via = Literal["user", "connector", "retention", "tenant_deletion"]

BLOCKED: Final = "legal_hold.deletion_blocked"
LABELS: Final[dict[Held, str]] = {
    "document": "document",
    "conversation": "conversation",
    "meeting": "meeting",
    "analytics_run": "Insights question",
}
assert set(get_args(HoldTarget)) == set(HOLD_TARGETS)  # noqa: S101 (one list, two spellings)

Pair = tuple[str, uuid.UUID]


# --- One item ----------------------------------------------------------------------------------


def _meeting_pairs(meeting: Meeting) -> list[Pair]:
    pairs: list[Pair] = [
        ("meeting", meeting.id),
        ("user", meeting.owner_id),
        ("collection", meeting.collection_id),
    ]
    if meeting.document_id is not None:
        pairs.append(("document", meeting.document_id))
    return pairs


async def _pairs(session: AsyncSession, kind: Held, item: Any) -> list[Pair]:
    """The (target_type, target_id) of every hold that would cover `item`."""
    if kind == "document":
        document: Document = item
        pairs: list[Pair] = [("document", document.id), ("collection", document.collection_id)]
        meetings = await session.scalars(select(Meeting).where(Meeting.document_id == document.id))
        for meeting in meetings:
            pairs += _meeting_pairs(meeting)
        return pairs
    if kind == "meeting":
        return _meeting_pairs(item)
    if kind == "conversation":
        conversation: Conversation = item
        return [("user", conversation.user_id)]
    run: AnalyticsRun = item
    return [("analytics_run", run.id), ("user", run.user_id)]


async def covering(session: AsyncSession, kind: Held, item: Any) -> list[LegalHold]:
    """The active holds that keep `item` from being deleted (empty: it may go)."""
    pairs = set(await _pairs(session, kind, item))
    query = select(LegalHold).where(
        LegalHold.released_at.is_(None),
        or_(*(and_(LegalHold.target_type == t, LegalHold.target_id == i) for t, i in pairs)),
    )
    return list((await session.scalars(query.order_by(LegalHold.created_at))).all())


def held_error(kind: Held) -> ApiError:
    return ApiError(
        409,
        "legal_hold",
        f"This {LABELS[kind]} is under a legal hold",
        "It cannot be deleted while the hold lasts. An administrator can tell you more.",
    )


async def record_blocked(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    actor: Actor,
    kind: Held | Literal["tenant"],
    resource_id: uuid.UUID | str,
    holds: Sequence[LegalHold],
    via: Via,
    **details: Any,
) -> None:
    await record_event(
        session,
        tenant_id=tenant_id,
        actor=actor,
        action=BLOCKED,
        resource_type=kind,
        resource_id=str(resource_id),
        details={
            "via": via,
            "holds": [str(h.id) for h in holds],
            "matters": sorted({h.matter for h in holds}),
            **details,
        },
    )


async def refuse(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    *,
    actor: Actor,
    kind: Held,
    resource_id: uuid.UUID,
    holds: Sequence[LegalHold],
    via: Via = "user",
) -> ApiError:
    """Audits a refused deletion in its own transaction (the caller's request changed nothing
    and its transaction is gone) and returns the 409 to raise."""
    async with tenant_transaction(sessions, tenant_id) as session:
        await record_blocked(
            session,
            tenant_id,
            actor=actor,
            kind=kind,
            resource_id=resource_id,
            holds=holds,
            via=via,
        )
    return held_error(kind)


# --- In bulk -----------------------------------------------------------------------------------


def targets(target_type: HoldTarget) -> Select[uuid.UUID]:
    """The ids named by active holds of one kind (a subquery for IN)."""
    return select(LegalHold.target_id).where(
        LegalHold.released_at.is_(None), LegalHold.target_type == target_type
    )


def meeting_held() -> ColumnElement[bool]:
    return or_(
        Meeting.id.in_(targets("meeting")),
        Meeting.owner_id.in_(targets("user")),
        Meeting.collection_id.in_(targets("collection")),
        and_(Meeting.document_id.is_not(None), Meeting.document_id.in_(targets("document"))),
    )


def document_held() -> ColumnElement[bool]:
    transcripts = select(Meeting.document_id).where(
        Meeting.document_id.is_not(None), meeting_held()
    )
    return or_(
        Document.id.in_(targets("document")),
        Document.collection_id.in_(targets("collection")),
        Document.id.in_(transcripts),
    )


def conversation_held() -> ColumnElement[bool]:
    return Conversation.user_id.in_(targets("user"))


def analytics_run_held() -> ColumnElement[bool]:
    return or_(
        AnalyticsRun.id.in_(targets("analytics_run")), AnalyticsRun.user_id.in_(targets("user"))
    )


async def active_holds(session: AsyncSession) -> list[LegalHold]:
    return list(
        (
            await session.scalars(
                select(LegalHold)
                .where(LegalHold.released_at.is_(None))
                .order_by(LegalHold.created_at)
            )
        ).all()
    )

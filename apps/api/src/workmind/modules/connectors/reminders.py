"""Telling people about sources that need them (docs/connectors.md).

- **A source that keeps failing.** After `failure_alert_after` failed syncs in a row, the people
  who manage it are notified, once per failure episode: the source's owner when they can still
  manage it (an org admin, or the owner of its collection) and the collection's owner; when
  neither is active, every active org owner and admin. The next successful sync ends the episode
  and tells the same people it recovered. The sync engine checks after every sync; the daily
  task checks again (for a threshold that was lowered).
- **Stale synced documents.** Once a day, a synced document that has not changed in its source
  for the source's `stale_after_days`, or that a cut-short scan no longer lists ("missing
  upstream", kept until a complete scan decides), is a stale document. Its owner (the governance
  owner, docs/governance.md) gets one task and one notification per daily run that lists every
  document that became stale, and each document is reminded once per staleness episode: the
  episode ends when the document changes in the source or is no longer stale. An owner who left,
  or who can no longer open the collection, is not reminded (the Sources page lists the
  document instead).

Both are idempotent: the markers (`sources.failure_alerted_at`, `documents.stale_reminded_at`)
are written in the same transaction as the notifications, with the rows locked.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import ColumnElement, and_, false, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import tenant_transaction
from workmind.modules.connectors.health import StaleReason, explain
from workmind.modules.connectors.models import Source
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import User
from workmind.modules.knowledge.governance import can_view_as
from workmind.modules.knowledge.models import Collection, Document
from workmind.modules.work.links import document_link, source_link, task_link
from workmind.modules.work.models import Task
from workmind.modules.work.notifications import notify

ADMIN_ROLES: Final = ("owner", "admin")
STALE_TASK_DUE: Final = timedelta(days=14)
TASK_LIST_LIMIT: Final = 50  # documents named in one task; the rest are counted


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- Failing sources ---------------------------------------------------------------------------


async def _active(session: AsyncSession, user_id: uuid.UUID | None) -> User | None:
    if user_id is None:
        return None
    user = await session.get(User, user_id)
    return user if user is not None and user.status == "active" else None


async def failure_recipients(session: AsyncSession, source: Source) -> list[tuple[User, bool]]:
    """Who manages the source, with whether each is an org admin (their link differs)."""
    collection = await session.get(Collection, source.collection_id)
    collection_owner = await _active(session, collection.owner_id if collection else None)
    people: dict[uuid.UUID, User] = {}
    owner = await _active(session, source.owner_id)
    if owner is not None and (
        owner.org_role in ADMIN_ROLES or (collection_owner and collection_owner.id == owner.id)
    ):
        people[owner.id] = owner
    if collection_owner is not None:
        people[collection_owner.id] = collection_owner
    if not people:
        admins = await session.scalars(
            select(User).where(User.org_role.in_(ADMIN_ROLES), User.status == "active")
        )
        people = {u.id: u for u in admins}
    return [(u, u.org_role in ADMIN_ROLES) for u in people.values()]


async def check_failing(
    session: AsyncSession, source: Source, *, threshold: int, now: datetime | None = None
) -> bool:
    """Alerts once per failure episode. The caller holds the source row (it was just updated,
    or selected FOR UPDATE). Returns whether an alert was sent."""
    if (
        not source.enabled
        or source.consecutive_failures < threshold
        or source.failure_alerted_at is not None
    ):
        return False
    now = now or utcnow()
    recipients = await failure_recipients(session, source)
    plain = explain(source.last_error) or "The last sync failed."
    for user, admin in recipients:
        await notify(
            session,
            source.tenant_id,
            user.id,
            kind="source.failing",
            title=f"“{source.name[:120]}” keeps failing to sync",
            body=f"{source.consecutive_failures} syncs in a row failed. {plain}",
            link=source_link(source.key, admin=admin),
        )
    source.failure_alerted_at = now
    await record_event(
        session,
        tenant_id=source.tenant_id,
        actor=Actor.system(),
        action="source.failing",
        resource_type="source",
        resource_id=source.key,
        details={
            "failures": source.consecutive_failures,
            "notified": [str(u.id) for u, _ in recipients],
            "last_error": (source.last_error or "")[:300] or None,
        },
    )
    return True


async def announce_recovery(session: AsyncSession, source: Source) -> None:
    """The first success after an alert: the same people hear that it works again."""
    recipients = await failure_recipients(session, source)
    for user, admin in recipients:
        await notify(
            session,
            source.tenant_id,
            user.id,
            kind="source.recovered",
            title=f"“{source.name[:120]}” syncs again",
            body="The last sync succeeded after the failures you were told about.",
            link=source_link(source.key, admin=admin),
        )
    await record_event(
        session,
        tenant_id=source.tenant_id,
        actor=Actor.system(),
        action="source.recovered",
        resource_type="source",
        resource_id=source.key,
        details={"notified": [str(u.id) for u, _ in recipients]},
    )


# --- Stale synced documents ----------------------------------------------------------------------


def stale_condition(
    sources: Sequence[Source], now: datetime, days: int | None = None
) -> ColumnElement[bool]:
    """Synced documents of `sources` that are stale now: missing upstream, or not changed in
    the source for `days` (default: each source's own threshold; none: only missing)."""
    not_updated = [
        and_(
            Document.source_id == s.id,
            Document.source_changed_at < now - timedelta(days=threshold),
        )
        for s in sources
        if (threshold := days if days is not None else s.stale_after_days)
    ]
    ids = [s.id for s in sources]
    if not ids:
        return false()
    return and_(
        Document.source_id.in_(ids),
        or_(Document.source_missing_since.is_not(None), *not_updated),
    )


def stale_reason(document: Document) -> StaleReason:
    return "missing_upstream" if document.source_missing_since is not None else "not_updated"


@dataclass
class StaleReminderReport:
    reminded: list[uuid.UUID] = field(default_factory=list[uuid.UUID])
    tasks: list[uuid.UUID] = field(default_factory=list[uuid.UUID])
    without_owner: int = 0
    episodes_ended: int = 0
    failing_alerted: list[str] = field(default_factory=list[str])


def _line(document: Document, sources: dict[uuid.UUID, Source]) -> str:
    source = sources.get(document.source_id) if document.source_id else None
    where = f" ({source.name})" if source else ""
    if document.source_missing_since is not None:
        why = (
            "no longer listed by the source since "
            f"{document.source_missing_since.date().isoformat()}; it may have been deleted there"
        )
    else:
        changed = document.source_changed_at
        why = f"unchanged in the source since {changed.date().isoformat()}" if changed else "old"
    return f"- {document.title}{where}: {why}. {document_link(document.id)}"


async def _remind_owner(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    documents: list[Document],
    sources: dict[uuid.UUID, Source],
    now: datetime,
) -> uuid.UUID:
    count = len(documents)
    one = documents[0]
    title = (
        f"Check “{one.title[:150]}”: it looks stale"
        if count == 1
        else f"Check {count} synced documents that look stale"
    )
    lines = [_line(d, sources) for d in documents[:TASK_LIST_LIMIT]]
    if count > TASK_LIST_LIMIT:
        lines.append(f"…and {count - TASK_LIST_LIMIT} more (Sources > Stale documents).")
    task = Task(
        tenant_id=tenant_id,
        title=title[:200],
        description=(
            "These documents come from a connected source and may be out of date. Check each "
            "one in its source: update it there, or delete it there if nobody needs it any "
            "more (WorkMind follows at the next sync).\n\n" + "\n".join(lines)
        ),
        assignee_id=owner_id,
        due_at=now + STALE_TASK_DUE,
        origin={
            "via": "connectors",
            "kind": "stale_documents",
            "documents": [
                {"id": str(d.id), "title": d.title[:200], "reason": stale_reason(d)}
                for d in documents[:TASK_LIST_LIMIT]
            ],
            "more": max(0, count - TASK_LIST_LIMIT),
        },
    )
    session.add(task)
    await session.flush()
    await notify(
        session,
        tenant_id,
        owner_id,
        kind="source.stale_documents",
        title=title,
        body="They come from a connected source and haven't changed there for a while, or "
        "the source no longer lists them.",
        link=task_link(task.id),
    )
    for document in documents:
        document.stale_reminded_at = now
        source = sources.get(document.source_id) if document.source_id else None
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=Actor.system(),
            action="knowledge.document.stale_reminded",
            resource_type="document",
            resource_id=str(document.id),
            details={
                "source": source.key if source else None,
                "reason": stale_reason(document),
                "owner": str(owner_id),
                "task_id": str(task.id),
            },
        )
    return task.id


async def send_source_reminders(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    *,
    failure_alert_after: int,
    now: datetime | None = None,
) -> StaleReminderReport:
    """Daily (Celery beat). Idempotent: running it twice, or twice at once, reminds once."""
    now = now or utcnow()
    report = StaleReminderReport()
    async with tenant_transaction(sessions, tenant_id) as session:
        sources = {
            s.id: s
            for s in await session.scalars(select(Source).order_by(Source.key).with_for_update())
        }
        for source in sources.values():
            if await check_failing(session, source, threshold=failure_alert_after, now=now):
                report.failing_alerted.append(source.key)

        stale = stale_condition(list(sources.values()), now)
        # Episodes that ended without a new version (the threshold changed, the item came back).
        ended = await session.execute(
            update(Document)
            .where(
                Document.source_id.is_not(None),
                Document.stale_reminded_at.is_not(None),
                ~func.coalesce(stale, false()),
            )
            .values(stale_reminded_at=None)
            .execution_options(synchronize_session=False)
        )
        report.episodes_ended = int(getattr(ended, "rowcount", 0) or 0)

        due = (
            await session.scalars(
                select(Document)
                .where(stale, Document.stale_reminded_at.is_(None))
                .order_by(Document.source_changed_at.nulls_first(), Document.title)
                .with_for_update(skip_locked=True)
            )
        ).all()
        by_owner: dict[uuid.UUID, list[Document]] = defaultdict(list)
        collections: dict[uuid.UUID, Collection | None] = {}
        allowed: dict[tuple[uuid.UUID, uuid.UUID], bool] = {}
        for document in due:
            owner_id = document.owner_id
            if owner_id is None or await _active(session, owner_id) is None:
                report.without_owner += 1
                continue
            if document.collection_id not in collections:
                collections[document.collection_id] = await session.get(
                    Collection, document.collection_id
                )
            collection = collections[document.collection_id]
            key = (owner_id, document.collection_id)
            if key not in allowed:
                allowed[key] = collection is not None and await can_view_as(
                    session, owner_id, collection
                )
            if not allowed[key]:
                report.without_owner += 1  # titles never reach someone who can't open them
                continue
            by_owner[owner_id].append(document)
        for owner_id, documents in by_owner.items():
            report.tasks.append(
                await _remind_owner(session, tenant_id, owner_id, documents, sources, now)
            )
            report.reminded += [d.id for d in documents]
    return report

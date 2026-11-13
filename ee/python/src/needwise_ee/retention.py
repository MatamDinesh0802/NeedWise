"""Retention policies (Enterprise feature "retention_policies"; docs/enterprise.md).

Per tenant, how many days to keep:

- conversations, counted from their last message (their messages go with them);
- BI agent runs (questions and answers in Insights), from when they were asked;
- meeting recordings: the media file only. The meeting, its transcript, summary, decisions and
  action items stay; the meeting shows that its recording was deleted;
- notifications that were read, from when they were read. Unread ones are never deleted.

Every period is at least 30 days (MIN_RETENTION_DAYS, also enforced by the database). Audit
events are never deleted: the log is append-only and hash-chained, the runtime database role
cannot delete from it, and deleting old events would break verification of every later one.

Items under a legal hold (needwise.modules.governance.holds) are kept, however old: the preview
and the run count them as `kept_under_hold`, and the run's audit event lists them.

Documents have their own rules, per collection (needwise_ee.document_retention); the daily task
applies both.

Everything runs inside one tenant's transaction, so row-level security limits it to that
tenant's rows; media keys are tenant-prefixed and checked again before a file is deleted.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import structlog
from fastapi import APIRouter, Request
from needwise.api.deps import resources_of
from needwise.api.v1.admin import AdminContext, PersonRef
from needwise.core.errors import ApiError
from needwise.core.resources import Resources
from needwise.core.tenancy import global_transaction, tenant_transaction
from needwise.modules.analytics.models import AnalyticsRun
from needwise.modules.conversations.models import Conversation
from needwise.modules.governance import holds
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.governance.models import (
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    RetentionPolicy,
)
from needwise.modules.identity.models import Tenant, User
from needwise.modules.knowledge.models import DocumentVersion
from needwise.modules.knowledge.storage import ObjectStorage, owned_key
from needwise.modules.meetings import transcripts
from needwise.modules.meetings.models import Meeting
from needwise.modules.work.models import Notification
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise_ee import document_retention

log = structlog.get_logger("needwise_ee.retention")

FEATURE: Final = "retention_policies"
TASK_NAME: Final = "needwise_ee.retention.apply"
DAILY_S: Final = 86_400.0

Kind = Literal["conversations", "analytics_runs", "meeting_recordings", "read_notifications"]
KINDS: Final[tuple[Kind, ...]] = (
    "conversations",
    "analytics_runs",
    "meeting_recordings",
    "read_notifications",
)

Days = int | None


class PolicyIn(BaseModel):
    """Days to keep each kind of data; null keeps it forever."""

    conversations_days: Days = Field(default=None, ge=MIN_RETENTION_DAYS, le=MAX_RETENTION_DAYS)
    analytics_runs_days: Days = Field(default=None, ge=MIN_RETENTION_DAYS, le=MAX_RETENTION_DAYS)
    meeting_recordings_days: Days = Field(
        default=None, ge=MIN_RETENTION_DAYS, le=MAX_RETENTION_DAYS
    )
    read_notifications_days: Days = Field(
        default=None, ge=MIN_RETENTION_DAYS, le=MAX_RETENTION_DAYS
    )

    def days(self, kind: Kind) -> int | None:
        value: int | None = getattr(self, f"{kind}_days")
        return value


class PolicyOut(PolicyIn):
    minimum_days: int = MIN_RETENTION_DAYS
    updated_at: datetime | None
    updated_by: PersonRef | None
    last_run_at: datetime | None
    last_run: dict[str, Any]


class Counts(BaseModel):
    conversations: int = 0
    analytics_runs: int = 0
    meeting_recordings: int = 0
    read_notifications: int = 0


class PreviewOut(BaseModel):
    as_of: datetime
    # The oldest a kept item may be, per kind (None: that kind is kept forever).
    cutoffs: dict[str, datetime | None]
    would_delete: Counts
    # Expired, but a legal hold keeps them (read notifications are never held).
    kept_under_hold: Counts = Field(default_factory=Counts)


class RunIn(BaseModel):
    confirm: Literal["delete"] = Field(description='"delete": deleting cannot be undone')


class RunOut(BaseModel):
    as_of: datetime
    deleted: Counts
    files_deleted: int
    kept_under_hold: Counts = Field(default_factory=Counts)


# --- The rules ---------------------------------------------------------------------------------


def _policy_in(policy: RetentionPolicy | None) -> PolicyIn:
    if policy is None:
        return PolicyIn()
    return PolicyIn(
        conversations_days=policy.conversations_days,
        analytics_runs_days=policy.analytics_runs_days,
        meeting_recordings_days=policy.meeting_recordings_days,
        read_notifications_days=policy.read_notifications_days,
    )


def cutoffs(policy: PolicyIn, now: datetime) -> dict[Kind, datetime | None]:
    return {
        kind: now - timedelta(days=days) if (days := policy.days(kind)) else None for kind in KINDS
    }


async def _expired_recordings(
    session: AsyncSession, cutoff: datetime, *, held: bool = False
) -> list[Meeting]:
    """Recordings older than `cutoff` that a run deletes, or (held=True) that a hold keeps."""
    candidates = await session.scalars(
        select(Meeting).where(
            Meeting.created_at < cutoff,
            Meeting.media_deleted_at.is_(None),
            holds.meeting_held() if held else ~holds.meeting_held(),
        )
    )
    # Recordings only: an uploaded transcript file is the transcript, which is kept.
    return [m for m in candidates if _is_recording(m.filename)]


def _expired(kind: Kind, cutoff: datetime) -> Any:
    """The rows of one kind older than `cutoff`, held or not (meeting recordings: see above)."""
    if kind == "conversations":
        return select(Conversation).where(Conversation.last_message_at < cutoff)
    if kind == "analytics_runs":
        return select(AnalyticsRun).where(AnalyticsRun.created_at < cutoff)
    return select(Notification).where(Notification.read_at < cutoff)


HELD = {"conversations": holds.conversation_held, "analytics_runs": holds.analytics_run_held}


def _is_recording(filename: str) -> bool:
    try:
        return transcripts.kind(filename) == "audio"
    except ValueError:
        return False


async def count_expired(
    session: AsyncSession, policy: PolicyIn, now: datetime
) -> tuple[Counts, Counts]:
    """Exactly what `delete_expired` would delete at `now`, in the session's tenant, and what
    legal holds keep although it expired."""
    limits = cutoffs(policy, now)
    counts, kept = Counts(), Counts()
    for kind in ("conversations", "analytics_runs"):
        if cutoff := limits[kind]:
            held = HELD[kind]()
            setattr(counts, kind, await _count(session, _expired(kind, cutoff).where(~held)))
            setattr(kept, kind, await _count(session, _expired(kind, cutoff).where(held)))
    if cutoff := limits["meeting_recordings"]:
        counts.meeting_recordings = len(await _expired_recordings(session, cutoff))
        kept.meeting_recordings = len(await _expired_recordings(session, cutoff, held=True))
    if cutoff := limits["read_notifications"]:
        counts.read_notifications = await _count(session, _expired("read_notifications", cutoff))
    return counts, kept


async def _count(session: AsyncSession, query: Any) -> int:
    return int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)


@dataclass(frozen=True)
class Deletion:
    counts: Counts
    media_keys: list[str]  # files no longer referenced by anything: delete after commit


async def delete_expired(
    session: AsyncSession, tenant_id: uuid.UUID, policy: PolicyIn, now: datetime
) -> Deletion:
    limits = cutoffs(policy, now)
    counts = Counts()
    if cutoff := limits["conversations"]:
        result = await session.execute(
            delete(Conversation)
            .where(Conversation.last_message_at < cutoff, ~holds.conversation_held())
            .returning(Conversation.id)
        )
        counts.conversations = len(result.all())  # messages are deleted with them (cascade)
    if cutoff := limits["analytics_runs"]:
        result = await session.execute(
            delete(AnalyticsRun)
            .where(AnalyticsRun.created_at < cutoff, ~holds.analytics_run_held())
            .returning(AnalyticsRun.id)
        )
        counts.analytics_runs = len(result.all())
    keys: list[str] = []
    if cutoff := limits["meeting_recordings"]:
        expired = await _expired_recordings(session, cutoff)
        for meeting in expired:
            meeting.media_deleted_at = now
        counts.meeting_recordings = len(expired)
        await session.flush()
        keys = await _unreferenced(session, tenant_id, {m.media_key for m in expired})
    if cutoff := limits["read_notifications"]:
        result = await session.execute(
            delete(Notification).where(Notification.read_at < cutoff).returning(Notification.id)
        )
        counts.read_notifications = len(result.all())
    return Deletion(counts=counts, media_keys=keys)


async def _unreferenced(session: AsyncSession, tenant_id: uuid.UUID, keys: set[str]) -> list[str]:
    """Storage is content-addressed: the same bytes may also be another meeting's recording
    that is still kept, or a document's file. Those keys stay."""
    if not keys:
        return []
    kept_meetings = await session.scalars(
        select(Meeting.media_key).where(
            Meeting.media_key.in_(keys), Meeting.media_deleted_at.is_(None)
        )
    )
    documents = await session.scalars(
        select(DocumentVersion.storage_key).where(DocumentVersion.storage_key.in_(keys))
    )
    still_used = set(kept_meetings.all()) | set(documents.all())
    prefix = f"tenants/{tenant_id}/"
    return sorted(k for k in keys - still_used if k.startswith(prefix))


async def apply_policy(
    sessions: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    tenant_id: uuid.UUID,
    *,
    actor: Actor,
    trigger: Literal["manual", "schedule"],
    now: datetime | None = None,
) -> tuple[RunOut, bool]:
    """Deletes what the tenant's policy says is expired, records it on the policy and in the
    audit log, then deletes the freed files. Returns (result, whether a policy exists)."""
    now = now or datetime.now(UTC)
    async with tenant_transaction(sessions, tenant_id) as session:
        policy = await session.scalar(select(RetentionPolicy))
        if policy is None:
            return RunOut(as_of=now, deleted=Counts(), files_deleted=0), False
        _, kept = await count_expired(session, _policy_in(policy), now)
        deletion = await delete_expired(session, tenant_id, _policy_in(policy), now)
        policy.last_run_at = now
        policy.last_run = {
            "trigger": trigger,
            "deleted": deletion.counts.model_dump(),
            "files": len(deletion.media_keys),
            "kept_under_hold": kept.model_dump(),
        }
        await record_event(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action="admin.retention.run",
            resource_type="retention_policy",
            resource_id=str(policy.id),
            details={
                "trigger": trigger,
                "policy": _policy_in(policy).model_dump(),
                "deleted": deletion.counts.model_dump(),
                "files": len(deletion.media_keys),
                "kept_under_hold": kept.model_dump(),
            },
        )
    deleted_files = 0
    for key in deletion.media_keys:  # after the commit: the rows no longer point at them
        try:
            await storage.delete(owned_key(tenant_id, key))
            deleted_files += 1
        except OSError as exc:
            log.warning("retention_file_not_deleted", key=key, error=type(exc).__name__)
    return (
        RunOut(
            as_of=now, deleted=deletion.counts, files_deleted=deleted_files, kept_under_hold=kept
        ),
        True,
    )


async def apply_all(resources: Resources) -> int:
    """The daily task: every active tenant's policy and document rules (when it has any)."""
    async with global_transaction(resources.db) as session:
        tenants = list(
            (await session.scalars(select(Tenant.id).where(Tenant.status == "active"))).all()
        )
    applied = 0
    for tenant_id in tenants:
        _, had_policy = await apply_policy(
            resources.db, resources.storage, tenant_id, actor=Actor.system(), trigger="schedule"
        )
        _, had_rules = await document_retention.apply_rules(
            resources.db,
            resources.storage,
            resources.qdrant,
            resources.collection_name,
            tenant_id,
            actor=Actor.system(),
            trigger="schedule",
        )
        applied += had_policy or had_rules
    log.info("retention_applied", tenants=applied)
    return applied


# --- HTTP (owners and admins; mounted behind sign-in and the license) ----------------------------

router = APIRouter(prefix="/admin/retention", tags=["enterprise"])


async def _policy_out(session: AsyncSession, policy: RetentionPolicy | None) -> PolicyOut:
    updated_by = (
        await session.get(User, policy.updated_by) if policy and policy.updated_by else None
    )
    return PolicyOut(
        **_policy_in(policy).model_dump(),
        updated_at=policy.updated_at if policy else None,
        updated_by=PersonRef(id=updated_by.id, display_name=updated_by.display_name)
        if updated_by
        else None,
        last_run_at=policy.last_run_at if policy else None,
        last_run=policy.last_run if policy else {},
    )


@router.get("")
async def get_policy(ctx: AdminContext, request: Request) -> PolicyOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        return await _policy_out(session, await session.scalar(select(RetentionPolicy)))


@router.put("")
async def put_policy(ctx: AdminContext, request: Request, body: PolicyIn) -> PolicyOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        policy = await session.scalar(select(RetentionPolicy))
        before = _policy_in(policy).model_dump()
        if policy is None:
            policy = RetentionPolicy(tenant_id=ctx.tenant_id)
            session.add(policy)
        for field, value in body.model_dump().items():
            setattr(policy, field, value)
        policy.updated_by = ctx.user_id
        await session.flush()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.retention.updated",
            resource_type="retention_policy",
            resource_id=str(policy.id),
            details={"before": before, "after": body.model_dump()},
        )
        await session.refresh(policy)
        return await _policy_out(session, policy)


@router.post("/preview")
async def preview(ctx: AdminContext, request: Request, body: PolicyIn | None = None) -> PreviewOut:
    """What a run would delete now. Deletes nothing. Without a body: the saved policy; with one:
    that draft, so an admin sees the effect of a change before saving it."""
    now = datetime.now(UTC)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        policy = body or _policy_in(await session.scalar(select(RetentionPolicy)))
        counts, kept = await count_expired(session, policy, now)
    return PreviewOut(
        as_of=now,
        cutoffs={kind: cutoff for kind, cutoff in cutoffs(policy, now).items()},
        would_delete=counts,
        kept_under_hold=kept,
    )


@router.post("/run")
async def run(ctx: AdminContext, request: Request, body: RunIn) -> RunOut:
    """Applies the policy now (the daily task does the same). Cannot be undone; audited."""
    del body  # its only field is the confirmation
    resources = resources_of(request)
    result, had_policy = await apply_policy(
        resources.db,
        resources.storage,
        ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        trigger="manual",
    )
    if not had_policy:
        raise ApiError(409, "no_policy", "Set a retention policy first")
    return result

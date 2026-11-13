"""Reminders and escalation (docs/automation.md): work that waits too long reaches someone.

Run by the beat task every hour (`workmind.escalate_work`), per tenant, with the tenant's SLAs
(policy.py, Admin > Automation):

    approval pending, `remind_before_hours` before it expires   -> its approver is reminded
    approval pending `escalate_after_hours` after it was asked  -> a copy for the escalation
        target: the approver's backup approver, else their department's managers, else the
        org admins (starting where the policy says); the first decision answers for all
    expiry (expire_due in approvals.py)                         -> still the last step
    task due within `task_remind_before_hours`                  -> its assignee is reminded
    task `task_escalate_after_hours` past its due date          -> the assignee's escalation
        target is told (people who can open the task, else the queue's managers)
    queue task unclaimed for `queue_escalate_after_hours`       -> the queue's managers
        (a request waiting in triage: the org admins)

Idempotent: each step stamps the approval or task (reminded_at, escalated_at) in the same
transaction as its notifications, so it happens once per approval (and so once per stage of a
chain: each stage has its own copies) and once per task, however often the task runs. Rows are
locked with SKIP LOCKED, so two overlapping runs never both act on one.

Not escalated: an approval waiting for the person who asked (they confirm their own
assistant's action: there is nobody to escalate past), an escalated copy itself, and
Knowledge Hub governance requests (they already go to the collection's owner or the org
admins). These are still reminded. Every step is audited.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import User
from workmind.modules.work.approvals import add_copy, earlier_deciders
from workmind.modules.work.approvers import (
    escalation_targets,
    managers_of_department_id,
    org_admins,
    stored_rule,
)
from workmind.modules.work.links import approval_link, task_link
from workmind.modules.work.models import Approval, Task, WorkPreference
from workmind.modules.work.notifications import notify
from workmind.modules.work.policy import EscalationPolicy, load_policy
from workmind.modules.work.visibility import can_see_task

log = structlog.get_logger("workmind.escalation")
OPEN_TASK = ("open", "in_progress")


@dataclass
class EscalationReport:
    approvals_reminded: int = 0
    approvals_escalated: int = 0
    tasks_reminded: int = 0
    tasks_escalated: int = 0


async def run_escalation(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, now: datetime
) -> EscalationReport:
    report = EscalationReport()
    async with tenant_transaction(sessions, tenant_id) as session:
        policy = (await load_policy(session)).escalation
        if not policy.enabled:
            return report
        report.approvals_reminded = await _remind_approvals(session, tenant_id, policy, now)
        report.approvals_escalated = await _escalate_approvals(session, tenant_id, policy, now)
        report.tasks_reminded = await _remind_tasks(session, tenant_id, policy, now)
        report.tasks_escalated = await _escalate_tasks(session, tenant_id, policy, now)
    if any(vars(report).values()):
        log.info("escalation_run", tenant_id=str(tenant_id), **vars(report))
    return report


def _when(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC")


async def _audit(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    details: dict[str, object],
) -> None:
    await record_event(
        session,
        tenant_id=tenant_id,
        actor=Actor.system(),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        details=details,
    )


# --- Approvals -------------------------------------------------------------------------------


async def _remind_approvals(
    session: AsyncSession, tenant_id: uuid.UUID, policy: EscalationPolicy, now: datetime
) -> int:
    if not policy.remind_before_hours:
        return 0
    due = (
        await session.scalars(
            select(Approval)
            .where(
                Approval.status == "pending",
                Approval.reminded_at.is_(None),
                Approval.expires_at > now,
                Approval.expires_at <= now + timedelta(hours=policy.remind_before_hours),
            )
            .order_by(Approval.expires_at)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for approval in due:
        approval.reminded_at = now
        await notify(
            session,
            tenant_id,
            approval.approver_id,
            kind="approval.reminder",
            title=f"Reminder: decide by {_when(approval.expires_at)}: {approval.summary[:120]}",
            body="Without a decision it expires, and nothing runs.",
            link=approval_link(approval.id),
        )
        await _audit(
            session,
            tenant_id,
            "approval.reminded",
            "approval",
            approval.id,
            {"approver": str(approval.approver_id), "expires_at": approval.expires_at.isoformat()},
        )
    return len(due)


async def _backup_of(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    preference = await session.get(WorkPreference, user_id)
    return preference.backup_approver_id if preference else None


async def _escalate_approvals(
    session: AsyncSession, tenant_id: uuid.UUID, policy: EscalationPolicy, now: datetime
) -> int:
    if not policy.escalate_after_hours:
        return 0
    due = (
        await session.scalars(
            select(Approval)
            .where(
                Approval.status == "pending",
                Approval.escalated_at.is_(None),
                Approval.escalated_from.is_(None),
                Approval.approver_id != Approval.requested_by,
                Approval.expires_at > now,
                Approval.created_at <= now - timedelta(hours=policy.escalate_after_hours),
                or_(
                    Approval.origin["via"].astext.is_(None),
                    Approval.origin["via"].astext != "governance",
                ),
            )
            .order_by(Approval.created_at)
            .with_for_update(skip_locked=True)
        )
    ).all()
    escalated = 0
    for approval in due:
        approval.escalated_at = now
        request_id = str(approval.origin.get("request_id") or approval.id)
        holders = list(
            (
                await session.scalars(
                    select(Approval.approver_id).where(
                        Approval.origin["request_id"].astext == request_id,
                        Approval.status == "pending",
                    )
                )
            ).all()
        )
        choice, targets = await escalation_targets(
            session,
            approval.approver_id,
            first_choice=policy.escalate_to,
            backup=await _backup_of(session, approval.approver_id),
            # Never the requester, someone who already holds a copy, or someone who approved an
            # earlier stage of this chain (two stages are two people).
            exclude=[approval.requested_by, *holders, *await earlier_deciders(session, approval)],
        )
        details: dict[str, object] = {
            "approver": str(approval.approver_id),
            "after_hours": policy.escalate_after_hours,
            "to": [str(t) for t in targets],
            "via": choice,
        }
        if not targets:
            await _audit(
                session, tenant_id, "approval.escalation_skipped", "approval", approval.id, details
            )
            continue
        if "request_id" not in approval.origin:
            # The copies answer together: the first decision supersedes the others.
            approval.origin = {**approval.origin, "request_id": request_id}
        hours = policy.escalate_after_hours
        for target in targets:
            await add_copy(
                session,
                tenant_id,
                tool=approval.tool,
                arguments=approval.edited_arguments or approval.arguments,
                summary=approval.summary,
                requested_by=approval.requested_by,
                approver_id=target,
                expires_at=approval.expires_at,
                origin=approval.origin,
                rule=stored_rule(approval.approver_rule),
                stage=approval.stage,
                escalated_from=approval.id,
                kind="approval.escalated",
                title=f"Escalated to you after {hours} h: {approval.summary[:120]}",
            )
        await notify(
            session,
            tenant_id,
            approval.approver_id,
            kind="approval.escalated",
            title=f"Also sent to {await _names(session, targets)}: {approval.summary[:120]}",
            body=f"Nobody decided within {hours} hours. You can still decide it.",
            link=approval_link(approval.id),
        )
        await _audit(session, tenant_id, "approval.escalated", "approval", approval.id, details)
        escalated += 1
    return escalated


async def _names(session: AsyncSession, people: list[uuid.UUID]) -> str:
    names = (await session.scalars(select(User.display_name).where(User.id.in_(people)))).all()
    shown = ", ".join(sorted(names)[:3])
    return shown + (f" and {len(names) - 3} more" if len(names) > 3 else "")


# --- Tasks -----------------------------------------------------------------------------------


async def _remind_tasks(
    session: AsyncSession, tenant_id: uuid.UUID, policy: EscalationPolicy, now: datetime
) -> int:
    if not policy.task_remind_before_hours:
        return 0
    due = (
        await session.scalars(
            select(Task)
            .where(
                Task.status.in_(OPEN_TASK),
                Task.assignee_id.is_not(None),
                Task.reminded_at.is_(None),
                Task.due_at.is_not(None),
                Task.due_at > now,
                Task.due_at <= now + timedelta(hours=policy.task_remind_before_hours),
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for task in due:
        assert task.assignee_id is not None  # noqa: S101 (filtered above)
        assert task.due_at is not None  # noqa: S101 (filtered above)
        task.reminded_at = now
        await notify(
            session,
            tenant_id,
            task.assignee_id,
            kind="task.reminder",
            title=f"Due {_when(task.due_at)}: {task.title[:150]}",
            link=task_link(task.id),
        )
        await _audit(
            session,
            tenant_id,
            "task.reminded",
            "task",
            task.id,
            {"assignee": str(task.assignee_id), "due_at": task.due_at.isoformat()},
        )
    return len(due)


async def _who_can_see(
    session: AsyncSession, task: Task, people: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Only people who can open the task get a notification about it."""
    found: list[uuid.UUID] = []
    for person in people:
        user = await session.get(User, person)
        if user is not None and await can_see_task(session, person, user.org_role, task):
            found.append(person)
    return found


async def _task_targets(
    session: AsyncSession, task: Task, policy: EscalationPolicy
) -> tuple[str, list[uuid.UUID]]:
    if task.assignee_id is not None:
        choice, people = await escalation_targets(
            session,
            task.assignee_id,
            first_choice=policy.escalate_to,
            backup=await _backup_of(session, task.assignee_id),
            exclude=[],
        )
        visible = await _who_can_see(session, task, people)
        if visible:
            return str(choice), visible
    if task.department_id is not None:
        managers = await managers_of_department_id(session, task.department_id)
        visible = [m for m in managers if m != task.assignee_id]
        if visible:
            return "queue_managers", visible
    if task.triage:
        return "org_admins", await org_admins(session)
    if task.created_by is not None and task.created_by != task.assignee_id:
        return "creator", [task.created_by]
    return "nobody", []


async def _escalate_tasks(
    session: AsyncSession, tenant_id: uuid.UUID, policy: EscalationPolicy, now: datetime
) -> int:
    conditions: list[ColumnElement[bool]] = []
    if policy.task_escalate_after_hours:
        conditions.append(Task.due_at <= now - timedelta(hours=policy.task_escalate_after_hours))
    if policy.queue_escalate_after_hours:
        conditions.append(
            (Task.assignee_id.is_(None))
            & (Task.created_at <= now - timedelta(hours=policy.queue_escalate_after_hours))
            & ((Task.department_id.is_not(None)) | Task.triage.is_(True))
        )
    if not conditions:
        return 0
    due = (
        await session.scalars(
            select(Task)
            .where(Task.status.in_(OPEN_TASK), Task.escalated_at.is_(None), or_(*conditions))
            .with_for_update(skip_locked=True)
        )
    ).all()
    escalated = 0
    for task in due:
        task.escalated_at = now
        overdue = task.due_at is not None and task.due_at <= now
        via, targets = await _task_targets(session, task, policy)
        details: dict[str, object] = {"to": [str(t) for t in targets], "via": via}
        if not targets:
            await _audit(session, tenant_id, "task.escalation_skipped", "task", task.id, details)
            continue
        if overdue and task.due_at is not None:
            title = f"Overdue since {_when(task.due_at)}: {task.title[:150]}"
            details["reason"] = "overdue"
        else:
            title = f"Nobody has picked this up yet: {task.title[:150]}"
            details["reason"] = "unclaimed"
        for target in targets:
            await notify(
                session,
                tenant_id,
                target,
                kind="task.escalated",
                title=title,
                link=task_link(task.id),
            )
        await _audit(session, tenant_id, "task.escalated", "task", task.id, details)
        escalated += 1
    return escalated

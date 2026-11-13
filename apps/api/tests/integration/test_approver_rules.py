"""Approver rules and escalation with the Acme people (docs/automation.md): org admins,
department managers, any-of groups and chains; never the person who asked; reminders and
escalations once per approval (stage); expiry stays the last step.

Needs the test database and TEST_VALKEY_URL."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select, update

from tests.integration.conftest import Acme, SessionFactory
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.models import AuditEvent
from workmind.modules.identity.models import User
from workmind.modules.tools.registry import Tool, ToolRegistry, ToolResult
from workmind.modules.work.approvals import decide, expire_due, propose
from workmind.modules.work.approvers import RuleError, parse_rule
from workmind.modules.work.escalation import run_escalation
from workmind.modules.work.models import (
    Approval,
    AutomationSettings,
    Notification,
    Task,
    WorkPreference,
)


class Mail(BaseModel):
    to: str
    body: str


def outbox(sent: list[dict[str, Any]]) -> ToolRegistry:
    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        sent.append({"as": ctx.user_id, **args.model_dump()})
        return ToolResult("sent")

    registry = ToolRegistry()
    registry.register(Tool("mail", "Send mail", Mail, "external", "assistant:ask", run))
    return registry


def person(acme: Acme, name: str, role: str = "member") -> RequestContext:
    return RequestContext(
        request_id="t",
        tenant_id=acme.tenant_id,
        user_id=acme.users[name],
        org_role=role,
        principals=frozenset(),
        auth_method="session",
        scopes=frozenset({"*"}),
    )


async def ask(
    sessions: SessionFactory, acme: Acme, requester: str, rule: Any, **origin: Any
) -> Approval:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return await propose(
            session,
            person(acme, requester),
            tool="mail",
            arguments={"to": "board@acme.test", "body": "Q4 budget"},
            summary="Send the Q4 budget to the board",
            rule=parse_rule(rule) if rule is not None else None,
            origin=origin or None,
        )


async def copies(sessions: SessionFactory, acme: Acme, first: Approval) -> list[Approval]:
    request_id = first.origin.get("request_id")
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        query = select(Approval).order_by(Approval.created_at)
        if request_id:
            query = query.where(Approval.origin["request_id"].astext == request_id)
        else:
            query = query.where(Approval.id == first.id)
        return list((await session.scalars(query)).all())


def names(acme: Acme, ids: list[uuid.UUID]) -> list[str]:
    by_id = {v: k for k, v in acme.users.items()}
    return sorted(by_id[i] for i in ids)


async def told(sessions: SessionFactory, acme: Acme, who: str, kind: str) -> list[Notification]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(Notification).where(
                        Notification.user_id == acme.users[who], Notification.kind == kind
                    )
                )
            ).all()
        )


# --- Rules -------------------------------------------------------------------------------------


async def test_org_admins_and_department_managers_never_include_the_requester(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    by_admin = await ask(app_sessions, acme, "erin", {"role": "org_admins"})
    assert names(acme, [a.approver_id for a in await copies(app_sessions, acme, by_admin)]) == [
        "priya.raman"
    ]
    by_bob = await ask(app_sessions, acme, "bob", {"department_manager": "requester"})
    assert names(acme, [a.approver_id for a in await copies(app_sessions, acme, by_bob)]) == [
        "marcus.bell"
    ]
    finance = await ask(app_sessions, acme, "bob", {"department_manager": "finance"})
    assert [a.approver_id for a in await copies(app_sessions, acme, finance)] == [
        acme.users["hannah.schmidt"]
    ]
    # Alice manages HR: her department's manager is herself, so nobody else can approve.
    with pytest.raises(RuleError, match="Nobody can approve"):
        await ask(app_sessions, acme, "alice", {"department_manager": "requester"})
    with pytest.raises(RuleError, match="No colleague"):
        await ask(app_sessions, acme, "bob", "nobody@acme.test")
    with pytest.raises(RuleError, match="No department"):
        await ask(app_sessions, acme, "bob", {"department_manager": "legal"})
    # Guests never approve.
    with pytest.raises(RuleError, match="Nobody can approve"):
        await ask(app_sessions, acme, "bob", "mallory@acme.test")


async def test_any_of_a_group_decides_once_and_the_others_are_superseded(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    sent: list[dict[str, Any]] = []
    first = await ask(
        app_sessions,
        acme,
        "carol",
        {"any_of": ["hannah.schmidt@acme.test", "priya.raman@acme.test", "carol@acme.test"]},
    )
    group = await copies(app_sessions, acme, first)
    assert names(acme, [a.approver_id for a in group]) == ["hannah.schmidt", "priya.raman"]
    priyas = next(a for a in group if a.approver_id == acme.users["priya.raman"])

    decided = await decide(
        app_sessions, outbox(sent), person(acme, "priya.raman"), priyas.id, decision="approve"
    )
    assert decided.status == "executed"
    assert sent == [{"as": acme.users["carol"], "to": "board@acme.test", "body": "Q4 budget"}]
    hannahs = next(a for a in await copies(app_sessions, acme, first) if a.id != priyas.id)
    assert (hannahs.status, hannahs.result) == ("superseded", {"decided_in": str(priyas.id)})
    with pytest.raises(ApiError) as late:
        await decide(
            app_sessions,
            outbox(sent),
            person(acme, "hannah.schmidt"),
            hannahs.id,
            decision="approve",
        )
    assert late.value.status == 409
    assert len(sent) == 1


async def test_a_chain_approves_in_order_with_edits_and_runs_once_at_the_end(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    sent: list[dict[str, Any]] = []
    rule = {"chain": [{"department_manager": "requester"}, "hannah.schmidt@acme.test"]}
    first = await ask(app_sessions, acme, "bob", rule)
    assert (first.approver_id, first.stage) == (acme.users["marcus.bell"], 0)
    # Hannah cannot decide before her stage starts.
    with pytest.raises(ApiError):
        await decide(
            app_sessions, outbox(sent), person(acme, "hannah.schmidt"), first.id, decision="approve"
        )

    forwarded = await decide(
        app_sessions,
        outbox(sent),
        person(acme, "marcus.bell"),
        first.id,
        decision="approve",
        edited_arguments={"to": "board@acme.test", "body": "Q4 budget, checked by Marcus"},
    )
    assert forwarded.status == "forwarded"
    assert sent == []  # nothing runs half-approved
    assert forwarded.result is not None
    assert forwarded.result["stage"] == 2
    (second_id,) = forwarded.result["next"]
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        second = await session.get(Approval, uuid.UUID(second_id))
    assert second is not None
    assert (second.approver_id, second.stage) == (acme.users["hannah.schmidt"], 1)
    assert second.arguments["body"] == "Q4 budget, checked by Marcus"  # as approved so far
    assert await told(app_sessions, acme, "bob", "approval.forwarded")
    (hannah_told,) = await told(app_sessions, acme, "hannah.schmidt", "approval.requested")
    assert hannah_told.body.startswith("Step 2 of 2")

    done = await decide(
        app_sessions, outbox(sent), person(acme, "hannah.schmidt"), second.id, decision="approve"
    )
    assert done.status == "executed"
    assert sent == [
        {"as": acme.users["bob"], "to": "board@acme.test", "body": "Q4 budget, checked by Marcus"}
    ]
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        actions = [
            (e.action, e.details.get("stage"))
            for e in await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.resource_type == "approval")
                .order_by(AuditEvent.occurred_at)
            )
        ]
    assert ("approval.forwarded", 2) in actions
    assert ("approval.approved", 1) in actions
    assert ("approval.approved", 2) in actions


async def test_a_rejection_at_any_stage_ends_the_chain(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    sent: list[dict[str, Any]] = []
    first = await ask(
        app_sessions, acme, "bob", {"chain": ["marcus.bell@acme.test", "hannah.schmidt@acme.test"]}
    )
    forwarded = await decide(
        app_sessions, outbox(sent), person(acme, "marcus.bell"), first.id, decision="approve"
    )
    assert forwarded.result is not None
    second_id = uuid.UUID(forwarded.result["next"][0])
    rejected = await decide(
        app_sessions, outbox(sent), person(acme, "hannah.schmidt"), second_id, decision="reject"
    )
    assert (rejected.status, sent) == ("rejected", [])


async def test_a_chain_whose_next_stage_lost_its_approvers_fails_instead_of_running(
    app_sessions: SessionFactory, owner_sessions: SessionFactory, acme: Acme
) -> None:
    sent: list[dict[str, Any]] = []
    first = await ask(
        app_sessions, acme, "bob", {"chain": ["marcus.bell@acme.test", "grace.liu@acme.test"]}
    )
    async with tenant_transaction(owner_sessions, acme.tenant_id) as session:
        await session.execute(
            update(User).where(User.id == acme.users["grace.liu"]).values(status="disabled")
        )
    decided = await decide(
        app_sessions, outbox(sent), person(acme, "marcus.bell"), first.id, decision="approve"
    )
    assert decided.status == "failed"
    assert decided.result is not None
    assert "nobody can approve step 2" in decided.result["error"]
    assert sent == []
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        stored = await session.get(Approval, first.id)
    assert stored is not None
    assert stored.status == "failed"


async def test_one_person_never_approves_two_stages_of_a_chain(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    # The docs' own example: Hannah manages Finance and is on the second stage's list, so she
    # used to get a stage-2 copy after approving stage 1 and run the action alone.
    sent: list[dict[str, Any]] = []
    rule = {
        "chain": [
            {"department_manager": "requester"},
            {"any_of": ["hannah.schmidt@acme.test", "priya.raman@acme.test"]},
        ]
    }
    first = await ask(app_sessions, acme, "carol", rule)
    assert first.approver_id == acme.users["hannah.schmidt"]
    forwarded = await decide(
        app_sessions, outbox(sent), person(acme, "hannah.schmidt"), first.id, decision="approve"
    )
    assert forwarded.status == "forwarded"
    assert forwarded.result is not None
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        second = [await session.get(Approval, uuid.UUID(i)) for i in forwarded.result["next"]]
    assert names(acme, [a.approver_id for a in second if a is not None]) == ["priya.raman"]

    # A copy for an earlier decider (none is issued; e.g. one made before this rule) is refused.
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        assert second[0] is not None
        planted = Approval(
            tenant_id=acme.tenant_id,
            tool="mail",
            arguments=second[0].arguments,
            summary=second[0].summary,
            requested_by=acme.users["carol"],
            approver_id=acme.users["hannah.schmidt"],
            expires_at=second[0].expires_at,
            origin=dict(second[0].origin),
            approver_rule=second[0].approver_rule,
            stage=1,
        )
        session.add(planted)
    with pytest.raises(ApiError) as refused:
        await decide(
            app_sessions,
            outbox(sent),
            person(acme, "hannah.schmidt"),
            planted.id,
            decision="approve",
        )
    assert (refused.value.status, sent) == (403, [])

    assert second[0] is not None
    done = await decide(
        app_sessions, outbox(sent), person(acme, "priya.raman"), second[0].id, decision="approve"
    )
    assert (done.status, len(sent)) == ("executed", 1)


async def test_a_chain_that_needs_one_person_twice_is_refused_or_fails(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    sent: list[dict[str, Any]] = []
    with pytest.raises(RuleError, match="different person"):
        await ask(
            app_sessions,
            acme,
            "bob",
            {"chain": ["hannah.schmidt@acme.test", {"department_manager": "finance"}]},
        )
    # Possible when proposed (Priya, then Hannah), impossible once Hannah took stage 1.
    first = await ask(
        app_sessions,
        acme,
        "carol",
        {
            "chain": [
                {"any_of": ["hannah.schmidt@acme.test", "priya.raman@acme.test"]},
                "hannah.schmidt@acme.test",
            ]
        },
    )
    hannahs = next(
        a
        for a in await copies(app_sessions, acme, first)
        if a.approver_id == acme.users["hannah.schmidt"]
    )
    decided = await decide(
        app_sessions, outbox(sent), person(acme, "hannah.schmidt"), hannahs.id, decision="approve"
    )
    assert decided.status == "failed"
    assert decided.result is not None
    assert "only people who approved an earlier step" in decided.result["error"]
    assert sent == []


# --- Escalation --------------------------------------------------------------------------------


async def age(sessions: SessionFactory, acme: Acme, approval: Approval, hours: float) -> None:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        await session.execute(
            update(Approval)
            .where(Approval.id == approval.id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=hours))
        )


async def backup(sessions: SessionFactory, acme: Acme, who: str, backup_name: str) -> None:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        session.add(
            WorkPreference(
                tenant_id=acme.tenant_id,
                user_id=acme.users[who],
                backup_approver_id=acme.users[backup_name],
            )
        )


async def test_an_approval_nobody_decides_goes_to_the_backup_approver_once(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    sent: list[dict[str, Any]] = []
    await backup(app_sessions, acme, "olivia.grant", "ivan.petrov")
    waiting = await ask(app_sessions, acme, "bob", "olivia.grant@acme.test")
    fresh = await ask(app_sessions, acme, "carol", "olivia.grant@acme.test")
    await age(app_sessions, acme, waiting, 25)

    now = datetime.now(UTC)
    report = await run_escalation(app_sessions, acme.tenant_id, now)
    again = await run_escalation(app_sessions, acme.tenant_id, now + timedelta(hours=1))

    assert (report.approvals_escalated, again.approvals_escalated) == (1, 0)
    group = await copies(app_sessions, acme, await reload(app_sessions, acme, waiting))
    assert names(acme, [a.approver_id for a in group]) == ["ivan.petrov", "olivia.grant"]
    ivans = next(a for a in group if a.approver_id == acme.users["ivan.petrov"])
    assert (ivans.escalated_from, ivans.expires_at) == (waiting.id, waiting.expires_at)
    assert await told(app_sessions, acme, "ivan.petrov", "approval.escalated")
    assert await told(app_sessions, acme, "olivia.grant", "approval.escalated")
    assert len(await copies(app_sessions, acme, fresh)) == 1  # not due yet

    # The backup decides; Olivia's copy is superseded; the action runs once.
    await decide(
        app_sessions, outbox(sent), person(acme, "ivan.petrov"), ivans.id, decision="approve"
    )
    assert len(sent) == 1
    original = await reload(app_sessions, acme, waiting)
    assert original.status == "superseded"
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        audit = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "approval.escalated")
            )
        ).one()
    assert audit.details["via"] == "backup"
    assert audit.details["to"] == [str(acme.users["ivan.petrov"])]


async def reload(sessions: SessionFactory, acme: Acme, approval: Approval) -> Approval:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        stored = await session.get(Approval, approval.id)
    assert stored is not None
    return stored


async def test_without_a_backup_the_managers_then_the_org_admins_get_it(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    to_carol = await ask(app_sessions, acme, "bob", "carol@acme.test")  # Finance: Hannah manages
    to_olivia = await ask(app_sessions, acme, "bob", "olivia.grant@acme.test")  # she manages Ops
    for approval in (to_carol, to_olivia):
        await age(app_sessions, acme, approval, 30)

    await run_escalation(app_sessions, acme.tenant_id, datetime.now(UTC))

    carol_group = await copies(app_sessions, acme, await reload(app_sessions, acme, to_carol))
    assert names(acme, [a.approver_id for a in carol_group]) == ["carol", "hannah.schmidt"]
    olivia_group = await copies(app_sessions, acme, await reload(app_sessions, acme, to_olivia))
    assert names(acme, [a.approver_id for a in olivia_group]) == [
        "erin",
        "olivia.grant",
        "priya.raman",
    ]


async def test_reminders_come_once_and_self_confirmations_and_governance_are_not_escalated(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    own = await ask(app_sessions, acme, "bob", None)  # Bob confirms his own assistant's action
    governance = await ask(app_sessions, acme, "bob", "olivia.grant@acme.test", via="governance")
    soon = await ask(app_sessions, acme, "carol", "hannah.schmidt@acme.test")
    for approval in (own, governance):
        await age(app_sessions, acme, approval, 60)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Approval)
            .where(Approval.id == soon.id)
            .values(expires_at=datetime.now(UTC) + timedelta(hours=3))
        )

    first = await run_escalation(app_sessions, acme.tenant_id, datetime.now(UTC))
    second = await run_escalation(app_sessions, acme.tenant_id, datetime.now(UTC))

    assert first.approvals_escalated == 0
    assert (first.approvals_reminded, second.approvals_reminded) == (1, 0)
    (reminder,) = await told(app_sessions, acme, "hannah.schmidt", "approval.reminder")
    assert reminder.title.startswith("Reminder: decide by")
    assert len(await copies(app_sessions, acme, own)) == 1
    assert len(await copies(app_sessions, acme, governance)) == 1

    # Expiry is still the last step: nothing runs.
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Approval)
            .where(Approval.id == soon.id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    assert soon.id in [a.id for a in await expire_due(app_sessions, acme.tenant_id)]


async def test_escalation_follows_the_tenants_policy(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        session.add(
            AutomationSettings(
                tenant_id=acme.tenant_id,
                escalation={"escalate_after_hours": 72, "escalate_to": "org_admins"},
            )
        )
    approval = await ask(app_sessions, acme, "bob", "carol@acme.test")
    await age(app_sessions, acme, approval, 30)
    assert (
        await run_escalation(app_sessions, acme.tenant_id, datetime.now(UTC))
    ).approvals_escalated == 0
    await age(app_sessions, acme, approval, 73)
    await run_escalation(app_sessions, acme.tenant_id, datetime.now(UTC))
    group = await copies(app_sessions, acme, await reload(app_sessions, acme, approval))
    assert names(acme, [a.approver_id for a in group]) == ["carol", "erin", "priya.raman"]

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(update(AutomationSettings).values(escalation={"enabled": False}))
    other = await ask(app_sessions, acme, "dan", "carol@acme.test")
    await age(app_sessions, acme, other, 100)
    report = await run_escalation(app_sessions, acme.tenant_id, datetime.now(UTC))
    assert report.approvals_escalated == 0
    assert report.approvals_reminded == 0


# --- Tasks -------------------------------------------------------------------------------------


async def add_task(sessions: SessionFactory, acme: Acme, **fields: Any) -> Task:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        task = Task(tenant_id=acme.tenant_id, **fields)
        session.add(task)
        await session.flush()
        return task


async def department(sessions: SessionFactory, acme: Acme, slug: str) -> uuid.UUID:
    from workmind.modules.identity.models import Department

    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return (await session.scalars(select(Department.id).where(Department.slug == slug))).one()


async def test_tasks_are_reminded_before_they_are_due_and_escalated_when_overdue_or_unclaimed(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    now = datetime.now(UTC)
    finance = await department(app_sessions, acme, "finance")
    due_soon = await add_task(
        app_sessions,
        acme,
        title="Close the books",
        assignee_id=acme.users["carol"],
        created_by=acme.users["hannah.schmidt"],
        department_id=finance,
        due_at=now + timedelta(hours=5),
    )
    overdue = await add_task(
        app_sessions,
        acme,
        title="Reconcile the cards",
        assignee_id=acme.users["noah.fischer"],
        created_by=acme.users["noah.fischer"],
        department_id=finance,
        due_at=now - timedelta(days=2),
    )
    unclaimed = await add_task(
        app_sessions,
        acme,
        title="Vendor refund",
        department_id=finance,
        created_by=acme.users["bob"],
    )
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        stored = await session.get(Task, unclaimed.id)
        assert stored is not None
        stored.created_at = now - timedelta(days=3)

    first = await run_escalation(app_sessions, acme.tenant_id, now)
    second = await run_escalation(app_sessions, acme.tenant_id, now)

    assert (first.tasks_reminded, first.tasks_escalated) == (1, 2)
    assert (second.tasks_reminded, second.tasks_escalated) == (0, 0)
    (reminder,) = await told(app_sessions, acme, "carol", "task.reminder")
    assert reminder.link == f"/tasks/{due_soon.id}"
    escalated = await told(app_sessions, acme, "hannah.schmidt", "task.escalated")
    assert sorted(n.link or "" for n in escalated) == sorted(
        [f"/tasks/{overdue.id}", f"/tasks/{unclaimed.id}"]
    )
    assert any(n.title.startswith("Overdue since") for n in escalated)
    assert any(n.title.startswith("Nobody has picked this up yet") for n in escalated)


async def test_an_escalated_copy_never_gives_a_later_stage_to_its_decider_or_a_shorter_window(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    # Bob's chain: his manager (Marcus), then an org admin. Stage 1 escalates to the org admins
    # (Marcus has no backup and manages his department alone); Erin approves that copy. Stage 2
    # used to go to Erin again, with the 42 hours stage 1 had left instead of the 72 h TTL.
    sent: list[dict[str, Any]] = []
    first = await ask(
        app_sessions,
        acme,
        "bob",
        {"chain": [{"department_manager": "requester"}, {"role": "org_admins"}]},
    )
    now = datetime.now(UTC)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Approval)
            .where(Approval.id == first.id)
            .values(created_at=now - timedelta(hours=30), expires_at=now + timedelta(hours=42))
        )
    await run_escalation(app_sessions, acme.tenant_id, now)
    stage_one = await copies(app_sessions, acme, await reload(app_sessions, acme, first))
    assert "erin" in names(acme, [a.approver_id for a in stage_one])
    erins = next(a for a in stage_one if a.approver_id == acme.users["erin"])

    forwarded = await decide(
        app_sessions, outbox(sent), person(acme, "erin", "admin"), erins.id, decision="approve"
    )
    assert forwarded.status == "forwarded"
    assert forwarded.result is not None
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        second = [await session.get(Approval, uuid.UUID(i)) for i in forwarded.result["next"]]
    assert names(acme, [a.approver_id for a in second if a is not None]) == ["priya.raman"]
    stage_two_copy = second[0]
    assert stage_two_copy is not None
    window = stage_two_copy.expires_at - datetime.now(UTC)
    assert timedelta(hours=71) < window <= timedelta(hours=72)

    # Escalating stage 2 never hands it back to Erin either.
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Approval)
            .where(Approval.id == stage_two_copy.id)
            .values(created_at=now - timedelta(hours=30))
        )
    await run_escalation(app_sessions, acme.tenant_id, datetime.now(UTC))
    stage_two = await copies(app_sessions, acme, await reload(app_sessions, acme, stage_two_copy))
    assert "erin" not in names(acme, [a.approver_id for a in stage_two])


async def test_an_edit_changes_the_summary_that_describes_the_action(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    # The card's header and the inbox kept the proposal's subject after the approver edited it.
    sent: list[dict[str, Any]] = []
    registry = outbox(sent)
    base = registry.get("mail")
    assert base is not None
    registry = ToolRegistry()
    registry.register(
        Tool(
            "mail",
            "Send mail",
            Mail,
            "external",
            "assistant:ask",
            base.handler,
            summarize=lambda a: f"Send an email to {a['to']}: {a['body']}",
        )
    )
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        first = await propose(
            session,
            person(acme, "bob"),
            tool="mail",
            arguments={"to": "board@acme.test", "body": "Q4 budget"},
            summary="Weekly report: Send an email to board@acme.test: Q4 budget",
            rule=parse_rule("marcus.bell@acme.test"),
        )
    done = await decide(
        app_sessions,
        registry,
        person(acme, "marcus.bell"),
        first.id,
        decision="approve",
        edited_arguments={"to": "board@acme.test", "body": "Q4 budget (checked)"},
    )
    assert done.status == "executed"
    assert done.summary == "Weekly report: Send an email to board@acme.test: Q4 budget (checked)"
    assert (await reload(app_sessions, acme, first)).summary == done.summary

"""Workflows end to end with real people: research, pause at the side effect, an edited
approval, the email sent, the run finished, everything audited. The model is scripted and mail
is captured. Needs the test database and TEST_VALKEY_URL."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from tests.integration.conftest import Acme, SessionFactory
from workmind.core.context import RequestContext
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.models import AuditEvent
from workmind.modules.tools.registry import ToolRegistry
from workmind.modules.work.approvals import decide
from workmind.modules.work.models import Approval, AutomationSettings, Workflow, WorkflowRun
from workmind.modules.work.tools import send_email_tool
from workmind.modules.work.workflows import WorkflowDeps, render, resume_after, start_run
from workmind.providers.email import SentEmail
from workmind.providers.llm import (
    LLMRequest,
    LLMUsage,
    Role,
    ToolExecutor,
    ToolLoopResult,
    ToolSpec,
)

REPORT = {
    "trigger": {"schedule": "0 8 * * MON"},
    "steps": [
        {"id": "gather", "research": "Summarize this week's operations."},
        {
            "id": "send",
            "tool": "send_email",
            "arguments": {
                "to": ["leadership@acme.test"],
                "subject": "Weekly ops report {{ today }}",
                "body": "{{ steps.gather.text }}",
            },
            "approver": "priya.raman@acme.test",
        },
    ],
}


class CapturingMailer:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self, to: Sequence[str], subject: str, body: str, *, cc: Sequence[str] = ()
    ) -> SentEmail:
        self.sent.append({"to": list(to), "subject": subject, "body": body})
        return SentEmail("<id@acme.test>", list(to))


class ReportModel:
    external = True

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult:
        return ToolLoopResult(
            text="No incidents. 42 robots shipped.",
            usage=[LLMUsage(model="fake", input_tokens=10, output_tokens=5)],
        )


def person(acme: Acme, name: str) -> RequestContext:
    return RequestContext(
        request_id="t",
        tenant_id=acme.tenant_id,
        user_id=acme.users[name],
        org_role="member",
        principals=frozenset(),
        auth_method="session",
        scopes=frozenset({"*"}),
    )


async def setup(
    sessions: SessionFactory, acme: Acme
) -> tuple[WorkflowDeps, CapturingMailer, Workflow]:
    mailer = CapturingMailer()
    tools = ToolRegistry()
    tools.register(send_email_tool(mailer, sessions))  # type: ignore[arg-type]
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        workflow = Workflow(
            tenant_id=acme.tenant_id,
            key="weekly-ops",
            name="Weekly ops report",
            owner_id=acme.users["olivia.grant"],
            definition=REPORT,
        )
        session.add(workflow)
        await session.flush()
    return WorkflowDeps(sessions, ReportModel(), tools), mailer, workflow  # type: ignore[arg-type]


async def test_research_then_an_edited_approval_sends_the_report(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    deps, mailer, workflow = await setup(app_sessions, acme)

    paused = await start_run(deps, acme.tenant_id, workflow.id, trigger="schedule")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        approval = (await session.scalars(select(Approval))).one()
    assert paused.status == "waiting_approval"
    assert mailer.sent == []  # nothing leaves before a person decides
    assert approval.approver_id == acme.users["priya.raman"]
    assert approval.arguments["body"] == "No incidents. 42 robots shipped."
    assert approval.summary.startswith("Weekly ops report: Send an email to leadership@acme.test")

    edited = {**approval.arguments, "body": "No incidents. 42 robots shipped. Great week. Priya"}
    await decide(
        app_sessions,
        deps.tools,
        person(acme, "priya.raman"),
        approval.id,
        decision="approve",
        edited_arguments=edited,
        on_decided=lambda a: resume_after(deps, a),
    )

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        run = await session.get(WorkflowRun, paused.id)
        actions = [
            e.action for e in await session.scalars(select(AuditEvent).order_by(AuditEvent.seq))
        ]
    assert run is not None
    print("RUN", run.status, run.steps, run.error)
    assert (run.status, run.steps["send"]["status"]) == ("succeeded", "done")
    assert mailer.sent == [
        {
            "to": ["leadership@acme.test"],
            "subject": edited["subject"],
            "body": "No incidents. 42 robots shipped. Great week. Priya",
        }
    ]
    for action in (
        "approval.requested",
        "approval.approved",
        "tool.called",
        "email.sent",
        "workflow.succeeded",
    ):
        assert action in actions


async def test_a_rejected_step_cancels_the_run_and_sends_nothing(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    deps, mailer, workflow = await setup(app_sessions, acme)
    paused = await start_run(deps, acme.tenant_id, workflow.id, trigger="manual")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        approval = (await session.scalars(select(Approval))).one()

    await decide(
        app_sessions,
        deps.tools,
        person(acme, "priya.raman"),
        approval.id,
        decision="reject",
        on_decided=lambda a: resume_after(deps, a),
    )

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        run = await session.get(WorkflowRun, paused.id)
    assert run is not None
    assert run.status == "cancelled"
    assert mailer.sent == []


def test_templates_keep_types_for_whole_values_and_fill_text() -> None:
    context = {
        "steps": {"draft": {"data": {"to": ["a@x.test", "b@x.test"]}, "text": "Hi"}},
        "today": "2026-11-09",
    }

    assert render("{{ steps.draft.data.to }}", context) == ["a@x.test", "b@x.test"]
    assert render("Report {{ today }}: {{ steps.draft.text }}", context) == "Report 2026-11-09: Hi"
    assert render({"x": ["{{ missing.path }}"]}, context) == {"x": [""]}


async def test_an_invalid_action_fails_the_run_before_anyone_is_asked(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    deps, _, workflow = await setup(app_sessions, acme)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        stored = await session.get(Workflow, workflow.id)
        assert stored is not None
        broken = dict(REPORT)
        broken["steps"] = [
            REPORT["steps"][0],
            {
                **REPORT["steps"][1],
                "arguments": {"to": ["not-an-address"], "subject": "x", "body": "y"},
            },
        ]
        stored.definition = broken

    run = await start_run(deps, acme.tenant_id, workflow.id, trigger="manual")

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        approvals = (await session.scalars(select(Approval))).all()
    assert run.status == "failed"
    assert approvals == []  # nobody was asked to approve something that cannot run


async def use_definition(
    sessions: SessionFactory, acme: Acme, workflow: Workflow, owner: str, send: dict[str, Any]
) -> None:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        stored = await session.get(Workflow, workflow.id)
        assert stored is not None
        stored.owner_id = acme.users[owner]
        stored.definition = {**REPORT, "steps": [REPORT["steps"][0], send]}


async def test_a_chain_approved_step_runs_after_its_last_stage_and_resumes_the_run(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    deps, mailer, workflow = await setup(app_sessions, acme)
    chain = {"chain": [{"department_manager": "requester"}, "priya.raman@acme.test"]}
    await use_definition(
        app_sessions, acme, workflow, "erin", {**REPORT["steps"][1], "approver": chain}
    )

    paused = await start_run(deps, acme.tenant_id, workflow.id, trigger="manual")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        first = (await session.scalars(select(Approval))).one()
    assert (paused.status, first.approver_id) == ("waiting_approval", acme.users["olivia.grant"])

    forwarded = await decide(
        app_sessions,
        deps.tools,
        person(acme, "olivia.grant"),
        first.id,
        decision="approve",
        on_decided=lambda a: resume_after(deps, a),
    )
    assert forwarded.status == "forwarded"
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        run = await session.get(WorkflowRun, paused.id)
        second = (await session.scalars(select(Approval).where(Approval.stage == 1))).one()
    assert run is not None
    assert run.status == "waiting_approval"
    assert mailer.sent == []
    assert second.origin["workflow_run_id"] == str(paused.id)

    await decide(
        app_sessions,
        deps.tools,
        person(acme, "priya.raman"),
        second.id,
        decision="approve",
        on_decided=lambda a: resume_after(deps, a),
    )
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        run = await session.get(WorkflowRun, paused.id)
    assert run is not None
    assert run.status == "succeeded"
    assert len(mailer.sent) == 1


async def test_a_step_nobody_else_can_approve_fails_and_the_tenant_rule_is_the_default(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    deps, mailer, workflow = await setup(app_sessions, acme)
    # Olivia manages Operations: "her department's manager" is nobody but herself.
    send = {**REPORT["steps"][1], "approver": {"department_manager": "requester"}}
    await use_definition(app_sessions, acme, workflow, "olivia.grant", send)
    failed = await start_run(deps, acme.tenant_id, workflow.id, trigger="manual")
    assert failed.status == "failed"
    assert failed.error is not None
    assert "Nobody can approve" in failed.error

    # Without an approver of its own, the step uses the tenant's rule for the tool.
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        session.add(
            AutomationSettings(
                tenant_id=acme.tenant_id, approver_rules={"send_email": {"role": "org_admins"}}
            )
        )
    plain = {k: v for k, v in REPORT["steps"][1].items() if k != "approver"}
    await use_definition(app_sessions, acme, workflow, "erin", plain)
    paused = await start_run(deps, acme.tenant_id, workflow.id, trigger="manual")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        approvers = (
            await session.scalars(
                select(Approval.approver_id).where(
                    Approval.origin["workflow_run_id"].astext == str(paused.id)
                )
            )
        ).all()
    assert paused.status == "waiting_approval"
    assert approvers == [acme.users["priya.raman"]]  # the org admins, but never Erin herself
    assert mailer.sent == []

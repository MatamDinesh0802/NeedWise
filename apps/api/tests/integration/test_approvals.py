"""Approvals with real people: the proposed action runs once, as edited, as the requester, and
only if the approver approves in time. Needs the test database and TEST_VALKEY_URL."""

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
from workmind.modules.identity.models import User
from workmind.modules.tools.registry import Tool, ToolRegistry, ToolResult
from workmind.modules.work.approvals import decide, expire_due, pending_for, propose
from workmind.modules.work.models import Approval, Notification


class Mail(BaseModel):
    to: str
    body: str


def outbox_tool(sent: list[dict[str, Any]]) -> ToolRegistry:
    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        sent.append({"as": ctx.user_id, **args.model_dump()})
        return ToolResult("sent")

    registry = ToolRegistry()
    registry.register(Tool("mail", "Send mail", Mail, "external", "assistant:ask", run))
    return registry


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


async def proposal(sessions: SessionFactory, acme: Acme, approver: str | None = None) -> Approval:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return await propose(
            session,
            person(acme, "alice"),
            tool="mail",
            arguments={"to": "leadership@acme.test", "body": "Draft report"},
            summary="Send the weekly report to leadership",
            approver_id=acme.users[approver] if approver else None,
        )


async def test_an_approved_action_runs_once_as_edited_and_as_the_requester(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    sent: list[dict[str, Any]] = []
    tools = outbox_tool(sent)
    approval = await proposal(app_sessions, acme, approver="priya.raman")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        waiting = await pending_for(session, acme.users["priya.raman"])
        priya_told = (
            await session.scalars(
                select(Notification).where(Notification.user_id == acme.users["priya.raman"])
            )
        ).all()

    with pytest.raises(ApiError) as not_hers:
        await decide(app_sessions, tools, person(acme, "bob"), approval.id, decision="approve")
    decided = await decide(
        app_sessions,
        tools,
        person(acme, "priya.raman"),
        approval.id,
        decision="approve",
        edited_arguments={"to": "leadership@acme.test", "body": "Final report, edited by Priya"},
        note="Shortened",
    )
    with pytest.raises(ApiError) as twice:
        await decide(
            app_sessions, tools, person(acme, "priya.raman"), approval.id, decision="approve"
        )

    assert [a.id for a in waiting] == [approval.id]
    assert priya_told[0].kind == "approval.requested"
    assert not_hers.value.status == 404  # only the named approver may decide
    assert decided.status == "executed"
    assert sent == [
        {
            "as": acme.users["alice"],
            "to": "leadership@acme.test",
            "body": "Final report, edited by Priya",
        }
    ]
    assert twice.value.status == 409  # never runs twice


async def test_rejected_and_expired_actions_never_run(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    sent: list[dict[str, Any]] = []
    tools = outbox_tool(sent)
    rejected = await proposal(app_sessions, acme)
    late = await proposal(app_sessions, acme)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Approval)
            .where(Approval.id == late.id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )

    await decide(app_sessions, tools, person(acme, "alice"), rejected.id, decision="reject")
    expired = await expire_due(app_sessions, acme.tenant_id)
    with pytest.raises(ApiError) as too_late:
        await decide(app_sessions, tools, person(acme, "alice"), late.id, decision="approve")

    assert sent == []
    assert [a.id for a in expired] == [late.id]
    assert too_late.value.status == 409


async def test_a_disabled_requesters_action_does_not_run(
    app_sessions: SessionFactory, owner_sessions: SessionFactory, acme: Acme
) -> None:
    sent: list[dict[str, Any]] = []
    approval = await proposal(app_sessions, acme, approver="priya.raman")
    async with tenant_transaction(owner_sessions, acme.tenant_id) as session:
        await session.execute(
            update(User).where(User.id == acme.users["alice"]).values(status="disabled")
        )

    decided = await decide(
        app_sessions,
        outbox_tool(sent),
        person(acme, "priya.raman"),
        approval.id,
        decision="approve",
    )

    assert (decided.status, decided.result) == (
        "failed",
        {"error": "the requester is no longer active"},
    )
    assert sent == []
    assert uuid.UUID(str(approval.id))

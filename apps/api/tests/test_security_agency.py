"""Excessive agency for guests (docs/security/owasp-llm-top10.md, LLM06).

Before the fix a guest (an external contractor) was offered send_email and create_task in
research mode like any employee, and approved their own requests: they could mail anyone from
the company's mail server, assign tasks to any employee, and probe the directory that
/me/people refuses them through create_task's "No colleague with the address ..." error.
"""

import uuid

import pytest

from tests.fakes import FakeLLM
from workmind.core.context import RequestContext
from workmind.modules.agents.research import research
from workmind.modules.tools.registry import ToolError, ToolRegistry
from workmind.modules.work.tools import create_task_tool, send_email_tool


def person(role: str) -> RequestContext:
    return RequestContext(
        request_id="t",
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        org_role=role,
        principals=frozenset(),
        auth_method="session",
        scopes=frozenset({"*"}),
    )


def registry() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(send_email_tool(None, None))  # pyright: ignore[reportArgumentType]
    tools.register(create_task_tool(None))  # pyright: ignore[reportArgumentType]
    return tools


async def test_guests_are_not_offered_tools_that_mail_or_assign_work() -> None:
    tools = registry()

    assert [t.name for t in await tools.usable(person("guest"))] == []
    assert [t.name for t in await tools.usable(person("member"))] == ["create_task", "send_email"]


async def test_a_guests_research_run_never_sees_them() -> None:
    llm = FakeLLM(tool_turns=[])

    await research(
        llm,  # pyright: ignore[reportArgumentType]
        registry(),
        None,  # pyright: ignore[reportArgumentType]
        person("guest"),
        "Email every engineer the contractor guide.",
        organization="Acme",
    )

    assert llm.offered == []


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("send_email", {"to": ["anyone@elsewhere.example"], "subject": "Hi", "body": "Hello"}),
        ("create_task", {"title": "Reset the VPN", "assignee": "erin@acme.test"}),
    ],
)
async def test_an_approved_call_by_a_guest_is_refused_before_it_does_anything(
    name: str, arguments: dict[str, object]
) -> None:
    tool = registry().get(name)
    assert tool is not None

    with pytest.raises(ToolError) as refused:
        # No mailer and no database: reaching either would fail differently.
        await tool.handler(person("guest"), tool.validate(arguments))

    assert refused.value.code == "forbidden"

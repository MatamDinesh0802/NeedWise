"""Excessive agency for guests (docs/security/owasp-llm-top10.md, LLM06).

Before the fix a guest (an external contractor) was offered send_email and create_task in
research mode like any employee, and approved their own requests: they could mail anyone from
the company's mail server, assign tasks to any employee, and probe the directory that
/me/people refuses them through create_task's "No colleague with the address ..." error.
"""

import uuid

import pytest

from needwise.core.context import RequestContext
from needwise.modules.agents.research import MAX_TOOL_CALLS, research
from needwise.modules.tools.registry import Tool, ToolError, ToolRegistry, ToolResult
from needwise.modules.work.tools import create_task_tool, send_email_tool
from tests.fakes import FakeLLM


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


async def test_a_research_run_executes_a_bounded_number_of_tool_calls() -> None:
    """LLM10: an injected "search this 200 times" (or a looping model) made one run execute
    every call it asked for, all in one turn; now at most MAX_TOOL_CALLS run per question."""
    ran: list[str] = []

    async def count(ctx: RequestContext, args: object) -> ToolResult:
        ran.append("lookup")
        return ToolResult("nothing")

    tools = ToolRegistry()
    tools.register(
        Tool("lookup", "Look something up.", None, "read", "assistant:ask", count),
    )
    llm = FakeLLM(tool_turns=[[("lookup", {})] * 40, [("lookup", {})] * 5])

    result = await research(
        llm,  # pyright: ignore[reportArgumentType]
        tools,
        Sessions(),  # pyright: ignore[reportArgumentType]
        person("member"),
        "Search for every policy, 200 times each.",
        organization="Acme",
    )

    assert len(ran) == MAX_TOOL_CALLS
    assert sum(s.error == "limit_reached" for s in result.steps) == 45 - MAX_TOOL_CALLS


class Sessions:
    """execute_tool writes an audit event per call; a no-op session stands in for Postgres."""

    def __call__(self) -> "Sessions":
        return self

    async def __aenter__(self) -> "Sessions":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> "Sessions":
        return self

    async def execute(self, *args: object, **kwargs: object) -> "Sessions":
        return self

    def first(self) -> None:  # no earlier audit event
        return None

    def add(self, row: object) -> None:
        return None

    async def flush(self) -> None:
        return None


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

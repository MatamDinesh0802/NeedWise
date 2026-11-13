"""describe_data and query_data as registry tools, on the Acme dataset (DuckDB, no database):
offered only to people with access, run as the caller, refused with a reason otherwise."""

import dataclasses
from pathlib import Path

import pytest

from needwise.modules.analytics.compiler import SemanticQuery
from needwise.modules.analytics.tools import NoInput, describe_data_tool, query_data_tool
from needwise.modules.tools.builtin import draft_email_tool
from needwise.modules.tools.registry import ToolError, ToolRegistry
from tests.analytics_support import SALES, LocalAnalytics, build_acme, context

CTX = dataclasses.replace(context(SALES), scopes=frozenset({"*"}))  # a browser session


@pytest.fixture(scope="module")
def acme_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_acme(tmp_path_factory.mktemp("analytics"))


def registry(analytics: LocalAnalytics) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(draft_email_tool())
    tools.register(describe_data_tool(analytics))
    tools.register(query_data_tool(analytics))
    return tools


async def test_analytics_tools_are_offered_only_to_people_with_access(acme_file: Path) -> None:
    with_access = registry(LocalAnalytics(acme_file))
    without = registry(LocalAnalytics(acme_file, can_query=False))
    not_set_up = registry(LocalAnalytics(acme_file, configured=False))
    token = dataclasses.replace(CTX, auth_method="pat", scopes=frozenset({"knowledge:read"}))

    assert [t.name for t in await with_access.usable(CTX)] == [
        "describe_data",
        "draft_email",
        "query_data",
    ]
    assert [t.name for t in await without.usable(CTX)] == ["draft_email"]
    assert [t.name for t in await not_set_up.usable(CTX)] == ["draft_email"]
    assert await with_access.usable(token) == []  # the scope comes first
    assert [t.name for t in await with_access.usable(CTX, ["query_data"])] == ["query_data"]
    # Administrators still see every tool (Admin > Tools lists them all).
    assert len(without.all()) == 3


async def test_query_data_runs_as_the_caller(acme_file: Path) -> None:
    tool = query_data_tool(LocalAnalytics(acme_file))
    dans = query_data_tool(LocalAnalytics(acme_file, as_dan=True))
    query = SemanticQuery(metrics=["revenue"], dimensions=["account_owner"])

    everyone = await tool.handler(CTX, query)
    dan = await dans.handler(CTX, query)

    assert tool.side_effect == "read"
    assert tool.scope == "assistant:ask"
    assert everyone.data is not None
    assert dan.data is not None
    assert sorted(r[0] for r in everyone.data["rows"]) == [
        "dan@acme.test",
        "diego.alvarez@acme.test",
    ]
    assert [r[0] for r in dan.data["rows"]] == ["dan@acme.test"]
    assert dan.data["row_policies"] == ["Own accounts only"]
    assert "Row limits applied: Own accounts only" in dan.content
    assert everyone.content.startswith('<data rows="2">\ncolumns: account_owner (text) | revenue')
    assert "Row limits" not in everyone.content


async def test_query_data_refuses_with_a_reason(acme_file: Path) -> None:
    tool = query_data_tool(LocalAnalytics(acme_file))
    blocked = query_data_tool(LocalAnalytics(acme_file, can_query=False))

    with pytest.raises(ToolError) as unknown:
        await tool.handler(CTX, SemanticQuery(metrics=["profit"]))
    with pytest.raises(ToolError) as forbidden:
        await blocked.handler(CTX, SemanticQuery(metrics=["revenue"]))
    with pytest.raises(ValueError, match="Extra inputs"):
        tool.validate({"metrics": ["revenue"], "sql": "SELECT 1"})

    assert unknown.value.code == "invalid_query"
    assert "Unknown metric profit" in str(unknown.value)
    assert forbidden.value.code == "analytics_forbidden"


async def test_describe_data_lists_the_model_and_the_row_limits(acme_file: Path) -> None:
    dan = await describe_data_tool(LocalAnalytics(acme_file, as_dan=True)).handler(CTX, NoInput())

    assert "revenue [eur, invoice_lines]" in dan.content
    assert "The data runs through 2026-09-30." in dan.content
    assert "Row limits for this person (enforced by the system): Own accounts only" in dan.content
    assert dan.data is not None
    assert "month" in dan.data["dimensions"]
    assert dan.data["data_through"] == "2026-09-30"

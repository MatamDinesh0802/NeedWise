"""Analytics as tools (ARCHITECTURE sections 13 and 16), for the research agent, workflows and
MCP clients:

    describe_data  the data model this person may query (metrics, dimensions, tables, limits)
    query_data     a semantic query (metrics by dimensions), run as this person

Both are read-only and need the assistant:ask scope. They are offered only to people the
semantic model gives access to (`Tool.available`), and the service checks again on every call:
the guard and the person's row policies apply exactly as in the BI agent and the API.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from workmind.core.context import RequestContext
from workmind.modules.analytics.agent import AnalyticsBackend, render_model, render_table
from workmind.modules.analytics.compiler import SemanticQuery
from workmind.modules.analytics.service import AnalyticsError
from workmind.modules.tools.registry import Tool, ToolError, ToolResult

TOOL_ROWS = 50  # rows of a result shown in the tool's text (all of them are in `data`)


class NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _available(analytics: AnalyticsBackend) -> Callable[[RequestContext], Awaitable[bool]]:
    async def available(ctx: RequestContext) -> bool:
        info = await analytics.access(ctx)
        return info.can_query and info.has_data

    return available


def _tool_error(exc: AnalyticsError) -> ToolError:
    return ToolError(exc.code, exc.detail or exc.title)


def describe_data_tool(analytics: AnalyticsBackend) -> Tool:
    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        try:
            model = await analytics.describe(ctx)
        except AnalyticsError as exc:
            raise _tool_error(exc) from exc
        limits = " ".join(model.access.row_policies)
        text = render_model(model)
        if model.data_through:
            text += f"\nThe data runs through {model.data_through.isoformat()}."
        if limits:
            text += f"\nRow limits for this person (enforced by the system): {limits}"
        return ToolResult(
            text,
            {
                "name": model.name,
                "data_through": model.data_through.isoformat() if model.data_through else None,
                "metrics": [m.name for m in model.metrics],
                "dimensions": [d.name for d in model.dimensions] + model.grains,
                "row_policies": model.access.row_policies,
            },
        )

    return Tool(
        name="describe_data",
        description=(
            "Describe the company's analytics data the current user may query: metrics, "
            "dimensions, tables and any row limits. Call it before query_data."
        ),
        input_model=NoInput,
        side_effect="read",
        scope="assistant:ask",
        handler=run,
        available=_available(analytics),
    )


def query_data_tool(analytics: AnalyticsBackend) -> Tool:
    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        assert isinstance(args, SemanticQuery)  # noqa: S101  (validated by the wrapper)
        try:
            result = await analytics.run_semantic(ctx, args)
        except AnalyticsError as exc:
            raise _tool_error(exc) from exc
        lines = [
            f'<data rows="{result.row_count}"{" truncated" if result.truncated else ""}>',
            *render_table(result.columns, result.rows, TOOL_ROWS),
            "</data>",
        ]
        if result.row_policies:
            lines.append(
                f"Row limits applied: {', '.join(result.row_policies)} (only rows this user "
                "may see are included)."
            )
        return ToolResult(
            "\n".join(lines),
            {
                "columns": [c.model_dump() for c in result.columns],
                "rows": result.rows,
                "row_count": result.row_count,
                "truncated": result.truncated,
                "sql": result.sql,
                "row_policies": result.row_policies,
            },
        )

    return Tool(
        name="query_data",
        description=(
            "Query the company's analytics data (e.g. revenue, pipeline) as the current user: "
            "metrics by dimensions, with filters and an inclusive date range. Use describe_data "
            "for the metric and dimension names. Only rows the user may see are returned."
        ),
        input_model=SemanticQuery,
        side_effect="read",
        scope="assistant:ask",
        handler=run,
        available=_available(analytics),
    )

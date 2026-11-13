"""Analytics as tools (ARCHITECTURE sections 13 and 16), for the research agent, workflows and
MCP clients:

    describe_data  the data model this person may query (metrics, dimensions, tables, limits)
    query_data     a semantic query (metrics by dimensions), run as this person

Both are read-only and need the assistant:ask scope. They are offered only to people the
semantic model gives access to (`Tool.available`), and the service checks again on every call:
the guard and the person's row policies apply exactly as in the BI agent and the API.

With database sources there are several datasets: describe_data lists each the person may query
under its key, and query_data takes that key (`dataset`; left out: the only one, or the
uploaded files).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workmind.core.context import RequestContext
from workmind.modules.analytics.agent import (
    DATASET_FIELD,
    AnalyticsBackend,
    render_model,
    render_models,
    render_table,
)
from workmind.modules.analytics.compiler import SemanticQuery
from workmind.modules.analytics.service import AnalyticsError, ModelDescription
from workmind.modules.tools.registry import Tool, ToolError, ToolResult

TOOL_ROWS = 50  # rows of a result shown in the tool's text (all of them are in `data`)


class NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataQuery(SemanticQuery):
    """query_data's input: a semantic query, on one dataset."""

    dataset: str | None = Field(default=None, max_length=63, description=DATASET_FIELD)


def _available(analytics: AnalyticsBackend) -> Callable[[RequestContext], Awaitable[bool]]:
    async def available(ctx: RequestContext) -> bool:
        return any(d.access.can_query and d.access.has_data for d in await analytics.datasets(ctx))

    return available


async def _usable(analytics: AnalyticsBackend, ctx: RequestContext) -> list[ModelDescription]:
    """The datasets this person may query, described; AnalyticsError when there are none."""
    datasets = [
        d for d in await analytics.datasets(ctx) if d.access.can_query and d.access.has_data
    ]
    if not datasets:
        return [await analytics.describe(ctx)]  # raises the reason (not set up, no access)
    return [await analytics.describe(ctx, d.key) for d in datasets]


def _tool_error(exc: AnalyticsError) -> ToolError:
    return ToolError(exc.code, exc.detail or exc.title)


def describe_data_tool(analytics: AnalyticsBackend) -> Tool:
    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        try:
            models = await _usable(analytics, ctx)
        except AnalyticsError as exc:
            raise _tool_error(exc) from exc
        several = len(models) > 1 or models[0].kind != "files"
        text = render_models(models) if several else render_model(models[0])
        for model in models:
            where = f" ({model.dataset})" if several else ""
            limits = " ".join(model.access.row_policies)
            if model.data_through:
                text += f"\nThe data{where} runs through {model.data_through.isoformat()}."
            if limits:
                text += f"\nRow limits for this person{where} (enforced by the system): {limits}"
        first = models[0]
        return ToolResult(
            text,
            {
                "name": first.name,
                "data_through": first.data_through.isoformat() if first.data_through else None,
                "metrics": [m.name for m in first.metrics],
                "dimensions": [d.name for d in first.dimensions] + first.grains,
                "row_policies": first.access.row_policies,
                "datasets": [
                    {
                        "key": m.dataset,
                        "name": m.name,
                        "dialect": m.dialect,
                        "metrics": [x.name for x in m.metrics],
                        "dimensions": [d.name for d in m.dimensions] + m.grains,
                        "row_policies": m.access.row_policies,
                    }
                    for m in models
                ],
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
        query = SemanticQuery.model_validate(args.model_dump(exclude={"dataset"}))
        try:
            dataset = args.dataset if isinstance(args, DataQuery) else None
            if dataset is None:
                models = await _usable(analytics, ctx)
                dataset = models[0].dataset if len(models) == 1 else None
            result = await analytics.run_semantic(ctx, query, dataset=dataset)
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
            "metrics by dimensions, with filters and an inclusive date range, on one dataset. "
            "Use describe_data for the datasets, metric and dimension names. Only rows the user "
            "may see are returned."
        ),
        input_model=DataQuery,
        side_effect="read",
        scope="assistant:ask",
        handler=run,
        available=_available(analytics),
    )

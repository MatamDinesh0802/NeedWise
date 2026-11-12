"""Business intelligence API (ARCHITECTURE section 16, docs/analytics.md).

    GET    /analytics/model                what the caller may query, or why they may not
    POST   /analytics/ask                  a question to the BI agent -> AnalyticsAnswer (stored)
    GET    /analytics/runs                 the caller's own answers, newest first
    GET    /analytics/runs/{id}            one of them (anyone else's is a 404)
    POST   /analytics/query                re-run SQL as the caller: same guard, same row policies

    GET    /admin/analytics                the model's YAML and the loaded tables
    PUT    /admin/analytics/model          replace the model
    POST   /admin/analytics/tables?name=   add or replace a table (CSV or XLSX body)
    DELETE /admin/analytics/tables/{name}  remove a table

Who may query, and which rows they see, is decided by the analytics service from the caller's
principals (never by the model, never by the request). Errors are problem documents.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, JsonValue, ValidationError

from workmind.api.deps import require_scope, resources_of, settings_of
from workmind.api.v1.admin import AdminContext
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.resources import Resources
from workmind.core.tenancy import tenant_transaction
from workmind.modules.analytics import agent
from workmind.modules.analytics.agent import AnalyticsAnswer, AnswerColumn
from workmind.modules.analytics.semantic import GRAINS
from workmind.modules.analytics.service import AnalyticsError, ModelDescription
from workmind.modules.identity.models import Tenant
from workmind.modules.usage.calls import attribute

router = APIRouter(prefix="/analytics", tags=["analytics"])
admin_router = APIRouter(prefix="/admin/analytics", tags=["admin"])
AskContext = Annotated[RequestContext, require_scope("assistant:ask")]
MAX_YAML_CHARS = 500_000
GRAIN_WORDS = {
    "day": "Calendar day",
    "week": "Week (starting Monday)",
    "month": "Calendar month",
    "quarter": "Calendar quarter",
    "year": "Calendar year",
}


# --- Schemas ------------------------------------------------------------------------------


class MetricOut(BaseModel):
    name: str
    description: str
    format: Literal["eur", "number", "percent"]


class DimensionOut(BaseModel):
    name: str
    description: str


class ColumnOut(BaseModel):
    name: str
    type: str
    description: str


class TableOut(BaseModel):
    name: str
    description: str
    columns: list[ColumnOut]


class ModelOut(BaseModel):
    available: Literal[True] = True
    name: str
    description: str
    data_through: date | None
    metrics: list[MetricOut]
    dimensions: list[DimensionOut]  # the time grains included
    tables: list[TableOut]
    row_policy: str | None  # the row limits that apply to the caller, in plain words
    examples: list[str]


class ModelUnavailable(BaseModel):
    available: Literal[False] = False
    reason: Literal["no_access", "not_configured"]
    message: str


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class RunItem(BaseModel):
    id: uuid.UUID
    question: str
    status: str  # answered | no_access | failed
    verified: int
    total: int
    created_at: datetime


class QueryIn(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000)


class QueryOut(BaseModel):
    columns: list[AnswerColumn]
    rows: list[list[JsonValue]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class DatasetColumnOut(BaseModel):
    name: str
    type: str


class DatasetTableOut(BaseModel):
    name: str
    rows: int
    columns: list[DatasetColumnOut]


class DatasetOut(BaseModel):
    version: str | None
    tables: list[DatasetTableOut]


class AdminAnalyticsOut(BaseModel):
    yaml: str | None
    dataset: DatasetOut


class ModelIn(BaseModel):
    yaml: str = Field(min_length=1, max_length=MAX_YAML_CHARS)


# --- Asking -------------------------------------------------------------------------------


@router.get("/model")
async def get_model(ctx: AskContext, request: Request) -> ModelOut | ModelUnavailable:
    """The data model the caller may query: metrics, dimensions, tables, the row limits that
    apply to them, and example questions."""
    analytics = resources_of(request).analytics
    access = await analytics.access(ctx)
    if not access.configured:
        return ModelUnavailable(reason="not_configured", message=agent.NOT_SET_UP)
    if not access.can_query:
        return ModelUnavailable(reason="no_access", message=access.summary)
    if not access.has_data:
        return ModelUnavailable(reason="not_configured", message=agent.NO_DATA)
    return _model_out(await analytics.describe(ctx))


@router.post("/ask")
async def ask(ctx: AskContext, request: Request, body: AskIn) -> AnalyticsAnswer:
    """Answer a question with queries run as the caller. Always 200 with an answer: its status
    says whether it was answered, refused (no_access) or failed. The answer is stored."""
    resources = resources_of(request)
    await resources.usage.check(ctx)  # 429 budget_exceeded before anything is stored
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
    with attribute("analytics", user_id=ctx.user_id):
        return await agent.answer(
            await resources.models.for_tenant(ctx.tenant_id),
            resources.analytics,
            ctx,
            body.question,
            organization=tenant.name if tenant else "the company",
        )


@router.get("/runs")
async def list_runs(ctx: AskContext, request: Request) -> list[RunItem]:
    """The caller's own answers, newest first (at most 100)."""
    runs = await resources_of(request).analytics.runs(ctx, limit=100, finished_only=True)
    return [
        RunItem(
            id=r.id,
            question=r.question,
            status=r.outcome or ("failed" if r.status == "failed" else "answered"),
            verified=r.verified,
            total=r.total,
            created_at=r.created_at,
        )
        for r in runs
    ]


@router.get("/runs/{run_id}")
async def get_run(ctx: AskContext, request: Request, run_id: uuid.UUID) -> AnalyticsAnswer:
    """One of the caller's answers, as it was given. Anyone else's is a 404."""
    run = await resources_of(request).analytics.run(ctx, run_id)
    if run.status == "running":
        raise ApiError(404, "not_found", "Run not found", "This question is still being answered.")
    try:
        return AnalyticsAnswer.model_validate(run.answer)
    except ValidationError:  # stored in another shape (e.g. by an older version)
        return AnalyticsAnswer(
            id=run.id,
            question=run.question,
            status="failed",
            error="This answer cannot be shown.",
            created_at=run.created_at,
        )


@router.post("/query")
async def run_query(ctx: AskContext, request: Request, body: QueryIn) -> QueryOut:
    """Re-run SQL (an answer's query, or one the caller wrote) through the same guard and the
    caller's own row policies: refreshed data, never more than they may see."""
    try:
        result = await resources_of(request).analytics.run_sql(ctx, body.sql)
    except AnalyticsError as exc:
        if exc.code == "analytics_forbidden":
            raise ApiError(403, "no_access", exc.title, exc.detail) from exc
        if exc.code in ("query_rejected", "query_failed"):
            raise ApiError(422, "sql_rejected", exc.title, exc.detail) from exc
        raise
    return QueryOut(
        columns=[AnswerColumn(name=c.name, type=c.type, format=c.format) for c in result.columns],
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        elapsed_ms=result.elapsed_ms,
    )


def _model_out(description: ModelDescription) -> ModelOut:
    dated = [f"{t.name}.{t.time_column}" for t in description.tables if t.time_column]
    on = f", on {' / '.join(dated)}" if dated else ""
    metrics = [
        MetricOut(
            name=m.name,
            description=m.description,
            format=m.format if m.format in ("eur", "percent") else "number",
        )
        for m in description.metrics
    ]
    return ModelOut(
        name=description.name,
        description=description.description,
        data_through=description.data_through,
        metrics=metrics,
        dimensions=[
            *(DimensionOut(name=d.name, description=d.description) for d in description.dimensions),
            *(DimensionOut(name=g, description=f"{GRAIN_WORDS[g]}{on}") for g in GRAINS),
        ],
        tables=[
            TableOut(
                name=t.name,
                description=t.description,
                columns=[
                    ColumnOut(name=c.name, type=c.type, description=c.description)
                    for c in t.columns
                ],
            )
            for t in description.tables
        ],
        row_policy=" ".join(description.access.row_policies) or None,
        examples=description.examples or _examples(metrics),
    )


def _examples(metrics: list[MetricOut]) -> list[str]:
    """Questions to start from when the model lists none."""
    names = [m.name.replace("_", " ") for m in metrics[:3]]
    if not names:
        return []
    return [f"Monthly {names[0]} this year", *(f"{n.capitalize()} by quarter" for n in names[1:])]


# --- Administration (org owners and admins) ------------------------------------------------


@admin_router.get("")
async def get_admin(ctx: AdminContext, request: Request) -> AdminAnalyticsOut:
    return await _admin_out(resources_of(request), ctx)


@admin_router.put("/model")
async def put_model(ctx: AdminContext, request: Request, body: ModelIn) -> AdminAnalyticsOut:
    """Replace the semantic model. A model that does not validate is refused (422
    invalid_model) with the problems listed."""
    resources = resources_of(request)
    await resources.analytics.set_model(ctx, body.yaml)
    return await _admin_out(resources, ctx)


@admin_router.post("/tables")
async def put_table(
    ctx: AdminContext,
    request: Request,
    name: Annotated[str, Query(min_length=1, max_length=63)],
) -> AdminAnalyticsOut:
    """Add or replace one table from the raw CSV or XLSX body, in a new dataset version."""
    settings, resources = settings_of(request), resources_of(request)
    limit = settings.max_upload_bytes
    if int(request.headers.get("content-length") or 0) > limit:
        raise ApiError(413, "too_large", "The file is too large", f"Limit: {limit} bytes.")
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > limit:
            raise ApiError(413, "too_large", "The file is too large", f"Limit: {limit} bytes.")
    await resources.analytics.put_table(
        ctx, name, bytes(data), request.headers.get("content-type") or ""
    )
    return await _admin_out(resources, ctx)


@admin_router.delete("/tables/{name}")
async def delete_table(ctx: AdminContext, request: Request, name: str) -> AdminAnalyticsOut:
    resources = resources_of(request)
    await resources.analytics.delete_table(ctx, name)
    return await _admin_out(resources, ctx)


async def _admin_out(resources: Resources, ctx: RequestContext) -> AdminAnalyticsOut:
    state = await resources.analytics.admin_state(ctx)
    return AdminAnalyticsOut(
        yaml=state.yaml,
        dataset=DatasetOut(
            version=state.dataset_version,
            tables=[
                DatasetTableOut(
                    name=t.name,
                    rows=t.rows,
                    columns=[
                        DatasetColumnOut(name=c.get("name", ""), type=c.get("type", ""))
                        for c in t.columns
                    ],
                )
                for t in state.tables
            ],
        ),
    )

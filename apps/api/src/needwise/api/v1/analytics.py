"""Business intelligence API (ARCHITECTURE section 16, docs/analytics.md).

    GET    /analytics/model                what the caller may query, or why they may not
    POST   /analytics/ask                  a question to the BI agent -> AnalyticsAnswer (stored)
    POST   /analytics/runs                 the same question, answered in the background -> 202
                                           {run_id}; its progress streams from .../events
    GET    /analytics/runs/{id}/events     Server-Sent Events: planning, queries, verification
    GET    /analytics/runs                 the caller's own answers, newest first
    GET    /analytics/runs/{id}            one of them (anyone else's is a 404)
    POST   /analytics/query                re-run SQL as the caller: same guard, same row policies

    GET    /admin/analytics                the model's YAML and the loaded tables
    PUT    /admin/analytics/model          replace the model
    POST   /admin/analytics/tables?name=   add or replace a table (CSV or XLSX body)
    DELETE /admin/analytics/tables/{name}  remove a table

    GET    /admin/analytics/sources                    PostgreSQL and MySQL sources
    POST   /admin/analytics/sources                    connect one (the password is sealed)
    PATCH  /admin/analytics/sources/{key}              change it
    DELETE /admin/analytics/sources/{key}              remove it
    POST   /admin/analytics/sources/{key}/test         connect and ask the server's version
    GET    /admin/analytics/sources/{key}/catalog      tables and columns it can read
    PUT    /admin/analytics/sources/{key}/model        its semantic model, checked against it

Who may query, and which rows they see, is decided by the analytics service from the caller's
principals (never by the model, never by the request). Errors are problem documents.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, JsonValue, ValidationError

from needwise.api.deps import rate_limit, require_scope, resources_of, settings_of
from needwise.api.v1.admin import AdminContext
from needwise.core.context import RequestContext
from needwise.core.errors import ApiError
from needwise.core.resources import Resources
from needwise.core.tenancy import tenant_transaction
from needwise.modules.analytics import agent
from needwise.modules.analytics.agent import AnalyticsAnswer, AnswerColumn
from needwise.modules.analytics.guard import Dialect
from needwise.modules.analytics.semantic import GRAINS
from needwise.modules.analytics.service import (
    AnalyticsError,
    DatasetKind,
    ModelDescription,
    SourceCatalog,
    SourceCheck,
    SourceOut,
)
from needwise.modules.analytics.sources import SourceIn, SourcePatch
from needwise.modules.conversations.events import RunMeta
from needwise.modules.identity.models import Tenant
from needwise.modules.usage.budgets import BudgetExceededError
from needwise.modules.usage.calls import attribute
from needwise.providers.llm import LLMProvider

log = structlog.get_logger("needwise.analytics")
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


class DatasetModelOut(BaseModel):
    """One dataset the caller may query: the uploaded files, or a database source."""

    key: str  # "uploads", or the source's key: pass it as `dataset` to /query
    kind: DatasetKind
    dialect: Dialect  # the SQL /query takes for it
    name: str
    description: str
    data_through: date | None
    metrics: list[MetricOut]
    dimensions: list[DimensionOut]  # the time grains included
    tables: list[TableOut]
    row_policy: str | None  # the row limits that apply to the caller, in plain words
    examples: list[str]


class ModelOut(BaseModel):
    """The first dataset (the uploaded files when the caller may query them) at the top level,
    as before database sources existed; every dataset the caller may query in `datasets`."""

    available: Literal[True] = True
    name: str
    description: str
    data_through: date | None
    metrics: list[MetricOut]
    dimensions: list[DimensionOut]  # the time grains included
    tables: list[TableOut]
    row_policy: str | None  # the row limits that apply to the caller, in plain words
    examples: list[str]
    datasets: list[DatasetModelOut] = []


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
    # "uploads" (the default) or a database source's key; the SQL is in that dataset's dialect.
    dataset: str | None = Field(default=None, max_length=63)


class RunStartedOut(BaseModel):
    run_id: uuid.UUID


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
    """The data the caller may query: for each dataset (the uploaded files, database sources)
    its metrics, dimensions, tables, the row limits that apply to the caller, and example
    questions."""
    analytics = resources_of(request).analytics
    datasets = await analytics.datasets(ctx)
    if not datasets:
        return ModelUnavailable(reason="not_configured", message=agent.NOT_SET_UP)
    queryable = [d for d in datasets if d.access.can_query]
    if not queryable:
        return ModelUnavailable(reason="no_access", message=datasets[0].access.summary)
    usable = [d for d in queryable if d.access.has_data]
    if not usable:
        return ModelUnavailable(reason="not_configured", message=agent.NO_DATA)
    described = [_dataset_out(await analytics.describe(ctx, d.key)) for d in usable]
    first = described[0]
    return ModelOut(
        **first.model_dump(exclude={"key", "kind", "dialect"}),
        datasets=described,
    )


@router.post("/ask", dependencies=[rate_limit("analytics")])
async def ask(ctx: AskContext, request: Request, body: AskIn) -> AnalyticsAnswer:
    """Answer a question with queries run as the caller. Always 200 with an answer: its status
    says whether it was answered, refused (no_access) or failed. The answer is stored."""
    resources = resources_of(request)
    await resources.usage.check(ctx)  # 429 budget_exceeded before anything is stored
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
    async with resources.runs.inflight.hold(ctx):  # 429 too_many_requests
        with attribute("analytics", user_id=ctx.user_id):
            return await agent.answer(
                await resources.models.for_tenant(ctx.tenant_id),
                resources.analytics,
                ctx,
                body.question,
                organization=tenant.name if tenant else "the company",
            )


@router.post("/runs", status_code=202, dependencies=[rate_limit("analytics")])
async def start_run(ctx: AskContext, request: Request, body: AskIn) -> RunStartedOut:
    """Ask in the background: 202 with the run's id at once; the answer's progress streams from
    GET /analytics/runs/{id}/events, and the answer is GET /analytics/runs/{id} once the stream
    says run.completed."""
    resources = resources_of(request)
    await resources.usage.check(ctx)  # 429 budget_exceeded before anything is stored
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
    token = uuid.uuid4().hex
    inflight = resources.runs.inflight
    if not await inflight.acquire(ctx, token):  # 429 too_many_requests
        raise inflight.refusal()
    try:
        created_at = datetime.now(UTC)
        run_id = await resources.analytics.start_run(ctx, body.question, created_at=created_at)
        # A run of its own: the event stream's key is the analytics run (no conversation).
        meta = RunMeta(run_id, ctx.tenant_id, ctx.user_id, conversation_id=run_id)
        await resources.runs.events.create(meta)
        llm = await resources.models.for_tenant(ctx.tenant_id)
    except BaseException:
        await inflight.release(ctx, token)
        raise
    with attribute("analytics", user_id=ctx.user_id):
        resources.runs.start(
            _answer_streamed(
                resources,
                llm,
                ctx,
                meta,
                body.question,
                organization=tenant.name if tenant else "the company",
                created_at=created_at,
            ),
            slot=(ctx, token),
        )
    return RunStartedOut(run_id=run_id)


async def _answer_streamed(
    resources: Resources,
    llm: LLMProvider,
    ctx: RequestContext,
    meta: RunMeta,
    question: str,
    *,
    organization: str,
    created_at: datetime,
) -> None:
    """The agent, with each step as an `analytics.step` event, then run.completed (the answer
    is stored: GET /analytics/runs/{id}) or run.failed."""
    events = resources.runs.events
    await events.emit(meta, "run.started", {"run_id": str(meta.run_id)})

    async def on_event(step: str, data: dict[str, Any]) -> None:
        await events.emit(meta, "analytics.step", {"step": step, **data})

    try:
        result = await agent.answer(
            llm,
            resources.analytics,
            ctx,
            question,
            organization=organization,
            run_id=meta.run_id,
            created_at=created_at,
            on_event=on_event,
        )
    except BudgetExceededError as exc:  # stored as a failed answer by the agent
        await events.emit(meta, "run.failed", exc.event())
        return
    except Exception:
        log.exception("analytics_run_failed", run_id=str(meta.run_id))
        await events.emit(meta, "run.failed", {"code": "internal", "message": agent.FAILED})
        return
    await events.emit(
        meta,
        "run.completed",
        {"run_id": str(meta.run_id), "status": result.status, "error": result.error},
    )


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
async def run_events(
    ctx: AskContext,
    request: Request,
    run_id: uuid.UUID,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """The run's progress as Server-Sent Events, replayable with Last-Event-ID, for an hour
    after its last event. Anyone else's run is a 404."""
    events = resources_of(request).runs.events
    meta = await events.meta(ctx.tenant_id, run_id)
    if meta is None or meta.user_id != ctx.user_id or meta.conversation_id != run_id:
        raise ApiError(404, "not_found", "Run not found or expired")

    async def stream() -> AsyncIterator[str]:
        yield "retry: 2000\n\n"
        async for event in events.read(meta, after=last_event_id or "0"):
            yield ": keep-alive\n\n" if event is None else event.sse()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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


@router.post("/query", dependencies=[rate_limit("analytics")])
async def run_query(ctx: AskContext, request: Request, body: QueryIn) -> QueryOut:
    """Re-run SQL (an answer's query, or one the caller wrote) through the same guard and the
    caller's own row policies: refreshed data, never more than they may see."""
    try:
        result = await resources_of(request).analytics.run_sql(ctx, body.sql, dataset=body.dataset)
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


def _dataset_out(description: ModelDescription) -> DatasetModelOut:
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
    return DatasetModelOut(
        key=description.dataset,
        kind=description.kind,
        dialect=description.dialect,
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


@admin_router.post("/tables", dependencies=[rate_limit("ingest")])
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


# --- Database sources (org owners and admins; docs/analytics-sql-sources.md) ---------------


@admin_router.get("/sources")
async def list_sources(ctx: AdminContext, request: Request) -> list[SourceOut]:
    """PostgreSQL and MySQL sources, with their status. Never the password."""
    return await resources_of(request).analytics.sources(ctx)


@admin_router.post("/sources", status_code=201)
async def create_source(ctx: AdminContext, request: Request, body: SourceIn) -> SourceOut:
    """Connect a database: where it is, the user and password (stored encrypted), the TLS mode,
    and the schemas its tables may come from. 422 invalid_source for a host NeedWise may not
    connect to; 409 conflict for a key in use."""
    return await resources_of(request).analytics.create_source(ctx, body)


@admin_router.patch("/sources/{key}")
async def update_source(
    ctx: AdminContext, request: Request, key: str, body: SourcePatch
) -> SourceOut:
    """Change a source. Pointing it elsewhere (host, port, database, user) needs the password
    again (422 password_required)."""
    return await resources_of(request).analytics.update_source(ctx, key, body)


@admin_router.delete("/sources/{key}", status_code=204)
async def delete_source(ctx: AdminContext, request: Request, key: str) -> Response:
    await resources_of(request).analytics.delete_source(ctx, key)
    return Response(status_code=204)


@admin_router.post("/sources/{key}/test")
async def test_source(ctx: AdminContext, request: Request, key: str) -> SourceCheck:
    """Connect as queries do (read-only session) and ask the server's version. 200 either way:
    `ok` and `error` say how it went."""
    return await resources_of(request).analytics.test_source(ctx, key)


@admin_router.get("/sources/{key}/catalog")
async def source_catalog(ctx: AdminContext, request: Request, key: str) -> SourceCatalog:
    """The tables and columns in the source's schemas that its user may read, to pick tables
    for the semantic model. 503 source_unavailable with the reason when it cannot connect."""
    return await resources_of(request).analytics.source_catalog(ctx, key)


@admin_router.put("/sources/{key}/model")
async def put_source_model(
    ctx: AdminContext, request: Request, key: str, body: ModelIn
) -> SourceOut:
    """Replace the source's semantic model. It is validated in the source's SQL dialect and
    checked against the database: 422 invalid_model with the problems."""
    return await resources_of(request).analytics.set_source_model(ctx, key, body.yaml)


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

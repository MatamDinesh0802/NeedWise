"""Usage and cost accounting, and budgets (docs/usage.md).

    GET /usage/summary      totals, cost per period, a breakdown and budget status, at the
                            caller's scope: the tenant (owners, admins), the departments they
                            manage (department managers), or only themselves
    GET /usage/events       every recorded model call, newest first (owners, admins)
    GET /usage/export.csv   the chargeback report: cost per department (owners, admins;
                            Enterprise "chargeback_reports", else 402 enterprise_feature)
    GET /admin/budgets      every budget with its status, and the default cap (owners, admins)
    PUT /admin/budgets      replace them (department budgets: Enterprise "department_budgets")

Days are UTC calendar days; budgets reset at UTC midnight or on the first of the month.
"""

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from workmind.api.deps import require_feature, require_scope, resources_of
from workmind.api.v1.admin import AdminContext
from workmind.core.context import RequestContext
from workmind.core.edition import enterprise_feature_error
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import User
from workmind.modules.usage import reports
from workmind.modules.usage.calls import FEATURES
from workmind.modules.usage.extensions import CHARGEBACK_REPORT, ChargebackQuery
from workmind.modules.usage.service import BudgetSpec, admin_view, replace_budgets

router = APIRouter(tags=["usage"])
admin_router = APIRouter(prefix="/admin/budgets", tags=["admin"])

MAX_RANGE_DAYS = 400
MAX_LIMIT_USD = 1_000_000_000.0
ReaderContext = Annotated[RequestContext, require_scope("profile:read")]
From = Annotated[date, Query(alias="from", description="first UTC day, YYYY-MM-DD")]
To = Annotated[date, Query(description="last UTC day, YYYY-MM-DD (included)")]


def _range(first: date, last: date) -> None:
    if last < first:
        raise ApiError(422, "invalid_range", "Invalid range", "`to` is before `from`.")
    if (last - first).days + 1 > MAX_RANGE_DAYS:
        raise ApiError(
            422, "invalid_range", "Range too long", f"Ask for at most {MAX_RANGE_DAYS} days."
        )


# --- Reports --------------------------------------------------------------------------------


@router.get("/usage/summary")
async def usage_summary(
    ctx: ReaderContext,
    request: Request,
    first: From,
    last: To,
    group_by: reports.GroupBy = "feature",
    interval: reports.Interval = "day",
) -> dict[str, Any]:
    """Usage at the caller's scope; `budgets` lists the budgets they may see, with status."""
    _range(first, last)
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        scope = await reports.scope_for(session, ctx)
        body = await reports.summary(session, scope, first, last, group_by, interval)
        bindings = await reports.visible_bindings(resources.usage, session, ctx, scope)
        statuses = await resources.usage.statuses(session, ctx.tenant_id, bindings)
    body["budgets"] = [s.as_dict() for s in statuses]
    return body


@router.get("/usage/events")
async def usage_events(
    ctx: AdminContext,
    request: Request,
    first: From,
    last: To,
    user_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    agent: Annotated[str | None, Query(max_length=100)] = None,
    model: Annotated[str | None, Query(max_length=200)] = None,
    feature: Annotated[str | None, Query(max_length=32)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Every recorded model call in the range, newest first, with names (never prompt text)."""
    _range(first, last)
    if feature and feature not in FEATURES:
        raise ApiError(422, "invalid_feature", "Unknown feature", f"Known: {', '.join(FEATURES)}.")
    filters = reports.EventFilters(first, last, user_id, department_id, agent, model, feature)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        try:
            return await reports.events_page(session, filters, cursor, limit)
        except reports.InvalidCursorError as exc:
            raise ApiError(422, "invalid_cursor", "Invalid cursor") from exc


@router.get(
    "/usage/export.csv",
    dependencies=[require_feature("chargeback_reports")],
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def chargeback_csv(
    ctx: AdminContext,
    request: Request,
    first: From,
    last: To,
    group_by: Literal["department"] = "department",
) -> Response:
    """The chargeback report (Enterprise): cost per department for the range, as CSV."""
    del group_by  # one grouping today; the parameter keeps the contract explicit
    _range(first, last)
    resources = resources_of(request)
    start, end = reports.day_range(first, last)
    query = ChargebackQuery(ctx.tenant_id, first, last, start, end)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        reports_found = resources.extensions.call(CHARGEBACK_REPORT, session, query)
        if not reports_found:  # licensed, but no implementation is installed
            raise enterprise_feature_error("chargeback_reports")
        csv = await reports_found[0]
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="usage.exported",
            resource_type="usage",
            details={"report": "chargeback", "from": first.isoformat(), "to": last.isoformat()},
        )
    filename = f"workmind-chargeback-{first.isoformat()}-to-{last.isoformat()}.csv"
    return Response(
        csv,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Budgets --------------------------------------------------------------------------------


class BudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["tenant", "department", "user"]
    subject_id: uuid.UUID | None = None
    period: Literal["day", "month"]
    limit_usd: float = Field(gt=0, le=MAX_LIMIT_USD)
    warn_at: float = Field(default=0.8, gt=0, le=1)
    action: Literal["block", "warn"] = "block"


class UserDefaultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Literal["day", "month"]
    limit_usd: float = Field(gt=0, le=MAX_LIMIT_USD)
    action: Literal["block", "warn"] = "block"
    warn_at: float = Field(default=0.8, gt=0, le=1)


class BudgetsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budgets: list[BudgetIn] = Field(default=[], max_length=1000)
    user_default: UserDefaultIn | None = None


def _error(index: int, field: str, message: str, value: object) -> dict[str, Any]:
    return {
        "type": "value_error",
        "loc": ("body", "budgets", index, field),
        "msg": message,
        "input": str(value) if value is not None else None,
    }


@admin_router.get("")
async def get_budgets(ctx: AdminContext, request: Request) -> dict[str, Any]:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        return await admin_view(resources.usage, session, ctx.tenant_id)


@admin_router.put("")
async def put_budgets(ctx: AdminContext, request: Request, body: BudgetsIn) -> dict[str, Any]:
    """Replaces every budget and the default cap (what is left out is removed)."""
    resources = resources_of(request)
    meter = resources.usage
    provider = meter.department_provider()
    if provider is None and any(b.scope == "department" for b in body.budgets):
        raise enterprise_feature_error("department_budgets")
    problems: list[dict[str, Any]] = []
    seen: set[tuple[str, uuid.UUID | None, str]] = set()
    for index, budget in enumerate(body.budgets):
        if budget.scope == "tenant" and budget.subject_id is not None:
            problems.append(
                _error(index, "subject_id", "The organization budget has no subject.", budget)
            )
        elif budget.scope != "tenant" and budget.subject_id is None:
            what = "department" if budget.scope == "department" else "person"
            problems.append(_error(index, "subject_id", f"Choose a {what}.", None))
        key = (budget.scope, budget.subject_id, budget.period)
        if key in seen:
            period = "daily" if budget.period == "day" else "monthly"
            whom = {"tenant": "the organization", "department": "this department"}.get(
                budget.scope, "this person"
            )
            problems.append(
                _error(index, "period", f"There is already a {period} budget for {whom}.", None)
            )
        seen.add(key)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        people = {b.subject_id for b in body.budgets if b.scope == "user" and b.subject_id}
        known_people: set[uuid.UUID] = (
            set((await session.scalars(select(User.id).where(User.id.in_(people)))).all())
            if people
            else set()
        )
        departments = {
            b.subject_id for b in body.budgets if b.scope == "department" and b.subject_id
        }
        known_departments: set[uuid.UUID] = (
            set(await provider.names(session, departments))
            if provider is not None and departments
            else set()
        )
        for index, budget in enumerate(body.budgets):
            if budget.subject_id is None:
                continue
            if budget.scope == "user" and budget.subject_id not in known_people:
                problems.append(
                    _error(index, "subject_id", "No such person in this organization.", None)
                )
            if budget.scope == "department" and budget.subject_id not in known_departments:
                problems.append(
                    _error(index, "subject_id", "No such department in this organization.", None)
                )
        if problems:
            raise RequestValidationError(problems)
        specs = [
            BudgetSpec(b.scope, b.subject_id, b.period, b.limit_usd, b.warn_at, b.action)
            for b in body.budgets
        ]
        cap = body.user_default
        if cap is not None:
            specs.append(
                BudgetSpec("user", None, cap.period, cap.limit_usd, cap.warn_at, cap.action)
            )
        await replace_budgets(
            session,
            ctx.tenant_id,
            specs=specs,
            with_departments=provider is not None,
            updated_by=ctx.user_id,
        )
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="budgets.updated",
            resource_type="budgets",
            details={
                "budgets": [
                    {
                        "scope": s.scope,
                        "subject_id": str(s.subject_id) if s.subject_id else None,
                        "period": s.period,
                        "limit_usd": s.limit_usd,
                        "action": s.action,
                    }
                    for s in specs
                ]
            },
        )
    meter.invalidate(ctx.tenant_id)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        return await admin_view(meter, session, ctx.tenant_id)

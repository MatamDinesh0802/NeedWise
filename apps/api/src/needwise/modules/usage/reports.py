"""Usage reports (docs/usage.md): who sees what, the summary, the list of events.

Owners and admins see the whole tenant. A department manager sees the people in the departments
they manage (all of those people's usage). Everyone else, and every access token, sees only
their own usage. Days are UTC calendar days.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.core.context import RequestContext
from needwise.modules.agents.models import AgentDefinition
from needwise.modules.identity.models import Department, DepartmentMember, User
from needwise.modules.usage.budgets import Binding, NamedRef
from needwise.modules.usage.meter import UsageMeter
from needwise.modules.usage.models import UsageEvent

GroupBy = Literal["department", "user", "agent", "model", "feature"]
Interval = Literal["day", "week", "month"]
ScopeKind = Literal["tenant", "departments", "self"]
NONE_KEY = "none"  # the group of events without a department, person or agent
NO_LABEL: dict[str, str] = {
    "department": "No department",
    "user": "No person (evaluations)",
    "agent": "No agent",
}
ADMIN_ROLES = ("owner", "admin")


@dataclass(frozen=True)
class UsageScope:
    kind: ScopeKind
    departments: list[NamedRef] = field(default_factory=list[NamedRef])
    users: list[uuid.UUID] | None = None  # whose events are visible; None: everyone's

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "departments": [{"id": str(d.id), "name": d.name} for d in self.departments],
        }


def is_admin(ctx: RequestContext) -> bool:
    """Owners and admins signed in with a browser session (tokens never administer)."""
    return ctx.has_role(*ADMIN_ROLES) and ctx.has_scope("admin")


async def scope_for(session: AsyncSession, ctx: RequestContext) -> UsageScope:
    if is_admin(ctx):
        return UsageScope("tenant")
    if ctx.has_scope("admin"):  # a browser session: a manager sees their departments
        managed = (
            await session.execute(
                select(Department.id, Department.name)
                .join(DepartmentMember, DepartmentMember.department_id == Department.id)
                .where(DepartmentMember.user_id == ctx.user_id, DepartmentMember.role == "manager")
                .order_by(Department.name)
            )
        ).all()
        departments = [NamedRef(did, name) for did, name in managed]
        if departments:
            members = await session.scalars(
                select(DepartmentMember.user_id)
                .where(DepartmentMember.department_id.in_([d.id for d in departments]))
                .distinct()
            )
            return UsageScope("departments", departments, sorted({*members.all(), ctx.user_id}))
    return UsageScope("self", users=[ctx.user_id])


def day_range(first: date, last: date) -> tuple[datetime, datetime]:
    """UTC days `first` to `last`, both included, as [start, end)."""
    start = datetime.combine(first, time(), tzinfo=UTC)
    return start, datetime.combine(last + timedelta(days=1), time(), tzinfo=UTC)


def _within(query: Any, scope: UsageScope, start: datetime, end: datetime) -> Any:
    query = query.where(UsageEvent.occurred_at >= start, UsageEvent.occurred_at < end)
    if scope.users is not None:
        query = query.where(UsageEvent.user_id.in_(scope.users))
    return query


async def _rows(session: AsyncSession, query: Any) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in await session.execute(query)]


GROUP_COLUMNS: dict[str, Any] = {
    "department": UsageEvent.department_id,
    "user": UsageEvent.user_id,
    "agent": UsageEvent.agent_key,
    "model": UsageEvent.model,
    "feature": UsageEvent.feature,
}


def _measures() -> tuple[Any, ...]:
    return (
        func.coalesce(func.sum(UsageEvent.cost_usd), 0),
        func.coalesce(func.sum(UsageEvent.input_tokens), 0),
        func.coalesce(func.sum(UsageEvent.output_tokens), 0),
        func.count(),
    )


def _point(values: tuple[Any, ...]) -> dict[str, Any]:
    """cost, input tokens, output tokens, requests: the measures after the first column."""
    return {
        "cost_usd": round(float(values[1]), 6),
        "input_tokens": int(values[2]),
        "output_tokens": int(values[3]),
        "requests": int(values[4]),
    }


async def summary(
    session: AsyncSession,
    scope: UsageScope,
    first: date,
    last: date,
    group_by: GroupBy,
    interval: Interval,
) -> dict[str, Any]:
    """Totals, the cost per period and the breakdown, as the API returns them (no budgets)."""
    start, end = day_range(first, last)
    unpriced = func.count().filter(UsageEvent.cost_usd.is_(None))
    (totals,) = await _rows(session, _within(select(*_measures(), unpriced), scope, start, end))
    period = func.date_trunc(interval, func.timezone("UTC", UsageEvent.occurred_at))
    series = await _rows(
        session,
        _within(select(period, *_measures()), scope, start, end).group_by(period).order_by(period),
    )
    column = GROUP_COLUMNS[group_by]
    rows = await _rows(
        session, _within(select(column, *_measures()), scope, start, end).group_by(column)
    )
    labels = await _labels(session, group_by, [row[0] for row in rows])
    total_cost = float(totals[0])
    groups: list[dict[str, Any]] = []
    for row in rows:
        key = row[0]
        group: dict[str, Any] = {
            "key": NONE_KEY if key is None else str(key),
            "label": NO_LABEL.get(group_by, "None") if key is None else labels.get(key, str(key)),
            **_point(row),
        }
        share = float(row[1]) / total_cost if total_cost > 0 else 0.0
        group["share"] = round(share, 6)
        groups.append(group)
    groups.sort(key=lambda g: (-float(g["cost_usd"]), -int(g["requests"]), str(g["label"])))
    return {
        "from": first.isoformat(),
        "to": last.isoformat(),
        "interval": interval,
        "group_by": group_by,
        "scope": scope.as_dict(),
        "totals": {
            "cost_usd": round(total_cost, 6),
            "input_tokens": int(totals[1]),
            "output_tokens": int(totals[2]),
            "requests": int(totals[3]),
            "unpriced_requests": int(totals[4]),
        },
        "series": [{"period": row[0].date().isoformat(), **_point(row)} for row in series],
        "groups": groups,
    }


async def _labels(session: AsyncSession, group_by: str, keys: Sequence[Any]) -> dict[Any, str]:
    """Names people read, never ids: departments, people and agents by name."""
    present = [k for k in keys if k is not None]
    if not present:
        return {}
    if group_by == "department":
        query: Any = select(Department.id, Department.name).where(Department.id.in_(present))
        missing = "(deleted department)"
    elif group_by == "user":
        query = select(User.id, User.display_name).where(User.id.in_(present))
        missing = "(deleted person)"
    elif group_by == "agent":
        query = select(AgentDefinition.key, AgentDefinition.name).where(
            AgentDefinition.key.in_(present)
        )
        missing = ""  # an agent deleted since: its key
    else:
        return {k: str(k) for k in present}
    found: dict[Any, str] = {row[0]: str(row[1]) for row in await _rows(session, query)}
    return {k: found.get(k) or missing or str(k) for k in present}


# --- Events (owners and admins) -------------------------------------------------------------


@dataclass(frozen=True)
class EventFilters:
    first: date
    last: date
    user_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    agent: str | None = None
    model: str | None = None
    feature: str | None = None


class InvalidCursorError(ValueError):
    pass


def parse_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        stamp, last_id = cursor.split("|", 1)
        when = datetime.fromisoformat(stamp)
        return (when if when.tzinfo else when.replace(tzinfo=UTC)), uuid.UUID(last_id)
    except ValueError as exc:
        raise InvalidCursorError(cursor) from exc


async def events_page(
    session: AsyncSession, filters: EventFilters, cursor: str | None, limit: int
) -> dict[str, Any]:
    """Newest first. `next_cursor` continues after the last item."""
    start, end = day_range(filters.first, filters.last)
    query = (
        select(UsageEvent, User.display_name, Department.name)
        .outerjoin(User, User.id == UsageEvent.user_id)
        .outerjoin(Department, Department.id == UsageEvent.department_id)
        .where(UsageEvent.occurred_at >= start, UsageEvent.occurred_at < end)
        .order_by(UsageEvent.occurred_at.desc(), UsageEvent.id.desc())
        .limit(limit + 1)
    )
    if filters.user_id is not None:
        query = query.where(UsageEvent.user_id == filters.user_id)
    if filters.department_id is not None:
        query = query.where(UsageEvent.department_id == filters.department_id)
    if filters.agent:
        query = query.where(UsageEvent.agent_key == filters.agent)
    if filters.model:
        query = query.where(UsageEvent.model == filters.model)
    if filters.feature:
        query = query.where(UsageEvent.feature == filters.feature)
    if cursor:
        query = query.where(tuple_(UsageEvent.occurred_at, UsageEvent.id) < parse_cursor(cursor))
    rows = (await session.execute(query)).all()
    page, more = rows[:limit], len(rows) > limit
    items = [
        {
            "id": str(event.id),
            "occurred_at": event.occurred_at.isoformat(),
            "user": {"id": str(event.user_id), "name": user_name or "(deleted person)"}
            if event.user_id
            else None,
            "department": {
                "id": str(event.department_id),
                "name": department_name or "(deleted department)",
            }
            if event.department_id
            else None,
            "agent_key": event.agent_key,
            "feature": event.feature,
            "model": event.model,
            "provider": event.provider,
            "local": event.local,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "cost_usd": None if event.cost_usd is None else round(float(event.cost_usd), 9),
            "ref": {"type": event.ref_type, "id": event.ref_id}
            if event.ref_type and event.ref_id
            else None,
        }
        for event, user_name, department_name in page
    ]
    last = page[-1][0] if page and more else None
    return {
        "items": items,
        "next_cursor": f"{last.occurred_at.isoformat()}|{last.id}" if last else None,
    }


# --- Budgets the caller may see -------------------------------------------------------------


async def visible_bindings(
    meter: UsageMeter, session: AsyncSession, ctx: RequestContext, scope: UsageScope
) -> list[Binding]:
    """The organization's budgets, the departments' the caller may see (Enterprise), their own
    cap, and for owners and admins every person's own cap."""
    rules = await meter.rules(ctx.tenant_id)
    found = [Binding(r, None) for r in rules if r.scope == "tenant"]
    provider = meter.department_provider()
    department_rules = [r for r in rules if r.scope == "department"]
    if provider is not None and department_rules:
        visible = await provider.visible(session, ctx)
        found += [
            Binding(r, r.subject_id)
            for r in department_rules
            if visible is None or r.subject_id in visible
        ]
    caps = [r for r in rules if r.scope == "user" and r.subject_id is not None]
    own = [r for r in caps if r.subject_id == ctx.user_id]
    if scope.kind == "tenant":
        found += [Binding(r, r.subject_id) for r in caps]
    else:
        found += [Binding(r, ctx.user_id) for r in own]
    if not own:  # the default cap is theirs
        found += [Binding(r, ctx.user_id) for r in rules if r.is_default]
    return found

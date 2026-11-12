"""The semantic layer (ARCHITECTURE section 16): what the BI agent may query, defined by admins in
YAML. Metrics, dimensions and joins name allow-listed tables; the model never sees anything else.

Who may query the model, and which rows they see, come from principals written as keys
(`department:finance`, `department-manager:sales`, `group:leadership`, `user:dan@acme.test`,
`org`). They are resolved to principal ids per tenant (resolve()), like collection grants.
"""

import re
import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.core.context import RequestContext
from workmind.modules.identity.models import Department, Group, User

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
Format = Literal["eur", "number", "percent", "text", "date"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Column(_Strict):
    type: Literal["text", "integer", "decimal", "date", "boolean"]
    description: str = ""


class Table(_Strict):
    description: str
    time_column: str | None = None  # the default date for time grains and date ranges
    columns: dict[str, Column]


class Join(_Strict):
    """from_table.from_column = to_table.to_column (many-to-one, from the fact table)."""

    left: str  # table.column
    right: str


class Dimension(_Strict):
    sql: str  # over qualified columns, e.g. customers.region
    description: str = ""
    format: Format = "text"


class Metric(_Strict):
    table: str  # the fact table it aggregates
    sql: str  # an aggregate, e.g. sum(invoice_lines.amount_eur)
    filter: str | None = None  # a condition on the fact table, e.g. status <> 'void'
    description: str
    format: Format = "number"


class RowPolicy(_Strict):
    """Rows a principal may see. `filters` per table may use {user.email} and {user.id}."""

    name: str
    description: str
    applies_to: list[str]
    unless: list[str] = []
    filters: dict[str, str]


class SemanticModel(_Strict):
    name: str
    description: str
    data_through: date | None = None  # the last closed period, told to the model
    access: list[str] = Field(min_length=1)
    tables: dict[str, Table]
    joins: list[Join] = []
    dimensions: dict[str, Dimension] = {}
    metrics: dict[str, Metric]
    row_policies: list[RowPolicy] = []

    @field_validator("tables", "dimensions", "metrics")
    @classmethod
    def _identifiers(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        bad = [k for k in value if not IDENTIFIER.match(k)]
        if bad:
            raise ValueError(f"not a lower-case identifier: {bad}")
        return value

    @model_validator(mode="after")
    def _references(self) -> "SemanticModel":
        for name, table in self.tables.items():
            bad = [c for c in table.columns if not IDENTIFIER.match(c)]
            if bad:
                raise ValueError(f"table {name}: bad column names {bad}")
            if table.time_column and table.time_column not in table.columns:
                raise ValueError(f"table {name}: time_column {table.time_column} is not a column")
        for join in self.joins:
            for side in (join.left, join.right):
                table, _, column = side.partition(".")
                if column not in self.tables.get(table, Table(description="", columns={})).columns:
                    raise ValueError(f"join: unknown column {side}")
        for name, metric in self.metrics.items():
            if metric.table not in self.tables:
                raise ValueError(f"metric {name}: unknown table {metric.table}")
        for name in self.dimensions:
            if name in GRAINS:
                raise ValueError(f"dimension {name}: reserved for time grains")
        for policy in self.row_policies:
            unknown = set(policy.filters) - set(self.tables)
            if unknown:
                raise ValueError(f"row policy {policy.name}: unknown tables {sorted(unknown)}")
        return self

    def join_path(self, fact: str, table: str) -> list[Join]:
        """Joins from the fact table to `table` (breadth-first over many-to-one joins)."""
        if table == fact:
            return []
        frontier: list[tuple[str, list[Join]]] = [(fact, [])]
        seen = {fact}
        while frontier:
            current, path = frontier.pop(0)
            for join in self.joins:
                left, right = join.left.split(".")[0], join.right.split(".")[0]
                if left == current and right not in seen:
                    if right == table:
                        return [*path, join]
                    seen.add(right)
                    frontier.append((right, [*path, join]))
        raise ValueError(f"{table} cannot be reached from {fact}")


# Time grains available on every fact table with a time_column.
GRAINS = {"day": "day", "week": "week", "month": "month", "quarter": "quarter", "year": "year"}


def parse(text: str) -> SemanticModel:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"not valid YAML: {exc}") from exc
    return SemanticModel.model_validate(raw)


class Resolved(BaseModel):
    """A model whose principal keys are resolved to this tenant's principal ids."""

    model: SemanticModel
    access: frozenset[str]
    policies: list[tuple[RowPolicy, frozenset[str], frozenset[str]]]  # policy, applies, unless


async def resolve(session: AsyncSession, model: SemanticModel, tenant_id: uuid.UUID) -> Resolved:
    async def ids(keys: list[str]) -> frozenset[str]:
        out: set[str] = set()
        for key in keys:
            out |= await _principal(session, key, tenant_id)
        return frozenset(out)

    return Resolved(
        model=model,
        access=await ids(model.access),
        policies=[(p, await ids(p.applies_to), await ids(p.unless)) for p in model.row_policies],
    )


async def _principal(session: AsyncSession, key: str, tenant_id: uuid.UUID) -> set[str]:
    kind, _, value = key.partition(":")
    if kind == "org" and not value:
        return {f"org:{tenant_id}"}  # members (not guests), as for collections
    if kind in ("department", "department-manager"):
        found = await session.scalar(select(Department.id).where(Department.slug == value))
        prefix = "dept" if kind == "department" else "dept-manager"
        return {f"{prefix}:{found}"} if found else set()
    if kind == "group":
        found = await session.scalar(select(Group.id).where(Group.slug == value))
        return {f"group:{found}"} if found else set()
    if kind == "user":
        found = await session.scalar(select(User.id).where(User.email == value.lower()))
        return {f"user:{found}"} if found else set()
    raise ValueError(f"unknown principal key {key!r}")


def can_query(resolved: Resolved, ctx: RequestContext) -> bool:
    return bool(resolved.access & ctx.principals)


def row_filters(resolved: Resolved, ctx: RequestContext, email: str) -> dict[str, str]:
    """table -> SQL condition for this user (several policies on a table are ANDed). The user's
    attributes are substituted as escaped string literals, never spliced as SQL."""
    filters: dict[str, list[str]] = {}
    for policy, applies, unless in resolved.policies:
        if not (applies & ctx.principals) or (unless & ctx.principals):
            continue
        for table, condition in policy.filters.items():
            text = condition.replace("{user.email}", _literal(email)).replace(
                "{user.id}", _literal(str(ctx.user_id))
            )
            filters.setdefault(table, []).append(f"({text})")
    return {table: " AND ".join(parts) for table, parts in filters.items()}


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"

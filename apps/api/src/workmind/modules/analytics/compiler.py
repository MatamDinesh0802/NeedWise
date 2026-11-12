"""Semantic queries -> SQL (ARCHITECTURE section 16).

The BI agent asks for metrics by dimensions, with filters and a date range; the compiler turns
that into one SELECT over the model's tables, joining from the metrics' fact table along the
model's (many-to-one) joins. It is built as a sqlglot tree: the model's SQL fragments are parsed,
and every value from the request is a literal node, so nothing is ever spliced into SQL text.

The compiled SQL then goes through the SQL guard like any other query (service.py), which adds
the user's row policies: there is one code path for permissions.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlglot import exp

from workmind.modules.analytics.guard import DIALECT
from workmind.modules.analytics.semantic import GRAINS, Format, Join, SemanticModel, expression

Op = Literal["=", "!=", ">", ">=", "<", "<=", "in", "not_in"]
Value = str | int | float | bool
_COMPARISONS: dict[str, type[exp.Binary]] = {
    "=": exp.EQ,
    "!=": exp.NEQ,
    ">": exp.GT,
    ">=": exp.GTE,
    "<": exp.LT,
    "<=": exp.LTE,
}


class CompileError(ValueError):
    """The request does not fit the model; the message says how to fix it."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QueryFilter(_Strict):
    dimension: str
    op: Op = "="
    values: list[Value] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _arity(self) -> "QueryFilter":
        if self.op not in ("in", "not_in") and len(self.values) != 1:
            raise ValueError(f"operator {self.op} takes exactly one value")
        if any(isinstance(v, float) and not math.isfinite(v) for v in self.values):
            raise ValueError("numbers must be finite")
        return self


class OrderBy(_Strict):
    field: str
    direction: Literal["asc", "desc"] = "desc"


class SemanticQuery(_Strict):
    """What the BI agent asks for. Dimensions may include a time grain (day, week, month,
    quarter, year) on the fact table's time column; dates are inclusive."""

    metrics: list[str] = Field(min_length=1, max_length=6)
    dimensions: list[str] = Field(default=[], max_length=4)
    filters: list[QueryFilter] = Field(default=[], max_length=10)
    date_from: date | None = None
    date_to: date | None = None
    order_by: list[OrderBy] = Field(default=[], max_length=4)
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def _consistent(self) -> "SemanticQuery":
        for names, kind in ((self.metrics, "metric"), (self.dimensions, "dimension")):
            if len(set(names)) != len(names):
                raise ValueError(f"a {kind} is listed twice")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from is after date_to")
        return self


@dataclass(frozen=True)
class OutputColumn:
    name: str
    kind: Literal["dimension", "metric"]
    format: Format


@dataclass(frozen=True)
class CompiledQuery:
    sql: str  # without row policies and without LIMIT: the guard adds both
    fact: str
    columns: list[OutputColumn]
    limit: int


def compile_query(model: SemanticModel, query: SemanticQuery) -> CompiledQuery:
    fact = _fact_table(model, query.metrics)
    time_column = model.tables[fact].time_column
    needed: set[str] = set()
    select = exp.Select()
    columns: list[OutputColumn] = []

    for name in query.dimensions:
        if name in GRAINS:
            if not time_column:
                raise CompileError(f"{fact} has no date column, so it cannot be grouped by {name}")
            truncated = exp.func("date_trunc", exp.Literal.string(name), _column(fact, time_column))
            node: exp.Expr = exp.cast(truncated, exp.DataType.build("DATE"))
            columns.append(OutputColumn(name, "dimension", "date"))
        else:
            node = _dimension(model, fact, name, needed)
            columns.append(OutputColumn(name, "dimension", model.dimensions[name].format))
        select = select.select(exp.alias_(node, name, quoted=False), copy=False)

    for name in query.metrics:
        metric = model.metrics[name]
        node = _metric(fact, metric.sql, metric.filter)
        select = select.select(exp.alias_(node, name, quoted=False), copy=False)
        columns.append(OutputColumn(name, "metric", metric.format))

    conditions: list[exp.Expr] = []
    for item in query.filters:
        if item.dimension in GRAINS:
            raise CompileError("Filter dates with date_from and date_to, not a time grain.")
        target = _dimension(model, fact, item.dimension, needed)
        conditions.append(_condition(target, item))
    if query.date_from or query.date_to:
        if not time_column:
            raise CompileError(f"{fact} has no date column, so it cannot be filtered by date")
        column = _column(fact, time_column)
        if query.date_from:
            conditions.append(exp.GTE(this=column.copy(), expression=_date(query.date_from)))
        if query.date_to:
            # Exclusive upper bound on the next day: also right for timestamp columns.
            end = query.date_to + timedelta(days=1)
            conditions.append(exp.LT(this=column.copy(), expression=_date(end)))

    select = select.from_(exp.to_table(fact), copy=False)
    for join in _joins(model, fact, needed):
        left_table, left_column = join.left.split(".")
        right_table, right_column = join.right.split(".")
        on = exp.EQ(
            this=_column(left_table, left_column), expression=_column(right_table, right_column)
        )
        select = select.join(exp.to_table(right_table), on=on, join_type="inner", copy=False)
    if conditions:
        select = select.where(exp.and_(*conditions, copy=False), copy=False)
    if query.dimensions:
        select = select.group_by(
            *[exp.Literal.number(i) for i in range(1, len(query.dimensions) + 1)], copy=False
        )

    positions = {c.name: i for i, c in enumerate(columns, start=1)}
    order = query.order_by or _default_order(query)
    for item in order:
        if item.field not in positions:
            raise CompileError(
                f"Cannot order by {item.field}: order by one of {', '.join(positions)}."
            )
        select = select.order_by(
            exp.Ordered(
                this=exp.Literal.number(positions[item.field]),
                desc=item.direction == "desc",
                nulls_first=False,
            ),
            copy=False,
        )
    return CompiledQuery(
        sql=select.sql(dialect=DIALECT), fact=fact, columns=columns, limit=query.limit
    )


def _fact_table(model: SemanticModel, metrics: list[str]) -> str:
    unknown = [m for m in metrics if m not in model.metrics]
    if unknown:
        raise CompileError(
            f"Unknown metric {', '.join(unknown)}. Metrics: {', '.join(model.metrics)}."
        )
    facts = {m: model.metrics[m].table for m in metrics}
    if len(set(facts.values())) > 1:
        detail = ", ".join(f"{m} ({t})" for m, t in facts.items())
        raise CompileError(
            f"These metrics come from different tables: {detail}. Ask for them separately."
        )
    return next(iter(facts.values()))


def _dimension(model: SemanticModel, fact: str, name: str, needed: set[str]) -> exp.Expr:
    if name not in model.dimensions:
        options = [*model.dimensions, *GRAINS]
        raise CompileError(f"Unknown dimension {name}. Dimensions: {', '.join(options)}.")
    for table in model.dimension_tables(name):
        try:
            model.join_path(fact, table)
        except ValueError as exc:
            raise CompileError(
                f"The dimension {name} ({table}) cannot be combined with metrics of {fact}."
            ) from exc
        needed.add(table)
    return expression(model.dimensions[name].sql)


def _joins(model: SemanticModel, fact: str, tables: set[str]) -> list[Join]:
    """The joins to reach every needed table, each once, in path order."""
    joins: list[Join] = []
    for table in sorted(tables):
        for join in model.join_path(fact, table):
            if join not in joins:
                joins.append(join)
    return joins


def _metric(fact: str, sql: str, condition: str | None) -> exp.Expr:
    node = _qualified(expression(sql), fact)
    if not condition:
        return node
    where = _qualified(expression(condition), fact)
    aggregates = [a for a in node.find_all(exp.AggFunc)]
    for aggregate in aggregates:
        parent = aggregate.parent
        if isinstance(parent, exp.Filter) and isinstance(parent.expression, exp.Where):
            existing = parent.expression.this
            parent.expression.set("this", exp.and_(existing, where.copy(), copy=False))
            continue
        filtered = exp.Filter(this=aggregate.copy(), expression=exp.Where(this=where.copy()))
        if aggregate is node:
            node = filtered
        else:
            aggregate.replace(filtered)
    return node


def _qualified(node: exp.Expr, table: str) -> exp.Expr:
    """Qualify bare columns with the fact table (other tables share column names, e.g. status)."""
    for column in node.find_all(exp.Column):
        if not column.table:
            column.set("table", exp.to_identifier(table))
    return node


def _condition(target: exp.Expr, item: QueryFilter) -> exp.Expr:
    values = [_literal(v) for v in item.values]
    if item.op == "in":
        return exp.In(this=target, expressions=values)
    if item.op == "not_in":
        return exp.Not(this=exp.In(this=target, expressions=values))
    return _COMPARISONS[item.op](this=target, expression=values[0])


def _literal(value: Value) -> exp.Expr:
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int | float):
        return exp.Literal.number(value)
    return exp.Literal.string(value)


def _date(value: date) -> exp.Expr:
    return exp.cast(exp.Literal.string(value.isoformat()), exp.DataType.build("DATE"))


def _column(table: str, column: str) -> exp.Column:
    return exp.column(column, table=table)


def _default_order(query: SemanticQuery) -> list[OrderBy]:
    grains = [d for d in query.dimensions if d in GRAINS]
    if grains:
        return [OrderBy(field=grains[0], direction="asc")]
    return [OrderBy(field=query.metrics[0], direction="desc")]

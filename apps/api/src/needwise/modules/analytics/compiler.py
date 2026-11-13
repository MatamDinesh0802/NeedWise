"""Semantic queries -> SQL (ARCHITECTURE section 16).

The BI agent asks for metrics by dimensions, with filters and a date range; the compiler turns
that into one SELECT over the model's tables, joining from the metrics' fact table along the
model's (many-to-one) joins. They are LEFT JOINs: a fact row whose key has no match (an orphan)
stays in the totals, under a NULL group, instead of silently dropping out of a breakdown. That
the other side's key is unique is checked by the service against the loaded data
(Analytics._check_joins): a duplicate would count fact rows twice. The SQL is built as a
sqlglot tree: the model's SQL fragments are parsed, and every value from the request is a
literal node, so nothing is ever spliced into SQL text.

The compiled SQL then goes through the SQL guard like any other query (service.py), which adds
the user's row policies: there is one code path for permissions.

The SQL is written in the model's dialect: DuckDB for uploaded files, PostgreSQL or MySQL for a
database source. MySQL has neither date_trunc nor FILTER (WHERE ...) on aggregates, so time
grains are built from MAKEDATE / INTERVAL and filtered metrics become CASE WHEN inside the
aggregate (the same values: rows that fail the condition add NULL, which aggregates skip).
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, cast

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlglot import exp

from needwise.modules.analytics.guard import DIALECT, Dialect
from needwise.modules.analytics.semantic import GRAINS, Format, Join, SemanticModel, expression

Op = Literal["=", "!=", ">", ">=", "<", "<=", "in", "not_in"]
Value = str | int | float | bool
# MySQL: the first day of each period, as a DATE (__nw_col__ stands for the time column).
_MYSQL_GRAINS = {
    "day": "CAST(__nw_col__ AS DATE)",
    "week": "DATE_SUB(CAST(__nw_col__ AS DATE), INTERVAL WEEKDAY(__nw_col__) DAY)",
    "month": "CAST(MAKEDATE(YEAR(__nw_col__), 1) + INTERVAL (MONTH(__nw_col__) - 1) MONTH AS DATE)",
    "quarter": (
        "CAST(MAKEDATE(YEAR(__nw_col__), 1) + INTERVAL (QUARTER(__nw_col__) - 1) QUARTER AS DATE)"
    ),
    "year": "MAKEDATE(YEAR(__nw_col__), 1)",
}
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
    joins: tuple[Join, ...] = ()  # many-to-one: each right-hand key must be unique


def compile_query(model: SemanticModel, query: SemanticQuery) -> CompiledQuery:
    fact = _fact_table(model, query.metrics)
    time_column = model.tables[fact].time_column
    dialect = model.dialect
    needed: set[str] = set()
    select = exp.Select()
    columns: list[OutputColumn] = []

    for name in query.dimensions:
        if name in GRAINS:
            if not time_column:
                raise CompileError(f"{fact} has no date column, so it cannot be grouped by {name}")
            node: exp.Expr = _grain(name, _column(fact, time_column), dialect)
            columns.append(OutputColumn(name, "dimension", "date"))
        else:
            node = _dimension(model, fact, name, needed)
            columns.append(OutputColumn(name, "dimension", model.dimensions[name].format))
        select = select.select(exp.alias_(node, name, quoted=False), copy=False)

    for name in query.metrics:
        metric = model.metrics[name]
        node = _metric(fact, metric.sql, metric.filter, dialect)
        select = select.select(exp.alias_(node, name, quoted=False), copy=False)
        columns.append(OutputColumn(name, "metric", metric.format))

    conditions: list[exp.Expr] = []
    filters = [model.metrics[name].filter for name in query.metrics]
    if query.dimensions and all(filters):
        # A row that passes no metric's filter adds nothing to any metric: leaving it out keeps
        # every value and drops groups that would only show NULL ("pipeline value by stage"
        # would otherwise list won and lost deals with no value).
        unique = dict.fromkeys(" ".join(f.split()) for f in filters if f)
        conditions.append(
            exp.or_(*(_qualified(expression(f, dialect), fact) for f in unique), copy=False)
        )
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
        if query.date_to and query.date_to < date.max:  # 9999-12-31: no upper bound
            # Exclusive upper bound on the next day: also right for timestamp columns.
            end = query.date_to + timedelta(days=1)
            conditions.append(exp.LT(this=column.copy(), expression=_date(end)))

    select = select.from_(exp.to_table(fact), copy=False)
    joins = _joins(model, fact, needed)
    for join in joins:
        left_table, left_column = join.left.split(".")
        right_table, right_column = join.right.split(".")
        on = exp.EQ(
            this=_column(left_table, left_column), expression=_column(right_table, right_column)
        )
        select = select.join(exp.to_table(right_table), on=on, join_type="left", copy=False)
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
                # MySQL cannot say NULLS LAST next to a position: its own order applies there
                # (NULL first ascending, last descending).
                nulls_first=None if dialect == "mysql" else False,
            ),
            copy=False,
        )
    return CompiledQuery(
        sql=select.sql(dialect=dialect),
        fact=fact,
        columns=columns,
        limit=query.limit,
        joins=tuple(joins),
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
    return expression(model.dimensions[name].sql, model.dialect)


def _joins(model: SemanticModel, fact: str, tables: set[str]) -> list[Join]:
    """The joins to reach every needed table, each once, in path order."""
    joins: list[Join] = []
    for table in sorted(tables):
        for join in model.join_path(fact, table):
            if join not in joins:
                joins.append(join)
    return joins


def _grain(grain: str, column: exp.Column, dialect: Dialect) -> exp.Expr:
    """The first day of `column`'s period, as a DATE."""
    if dialect == "mysql":
        node = sqlglot.parse_one(f"SELECT {_MYSQL_GRAINS[grain]}", dialect="mysql")
        for placeholder in list(node.find_all(exp.Column)):
            if placeholder.name == "__nw_col__":
                placeholder.replace(column.copy())
        return cast(exp.Select, node).selects[0]
    truncated = exp.func("date_trunc", exp.Literal.string(grain), column)
    return exp.cast(truncated, exp.DataType.build("DATE"))


def _metric(fact: str, sql: str, condition: str | None, dialect: Dialect = DIALECT) -> exp.Expr:
    node = _qualified(expression(sql, dialect), fact)
    if not condition:
        return node
    where = _qualified(expression(condition, dialect), fact)
    aggregates = [a for a in node.find_all(exp.AggFunc)]
    if dialect == "mysql":
        for aggregate in aggregates:
            _filter_inside(aggregate, where)
        return node
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


def _filter_inside(aggregate: exp.AggFunc, where: exp.Expr) -> None:
    """agg(x) FILTER (WHERE c) as agg(CASE WHEN c THEN x END), for MySQL: count(*) counts
    CASE WHEN c THEN 1 END, and DISTINCT applies to the CASE."""
    argument = aggregate.this
    if argument is None or isinstance(argument, exp.Star):
        aggregate.set("this", _case(where, exp.Literal.number(1)))
        return
    if isinstance(argument, exp.Distinct):
        argument.set("expressions", [_case(where, e) for e in argument.expressions])
        return
    aggregate.set("this", _case(where, argument))


def _case(where: exp.Expr, value: exp.Expr) -> exp.Expr:
    return exp.Case(ifs=[exp.If(this=where.copy(), true=value.copy())])


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


def duplicate_keys_sql(join: Join, dialect: Dialect = DIALECT) -> str:
    """How many values of the join's right-hand (one-side) key appear more than once: any is a
    fan-out, counting the fact rows that match it several times."""
    table, column = join.right.split(".")
    key = _column(table, column)
    select = exp.select(
        exp.Sub(
            this=exp.func("count", key.copy()),
            expression=exp.func("count", exp.Distinct(expressions=[key.copy()])),
        )
    ).from_(exp.to_table(table))
    return select.sql(dialect=dialect)


def orphan_keys_sql(join: Join, dialect: Dialect = DIALECT) -> str:
    """How many rows on the many side have a key the one side lacks (they show as NULL)."""
    left_table, left_column = join.left.split(".")
    right_table, right_column = join.right.split(".")
    on = exp.EQ(
        this=_column(left_table, left_column), expression=_column(right_table, right_column)
    )
    select = (
        exp.select(exp.func("count", exp.Star()))
        .from_(exp.to_table(left_table))
        .join(exp.to_table(right_table), on=on, join_type="left")
        .where(
            exp.and_(
                exp.Not(this=exp.Is(this=_column(left_table, left_column), expression=exp.Null())),
                exp.Is(this=_column(right_table, right_column), expression=exp.Null()),
            )
        )
    )
    return select.sql(dialect=dialect)

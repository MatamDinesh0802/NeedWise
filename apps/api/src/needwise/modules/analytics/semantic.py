"""The semantic layer (ARCHITECTURE section 16): what the BI agent may query, defined by admins in
YAML. Metrics, dimensions and joins name allow-listed tables; the model never sees anything else.

Who may query the model, and which rows they see, come from principals written as keys
(`department:finance`, `department-manager:sales`, `group:leadership`, `user:dan@acme.test`,
`org`). They are resolved to principal ids per tenant (resolve()), like collection grants.

Every SQL fragment in the YAML (dimensions, metrics, metric filters, row-policy filters) is
parsed with sqlglot when the model is loaded, so a typo or a reference to an unknown table or
column is an error for the admin, not a surprise at query time.

A model describes one dataset: the uploaded files (DuckDB SQL), or a PostgreSQL or MySQL
database (sources.py). The dataset decides the SQL dialect its fragments are written in
(`parse(text, dialect=...)`); in a database's model every table names the table it reads
(`source_table: schema.table`).
"""

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Annotated, Any, Literal, cast

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp
from sqlglot.errors import SqlglotError

from needwise.core.context import RequestContext
from needwise.modules.analytics.guard import (
    DIALECT,
    FORBIDDEN_NODES,
    Dialect,
    denied_function,
    unavailable_function,
)
from needwise.modules.identity.models import Department, Group, User

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# A table in a database, as `schema.table` (MySQL: `database.table`), each part as the database
# spells it.
SOURCE_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}\.[A-Za-z_][A-Za-z0-9_$]{0,62}$")
# Names DuckDB uses for its own catalog views, and the guard's own aliases.
RESERVED_PREFIXES = ("pg_", "duckdb_", "sqlite_", "pragma_", "information_schema", "_nw")
Format = Literal["eur", "number", "percent", "text", "date"]
ColumnType = Literal["text", "integer", "decimal", "date", "timestamp", "boolean"]
PRINCIPAL_KINDS = ("org", "department", "department-manager", "group", "user")
# Time grains available on every fact table with a time_column.
GRAINS = ("day", "week", "month", "quarter", "year")
# Row-policy placeholders -> the user attribute substituted, always as a string literal.
PLACEHOLDERS = {"{user.email}": "__nw_user_email__", "{user.id}": "__nw_user_id__"}


class ModelError(ValueError):
    """The YAML is not a valid semantic model; the message says why, for the admin."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Column(_Strict):
    type: ColumnType
    description: str = ""
    format: Format | None = None  # how values are shown (e.g. eur); default from the type


class Table(_Strict):
    description: str
    time_column: str | None = None  # the default date for time grains and date ranges
    columns: dict[str, Column] = Field(min_length=1)
    # Database sources only: the table this one reads, `schema.table` (MySQL: database.table).
    source_table: str | None = None

    @property
    def location(self) -> tuple[str, str] | None:
        """(schema, table) in the database, for a database source's table."""
        if self.source_table is None:
            return None
        schema, _, table = self.source_table.partition(".")
        return schema, table


class Join(_Strict):
    """left = right, many-to-one: `left` is table.column on the fact (many) side."""

    left: str
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
    applies_to: list[str] = Field(min_length=1)
    unless: list[str] = []
    filters: dict[str, str] = Field(min_length=1)


class SemanticModel(_Strict):
    name: str
    description: str
    data_through: date | None = None  # the last closed period, told to the model
    # Example questions shown to people who may query (GET /analytics/model).
    examples: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default=[], max_length=12
    )
    access: list[str] = Field(min_length=1)
    tables: dict[str, Table] = Field(min_length=1)
    joins: list[Join] = []
    dimensions: dict[str, Dimension] = {}
    metrics: dict[str, Metric] = Field(min_length=1)
    row_policies: list[RowPolicy] = []
    # Set by parse() from the dataset, never by the YAML: the SQL dialect of every fragment.
    dialect: Dialect = DIALECT

    @field_validator("tables", "dimensions", "metrics")
    @classmethod
    def _identifiers(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        bad = [k for k in value if not IDENTIFIER.match(k)]
        if bad:
            raise ValueError(f"not a lower-case identifier: {bad}")
        return value

    @field_validator("access")
    @classmethod
    def _access_keys(cls, value: list[str]) -> list[str]:
        for key in value:
            check_principal_key(key)
        return value

    @model_validator(mode="after")
    def _references(self) -> "SemanticModel":
        for name, table in self.tables.items():
            if name.startswith(RESERVED_PREFIXES):
                raise ValueError(
                    f"table {name}: names starting with {RESERVED_PREFIXES} are reserved"
                )
            if self.dialect == DIALECT and table.source_table is not None:
                raise ValueError(
                    f"table {name}: source_table is only for database sources; uploaded tables "
                    "are named by the table key"
                )
            if self.dialect != DIALECT:
                if table.source_table is None:
                    raise ValueError(
                        f"table {name}: add source_table, the table in the database (schema.table)"
                    )
                if not SOURCE_TABLE.match(table.source_table):
                    raise ValueError(
                        f"table {name}: source_table must be schema.table (letters, digits, "
                        "_ and $)"
                    )
            bad = [c for c in table.columns if not IDENTIFIER.match(c)]
            if bad:
                raise ValueError(f"table {name}: bad column names {bad}")
            if table.time_column:
                column = table.columns.get(table.time_column)
                if column is None:
                    raise ValueError(
                        f"table {name}: time_column {table.time_column} is not a column"
                    )
                if column.type not in ("date", "timestamp"):
                    raise ValueError(f"table {name}: time_column {table.time_column} is not a date")
        for join in self.joins:
            sides = [_split_column(side) for side in (join.left, join.right)]
            for (table, column), side in zip(sides, (join.left, join.right), strict=True):
                if column not in self.columns_of(table):
                    raise ValueError(f"join: unknown column {side}")
            if sides[0][0] == sides[1][0]:
                raise ValueError(f"join {join.left} = {join.right}: a table cannot join itself")
        clash = set(self.dimensions) & set(self.metrics)
        if clash:
            raise ValueError(f"names used for both a dimension and a metric: {sorted(clash)}")
        for name in [*self.dimensions, *self.metrics]:
            if name in GRAINS:
                raise ValueError(f"{name}: reserved for time grains ({', '.join(GRAINS)})")
        for name, dimension in self.dimensions.items():
            _check_dimension(self, name, dimension)
        locations = [t.source_table for t in self.tables.values() if t.source_table]
        if len(set(locations)) != len(locations):
            raise ValueError("two tables read the same source_table")
        for name, metric in self.metrics.items():
            if metric.table not in self.tables:
                raise ValueError(f"metric {name}: unknown table {metric.table}")
            _check_metric(self, name, metric)
        for policy in self.row_policies:
            for key in [*policy.applies_to, *policy.unless]:
                check_principal_key(key)
            unknown = set(policy.filters) - set(self.tables)
            if unknown:
                raise ValueError(f"row policy {policy.name}: unknown tables {sorted(unknown)}")
            for table, condition in policy.filters.items():
                _check_policy_filter(self, policy.name, table, condition)
        return self

    def columns_of(self, table: str) -> dict[str, Column]:
        found = self.tables.get(table)
        return found.columns if found else {}

    def join_path(self, fact: str, table: str) -> list[Join]:
        """Joins from the fact table to `table` (breadth-first over many-to-one joins)."""
        if table == fact:
            return []
        frontier: list[tuple[str, list[Join]]] = [(fact, [])]
        seen = {fact}
        while frontier:
            current, path = frontier.pop(0)
            for join in self.joins:
                left, right = _split_column(join.left)[0], _split_column(join.right)[0]
                if left == current and right not in seen:
                    if right == table:
                        return [*path, join]
                    seen.add(right)
                    frontier.append((right, [*path, join]))
        raise ValueError(f"{table} cannot be reached from {fact}")

    def dimension_tables(self, name: str) -> set[str]:
        node = expression(self.dimensions[name].sql, self.dialect)
        return {c.table for c in node.find_all(exp.Column)}

    def relations(self) -> dict[str, tuple[str, str]]:
        """Model table -> (schema, table) in the database; empty for uploaded files."""
        return {name: loc for name, table in self.tables.items() if (loc := table.location)}

    def column_format(self, table: str, column: str) -> Format | None:
        found = self.columns_of(table).get(column)
        return found.format if found else None


def check_principal_key(key: str) -> None:
    kind, sep, value = key.partition(":")
    if kind not in PRINCIPAL_KINDS:
        raise ValueError(f"unknown principal key {key!r} (use one of {', '.join(PRINCIPAL_KINDS)})")
    if (kind == "org" and sep) or (kind != "org" and not value.strip()):
        raise ValueError(f"principal key {key!r}: write `org`, or `{kind}:<name>`")


def _split_column(qualified: str) -> tuple[str, str]:
    table, _, column = qualified.partition(".")
    return table, column


@lru_cache(maxsize=512)
def _parsed(sql: str, dialect: Dialect) -> exp.Expr:
    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
    except SqlglotError as exc:
        raise ValueError(f"not valid SQL: {sql!r}") from exc
    return parsed


def expression(sql: str, dialect: Dialect = DIALECT) -> exp.Expr:
    """A fresh copy of a parsed SQL fragment from the model (callers may modify it)."""
    return _parsed(sql, dialect).copy()


def _check_fragment(
    where: str, node: exp.Expr, dialect: Dialect, *, subqueries: bool = False
) -> None:
    if isinstance(node, exp.Query):
        raise ValueError(f"{where}: must be an expression, not a statement")
    for part in node.walk():
        if isinstance(part, FORBIDDEN_NODES):
            raise ValueError(f"{where}: must be an expression, not a statement")
        if isinstance(part, exp.Func) and (name := denied_function(part, dialect)):
            raise ValueError(f"{where}: the function {name}() is not allowed")
        if reason := unavailable_function(part, dialect):
            raise ValueError(f"{where}: {reason}")
        if isinstance(part, exp.Query) and not subqueries:
            raise ValueError(f"{where}: subqueries are not allowed")
        if isinstance(part, exp.Window):
            raise ValueError(f"{where}: window functions are not allowed")
        if isinstance(part, exp.Placeholder | exp.Parameter):
            raise ValueError(f"{where}: parameters are not allowed")


def _check_dimension(model: SemanticModel, name: str, dimension: Dimension) -> None:
    where = f"dimension {name}"
    node = expression(dimension.sql, model.dialect)
    _check_fragment(where, node, model.dialect)
    if any(isinstance(part, exp.AggFunc) for part in node.walk()):
        raise ValueError(f"{where}: aggregates belong in metrics")
    columns = list(node.find_all(exp.Column))
    if not columns:
        raise ValueError(f"{where}: refers to no column")
    for column in columns:
        if not column.table:
            raise ValueError(f"{where}: qualify {column.name} with its table")
        if column.name not in model.columns_of(column.table):
            raise ValueError(f"{where}: unknown column {column.table}.{column.name}")


def _check_metric(model: SemanticModel, name: str, metric: Metric) -> None:
    where = f"metric {name}"
    node = expression(metric.sql, model.dialect)
    _check_fragment(where, node, model.dialect)
    if not any(isinstance(part, exp.AggFunc) for part in node.walk()):
        raise ValueError(f"{where}: sql must aggregate (sum, count, avg, ...)")
    fragments = [node]
    if metric.filter:
        condition = expression(metric.filter, model.dialect)
        _check_fragment(f"{where} filter", condition, model.dialect)
        if any(isinstance(part, exp.AggFunc) for part in condition.walk()):
            raise ValueError(f"{where}: the filter is a row condition, not an aggregate")
        fragments.append(condition)
    columns = model.columns_of(metric.table)
    for fragment in fragments:
        for column in fragment.find_all(exp.Column):
            if column.table and column.table != metric.table:
                raise ValueError(f"{where}: only columns of {metric.table} can be aggregated")
            if column.name not in columns:
                raise ValueError(f"{where}: unknown column {metric.table}.{column.name}")


def policy_expression(condition: str, dialect: Dialect = DIALECT) -> exp.Expr:
    """A row-policy condition with its placeholders as sentinel columns (see row_filters)."""
    text = condition
    for placeholder, sentinel in PLACEHOLDERS.items():
        text = text.replace(placeholder, sentinel)
    return expression(text, dialect)


def _check_policy_filter(model: SemanticModel, policy: str, table: str, condition: str) -> None:
    where = f"row policy {policy}, table {table}"
    node = policy_expression(condition, model.dialect)
    _check_fragment(where, node, model.dialect, subqueries=True)
    sentinels = set(PLACEHOLDERS.values())
    for literal in node.find_all(exp.Literal):
        if literal.is_string and any(s in literal.this for s in sentinels):
            raise ValueError(f"{where}: write {{user.email}} without quotes")
    for part in node.walk():
        if isinstance(part, exp.With):
            raise ValueError(f"{where}: CTEs are not allowed")
        if isinstance(part, exp.AggFunc) and part.find_ancestor(exp.Query) is None:
            raise ValueError(f"{where}: must be a row condition")
        if isinstance(part, exp.Table):
            if not isinstance(part.this, exp.Identifier) or part.db or part.catalog:
                raise ValueError(f"{where}: refer to model tables by their plain name")
            if part.name not in model.tables:
                raise ValueError(f"{where}: unknown table {part.name}")
    for column in node.find_all(exp.Column):
        if column.name in sentinels and not column.table:
            continue
        scopes = _column_scopes(column, table)
        if column.table:
            if column.table not in scopes:
                raise ValueError(f"{where}: unknown table or alias {column.table}")
            if column.name not in model.columns_of(scopes[column.table]):
                raise ValueError(f"{where}: unknown column {column.table}.{column.name}")
        else:
            # Innermost scope only: a misspelt column must not silently bind to an outer query.
            innermost = _innermost_tables(column, table)
            if not any(column.name in model.columns_of(t) for t in innermost):
                raise ValueError(f"{where}: unknown column {column.name}")


def _select_tables(select_: exp.Expr) -> dict[str, str]:
    """alias or name -> table, for the tables in this SELECT's FROM and JOINs."""
    sources: dict[str, str] = {}
    from_ = select_.args.get("from_")
    joins: list[exp.Expr] = list(select_.args.get("joins") or [])
    for source in [from_.this if isinstance(from_, exp.From) else None, *(j.this for j in joins)]:
        if isinstance(source, exp.Table):
            sources[source.alias_or_name] = source.name
    return sources


def _column_scopes(column: exp.Column, table: str) -> dict[str, str]:
    scopes = {table: table}
    for select_ in _enclosing_selects(column):
        scopes.update(_select_tables(select_))
    return scopes


def _innermost_tables(column: exp.Column, table: str) -> list[str]:
    selects = _enclosing_selects(column)
    return list(_select_tables(selects[0]).values()) if selects else [table]


def _enclosing_selects(node: exp.Expr) -> list[exp.Select]:
    found: list[exp.Select] = []
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Select):
            found.append(parent)
        parent = parent.parent
    return found


def parse(text: str, dialect: Dialect = DIALECT) -> SemanticModel:
    """Parse and validate a semantic model from YAML, its SQL in `dialect` (the dataset's: duckdb
    for uploaded files, postgres or mysql for a database). Raises ModelError with a readable
    reason."""
    return _parse_cached(text, dialect)


@lru_cache(maxsize=64)
def _parse_cached(text: str, dialect: Dialect) -> SemanticModel:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ModelError(f"not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ModelError("the semantic model must be a YAML mapping")
    if "dialect" in raw:
        raise ModelError("dialect: set by the dataset (uploaded files or a database), not here")
    raw = {**cast(dict[str, Any], raw), "dialect": dialect}
    try:
        return SemanticModel.model_validate(raw)
    except ValidationError as exc:
        problems: list[str] = []
        for error in exc.errors():
            where = ".".join(str(p) for p in error["loc"])
            message = str(error["msg"]).removeprefix("Value error, ")
            problems.append(f"{where}: {message}" if where else message)
        raise ModelError("; ".join(problems)) from exc


@dataclass(frozen=True)
class ResolvedPolicy:
    policy: RowPolicy
    applies: frozenset[str]
    unless: frozenset[str]


@dataclass(frozen=True)
class Resolved:
    """A model whose principal keys are resolved to this tenant's principal ids."""

    model: SemanticModel
    access: frozenset[str]
    policies: tuple[ResolvedPolicy, ...]
    unresolved: tuple[str, ...]  # keys that match nothing in this tenant (a typo, or deleted)


async def resolve(session: AsyncSession, model: SemanticModel, tenant_id: uuid.UUID) -> Resolved:
    """Must run inside the tenant's transaction (Row-Level Security scopes the lookups)."""
    unresolved: list[str] = []

    async def ids(keys: list[str]) -> frozenset[str]:
        out: set[str] = set()
        for key in keys:
            found = await _principal(session, key, tenant_id)
            if not found:
                unresolved.append(key)
            out |= found
        return frozenset(out)

    access = await ids(model.access)
    policies = tuple(
        [
            ResolvedPolicy(policy=p, applies=await ids(p.applies_to), unless=await ids(p.unless))
            for p in model.row_policies
        ]
    )
    return Resolved(
        model=model,
        access=access,
        policies=policies,
        unresolved=tuple(dict.fromkeys(unresolved)),
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


def applicable_policies(resolved: Resolved, ctx: RequestContext) -> list[RowPolicy]:
    """The row policies that restrict this user (applies to them, and no exemption)."""
    return [
        r.policy
        for r in resolved.policies
        if (r.applies & ctx.principals) and not (r.unless & ctx.principals)
    ]


def row_filters(resolved: Resolved, ctx: RequestContext, email: str) -> dict[str, exp.Expr]:
    """table -> SQL condition for this user (several policies on a table are ANDed). The user's
    attributes are substituted into the parsed condition as string literals, never spliced into
    SQL text; unqualified columns at the top of a condition are qualified with its table."""
    values = {
        PLACEHOLDERS["{user.email}"]: email,
        PLACEHOLDERS["{user.id}"]: str(ctx.user_id),
    }
    filters: dict[str, list[exp.Expr]] = {}
    for policy in applicable_policies(resolved, ctx):
        for table, condition in policy.filters.items():
            node = policy_expression(condition, resolved.model.dialect)
            for column in list(node.find_all(exp.Column)):
                if not column.table and column.name in values:
                    column.replace(exp.Literal.string(values[column.name]))
                elif not column.table and column.find_ancestor(exp.Select) is None:
                    column.set("table", exp.to_identifier(table))
            filters.setdefault(table, []).append(exp.paren(node, copy=False))
    return {
        table: parts[0] if len(parts) == 1 else exp.and_(*parts, copy=False)
        for table, parts in filters.items()
    }

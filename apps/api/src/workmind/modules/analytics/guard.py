"""The SQL guard (ARCHITECTURE section 16): every query the BI agent runs passes through here,
whether a person, the language model or the semantic-layer compiler wrote it.

check() accepts exactly one read-only query over the semantic model's tables and returns the
SQL to execute, rebuilt by sqlglot from the parsed tree (the text that was sent is never run):

1. Parse with sqlglot (DuckDB dialect). Exactly one statement, and it must be a query: SELECT,
   UNION / INTERSECT / EXCEPT, with CTEs. No DDL, DML, COPY, ATTACH, PRAGMA, SET, INSTALL, LOAD,
   CALL, EXPORT, transactions, SUMMARIZE, DESCRIBE, SHOW or SELECT ... INTO, at any depth.
2. Every table reference is a plain name, optionally `main.<name>`, of a table in the model, or
   a CTE visible at that point (resolved with DuckDB's scoping rules, including when a
   RECURSIVE CTE really is recursive). No table functions (read_csv, glob, range, ...), file
   paths, other schemas or catalogs (information_schema, pg_catalog, duckdb_*), and no CTE may
   be named like a model table or start with the reserved prefix `_wm_`.
3. No function from a deny-list of file, network, configuration and catalog functions.
4. Row policies, as fences: for every table with a row filter for this user, the engine first
   copies the rows the user may see into a temporary table (`Fence`: `CREATE TEMP TABLE
   _wm_rows_<table> AS SELECT * FROM <table> WHERE <filter>`, a statement of its own), and every
   reference to the table becomes `_wm_rows_<table> AS <alias or table name>`. The query never
   reads the table itself, so no plan DuckDB picks can run the query's own conditions (an
   error(), a failing CAST) on a hidden row: joins, subqueries, window functions and recursive
   CTEs only ever see the rows the user may see.
5. A row cap: LIMIT min(existing, cap + 1), so the caller can tell when a result was truncated.

The engine (engine.py) is the second lock: even SQL that skipped this guard cannot touch files,
the network or settings.

A PostgreSQL or MySQL source (sources.py, docs/analytics-sql-sources.md) is checked the same way
in its own dialect (`dialect=`), with these differences:
- Functions sqlglot does not know (`exp.Anonymous`) must be on that dialect's allow-list of
  ordinary built-ins: a database can hold functions of its own, with any side effect
  (pg_sleep, dblink, lo_import, LOAD_FILE, SLEEP, BENCHMARK and user-defined ones are all
  unknown names). Qualified function calls (`schema.f()`), variables (`@x`, `@@version`) and
  assignments are refused.
- Every reference to a model table becomes the table it reads in the database
  (`"schema"."table" AS <alias>`, from `relations`).
- A row policy cannot copy rows into a temporary table (the session is read-only, and a
  read-only role may not create one), so the fence is a CTE of its own at the head of the
  query: `WITH _wm_rows_<t> AS MATERIALIZED (SELECT * FROM "schema"."t" WHERE <filter>)` on
  PostgreSQL, where a MATERIALIZED CTE is planned on its own and no condition of the outer
  query is pushed into it; on MySQL the session turns off derived-table merging and condition
  pushdown (remote.py), which has the same effect. Every reference reads the fence.
"""

import fnmatch
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

Dialect = Literal["duckdb", "postgres", "mysql"]
DIALECT: Dialect = "duckdb"  # uploaded files; databases bring their own (sources.py)
MAX_SQL_CHARS = 20_000
RESERVED_PREFIX = "_wm_"  # names the guard and the engine generate; refused in queries
RESULT_ALIAS = "_wm_result"
FENCE_PREFIX = "_wm_rows_"

# Lower-case shell patterns, matched against every function name in the query.
DENIED_FUNCTIONS: tuple[str, ...] = (
    "read_*",  # read_csv, read_text, read_blob, read_parquet, read_json, ...
    "*_scan",  # parquet_scan, postgres_scan, sqlite_scan, seq_scan, ...
    "glob",
    "getenv",
    "current_setting",
    "query",
    "query_table",
    "sniff_csv",
    "load",
    "install",
    "duckdb_*",
    "pragma_*",
    "parquet_*",
    "iceberg_*",
    "delta_*",
    "postgres_*",
    "mysql_*",
    "sqlite_*",
    "ducklake_*",
    "http*",
    "json_execute*",
    "json_serialize*",
    "json_deserialize*",
    "system",
    "which_secret",
    "*secret*",
    "getvariable",
    "setvariable",
    "checkpoint",
    "force_checkpoint",
    "write_*",
    "export_*",
    "import_*",
    "copy_*",
    "attach*",
    "detach*",
    "enable_*",
    "disable_*",
    "truncate_*",
    "nextval",
    "currval",
    "setval",
    "pg_*",
    "sql_auto_complete",
    "error",  # raises with any text: an oracle on the rows it is evaluated on
    # Database sources (PostgreSQL, MySQL): waiting, files, locks, other connections, settings,
    # SQL in a string, and who the connection is. Most are unknown names there anyway (refused
    # unless allow-listed, see ALLOWED_FUNCTIONS); this list holds whatever the dialect.
    "sleep",
    "benchmark",
    "load_file",
    "*_lock",
    "release_all_locks",
    "*_pos_wait",
    "wait_for_executed_gtid_set",
    "wait_until_sql_thread_after_gtids",
    "sys_*",
    "lo_*",
    "dblink*",
    "set_config",
    "*_to_xml*",
    "*xmlschema*",
    "ts_stat",
    "ts_rewrite",
    "lastval",
    "txid_*",
    "inet_*",
    "current_user*",
    "current_session",
    "current_organization*",
    "next_value_for",
    "session_user",
    "system_user",
    "current_role",
    "current_database",
    "current_schema*",
    "current_catalog",
    "connection_id",
    "database",
    "schema",
    "user",
)

# Functions sqlglot does not know by type (exp.Anonymous) that a database source may call: common
# built-ins only, lower case. Anything else unknown is refused (a database can hold functions of
# its own); known functions pass unless DENIED_FUNCTIONS lists them.
ALLOWED_FUNCTIONS: dict[str, frozenset[str]] = {
    "postgres": frozenset(
        [
            "age",
            "array_length",
            "array_position",
            "array_remove",
            "array_to_string",
            "ascii",
            "asin",
            "atan",
            "atan2",
            "bit_length",
            "btrim",
            "cardinality",
            "cbrt",
            "char_length",
            "character_length",
            "chr",
            "corr",
            "cos",
            "covar_pop",
            "covar_samp",
            "cume_dist",
            "date_bin",
            "degrees",
            "div",
            "every",
            "factorial",
            "gcd",
            "initcap",
            "isfinite",
            "json_agg",
            "json_build_object",
            "json_object_agg",
            "jsonb_agg",
            "jsonb_build_object",
            "jsonb_object_agg",
            "justify_days",
            "justify_hours",
            "justify_interval",
            "lcm",
            "make_date",
            "make_interval",
            "make_time",
            "make_timestamp",
            "mode",
            "num_nonnulls",
            "num_nulls",
            "octet_length",
            "percent_rank",
            "pi",
            "radians",
            "regexp_match",
            "regexp_matches",
            "regexp_split_to_array",
            "regr_count",
            "regr_intercept",
            "regr_r2",
            "regr_slope",
            "scale",
            "sin",
            "starts_with",
            "string_to_array",
            "tan",
            "timezone",
            "to_hex",
            "to_number",
            "trim_scale",
            "width_bucket",
        ]
    ),
    "mysql": frozenset(
        [
            "adddate",
            "addtime",
            "any_value",
            "bin",
            "bit_xor",
            "conv",
            "curdate",
            "current_time",
            "curtime",
            "date",
            "dayname",
            "elt",
            "field",
            "find_in_set",
            "format",
            "from_days",
            "json_array",
            "json_arrayagg",
            "json_contains",
            "json_keys",
            "json_length",
            "json_object",
            "json_objectagg",
            "json_type",
            "json_unquote",
            "json_valid",
            "last_day",
            "lcase",
            "localtime",
            "localtimestamp",
            "makedate",
            "maketime",
            "microsecond",
            "mid",
            "monthname",
            "now",
            "oct",
            "period_add",
            "period_diff",
            "sec_to_time",
            "space",
            "std",
            "strcmp",
            "subdate",
            "time",
            "time_format",
            "time_to_sec",
            "timediff",
            "timestamp",
            "to_days",
            "to_seconds",
            "ucase",
            "unix_timestamp",
            "utc_date",
            "utc_time",
            "utc_timestamp",
            "week",
            "weekday",
            "weekofyear",
            "yearweek",
        ]
    ),
}


def _classes(*names: str) -> tuple[type[exp.Expr], ...]:
    """sqlglot classes by name; names a given version lacks are skipped."""
    found: list[type[exp.Expr]] = []
    for name in names:
        cls = getattr(exp, name, None)
        if isinstance(cls, type) and issubclass(cls, exp.Expr):
            found.append(cls)
    return tuple(found)


# Never allowed anywhere in the tree (statements, and query parts with side effects).
FORBIDDEN_NODES = _classes(
    "DDL",
    "DML",
    "Command",
    "Create",
    "Drop",
    "Alter",
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "TruncateTable",
    "Copy",
    "Attach",
    "Detach",
    "Pragma",
    "Set",
    "Install",
    "Use",
    "Transaction",
    "Commit",
    "Rollback",
    "Export",
    "LoadData",
    "Summarize",
    "Describe",
    "Show",
    "Into",
    "Lock",
    "Kill",
    "Cache",
    "Uncache",
    "Refresh",
    "Analyze",
    "Grant",
    "Revoke",
    "Comment",
    "Declare",
    "Execute",
    "Pivot",
    "Placeholder",
    "Parameter",
    "SessionParameter",
    "TableFromRows",
)
QUERY_NODES = (exp.Select, exp.SetOperation)
# What may stand in FROM or JOIN, besides a (checked) table name.
SOURCE_NODES = (exp.Table, exp.Subquery, exp.Unnest, exp.Lateral, exp.Values)
# Table arguments other than the name, schema, catalog and alias (TABLESAMPLE, AT (VERSION),
# PIVOT, parenthesized joins, ...) are refused rather than carried through the rewrite.
TABLE_ARGS = frozenset({"this", "db", "catalog", "alias"})
_CALL = re.compile(r'\s*"?([A-Za-z_][A-Za-z0-9_]*)"?\s*\(')


class QueryRejectedError(Exception):
    """The query is refused; `reason` is meant for the person (or model) who wrote it."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Fence:
    """The rows of a restricted table this user may see, which the engine copies into a
    temporary table before the query runs (its own statement: nothing in the query can be
    evaluated on the rows it leaves out)."""

    name: str  # the temporary table, _wm_rows_<table>
    table: str  # the model table
    sql: str  # SELECT * FROM <table> WHERE <the user's row filter>


@dataclass(frozen=True)
class GuardedQuery:
    # What to execute, after the fences: row policies applied, row cap enforced. For a database
    # source, the fences are CTEs inside it and it reads the database's own tables.
    sql: str
    query: str  # the checked query as written (normalized), before policies and the cap
    tables: tuple[str, ...]  # model tables the query reads
    filtered: tuple[str, ...]  # of those, the ones a row policy restricted
    fences: tuple[Fence, ...] = ()  # one per restricted table the query reads

    @property
    def statements(self) -> str:
        """Everything the engine runs, in order, as one text (to show and to audit)."""
        created = [f"CREATE TEMP TABLE {f.name} AS {f.sql}" for f in self.fences]
        return ";\n".join([*created, self.sql])


def check(
    sql: str,
    *,
    tables: Collection[str],
    row_filters: Mapping[str, exp.Expr] | None = None,
    max_rows: int = 1000,
    dialect: Dialect = DIALECT,
    relations: Mapping[str, tuple[str, str]] | None = None,
) -> GuardedQuery:
    """Validate `sql` against the allow-listed `tables` (lower-case names) and rewrite it with
    this user's `row_filters` (table -> condition, user values already literals).

    A database source passes its `dialect` and `relations`: model table -> (schema, table) in the
    database, for every model table."""
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    allowed = frozenset(t.lower() for t in tables)
    filters = {t.lower(): f for t, f in (row_filters or {}).items()}
    unknown = set(filters) - allowed
    if unknown:
        raise ValueError(f"row filters for tables outside the model: {sorted(unknown)}")
    if dialect != DIALECT:
        located = {t.lower(): loc for t, loc in (relations or {}).items()}
        if frozenset(located) != allowed:
            raise ValueError("a database source needs the location of every model table")
        return _check_external(sql, allowed, filters, max_rows, dialect, located)

    try:
        tree = _parse_one(sql)
        used = validate(tree, allowed)
        query = tree.sql(dialect=DIALECT, comments=False)

        fenced = _apply_row_filters(tree, filters)
        capped = _cap(tree, max_rows + 1)
        final = capped.sql(dialect=DIALECT, comments=False)

        # Defence in depth: what will run must itself parse back to one valid query, unchanged,
        # that reads no restricted table except through its fence.
        reparsed = _parse_one(final)
        validate(reparsed, (allowed - set(filters)) | {fence_name(t) for t in fenced})
        if reparsed.sql(dialect=DIALECT, comments=False) != final:
            raise QueryRejectedError("The query could not be rewritten safely.")
        fences = tuple(_fence(table, filters[table], allowed) for table in sorted(fenced))
    except RecursionError as exc:  # sqlglot parses and walks recursively
        raise QueryRejectedError("The query is nested too deeply.") from exc

    return GuardedQuery(
        sql=final,
        query=query,
        tables=tuple(sorted(used)),
        filtered=tuple(sorted(used & set(filters))),
        fences=fences,
    )


def _check_external(
    sql: str,
    allowed: frozenset[str],
    filters: Mapping[str, exp.Expr],
    max_rows: int,
    dialect: Dialect,
    relations: Mapping[str, tuple[str, str]],
) -> GuardedQuery:
    """check() for a database source: fences as CTEs, model tables as the database's tables."""
    try:
        tree = _parse_one(sql, dialect)
        used = validate(tree, allowed, dialect=dialect)
        query = tree.sql(dialect=dialect, comments=False)

        fenced = _apply_row_filters(tree, filters)
        _apply_relations(tree, relations)
        ctes = [_fence_cte(t, filters[t], relations, dialect) for t in sorted(fenced)]
        tree = _with_ctes(tree, ctes)
        capped = _cap(tree, max_rows + 1)
        final = capped.sql(dialect=dialect, comments=False)

        # Defence in depth: what will run parses back to itself, reads only the model's tables
        # in the database, and reads a restricted one only inside its fence.
        reparsed = _parse_one(final, dialect)
        if reparsed.sql(dialect=dialect, comments=False) != final:
            raise QueryRejectedError("The query could not be rewritten safely.")
        physical = frozenset(relations.values())
        validate(reparsed, frozenset(), dialect=dialect, physical=physical)
        restricted = {relations[t] for t in filters}
        for table in reparsed.find_all(exp.Table):
            if (table.db, table.name) in restricted and not _inside_fence(table):
                raise QueryRejectedError("The query could not be rewritten safely.")
    except RecursionError as exc:  # sqlglot parses and walks recursively
        raise QueryRejectedError("The query is nested too deeply.") from exc

    return GuardedQuery(
        sql=final,
        query=query,
        tables=tuple(sorted(used)),
        filtered=tuple(sorted(used & set(filters))),
    )


def _physical(table: str, relations: Mapping[str, tuple[str, str]]) -> exp.Table:
    schema, name = relations[table]
    return exp.Table(
        this=exp.to_identifier(name, quoted=True),
        db=exp.to_identifier(schema, quoted=True),
    )


def _apply_relations(tree: exp.Expr, relations: Mapping[str, tuple[str, str]]) -> None:
    """Point every reference to a model table at the database table it reads, under the same
    alias (or the model table's name, so `orders.amount` still resolves)."""
    targets = [
        t
        for t in tree.find_all(exp.Table)
        if not t.args.get("db") and not is_cte_reference(t) and t.name.lower() in relations
    ]
    for table in targets:
        alias = table.args.get("alias")
        replacement = _physical(table.name.lower(), relations)
        replacement.set(
            "alias",
            alias.copy()
            if isinstance(alias, exp.TableAlias)
            else exp.TableAlias(this=exp.to_identifier(table.name.lower())),
        )
        table.replace(replacement)


def _fence_cte(
    table: str,
    condition: exp.Expr,
    relations: Mapping[str, tuple[str, str]],
    dialect: Dialect,
) -> exp.CTE:
    """The rows of `table` this user may see, as a CTE of its own (PostgreSQL: MATERIALIZED,
    so it is planned alone and no outer condition reaches a hidden row)."""
    where = condition.copy()
    holder = exp.Paren(this=where)  # a parent, so a bare table reference could be replaced
    _apply_relations(holder, relations)  # tables in the policy's own subqueries
    source = _physical(table, relations)
    source.set("alias", exp.TableAlias(this=exp.to_identifier(table)))
    select = exp.select("*").from_(source).where(holder.this)
    return exp.CTE(
        this=select,
        alias=exp.TableAlias(this=exp.to_identifier(fence_name(table))),
        materialized=True if dialect == "postgres" else None,
    )


def _with_ctes(tree: exp.Expr, ctes: list[exp.CTE]) -> exp.Expr:
    """`tree` with `ctes` first in its WITH (so every CTE and subquery of it can see them)."""
    if not ctes:
        return tree
    if not isinstance(tree, QUERY_NODES):
        alias = exp.TableAlias(this=exp.to_identifier(RESULT_ALIAS))
        tree = exp.select("*").from_(exp.Subquery(this=tree, alias=alias))
    existing = tree.args.get("with_")
    if isinstance(existing, exp.With):
        existing.set("expressions", [*ctes, *existing.expressions])
    else:
        tree.set("with_", exp.With(expressions=ctes))
    return tree


def _inside_fence(node: exp.Expr) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.CTE) and parent.alias_or_name.startswith(FENCE_PREFIX):
            return True
        parent = parent.parent
    return False


def fence_name(table: str) -> str:
    return f"{FENCE_PREFIX}{table.lower()}"


def _fence(table: str, condition: exp.Expr, tables: frozenset[str]) -> Fence:
    """The statement that selects the rows of `table` this user may see. The condition is the
    admin's policy (validated when the model was saved), with the user's values as literals."""
    select = exp.select("*").from_(exp.Table(this=exp.to_identifier(table))).where(condition.copy())
    sql = select.sql(dialect=DIALECT, comments=False)
    validate(_parse_one(sql), tables)
    return Fence(name=fence_name(table), table=table, sql=sql)


def _parse_one(sql: str, dialect: Dialect = DIALECT) -> exp.Expr:
    if len(sql) > MAX_SQL_CHARS:
        raise QueryRejectedError(f"The query is too long (over {MAX_SQL_CHARS} characters).")
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect) if s is not None]
    except SqlglotError as exc:
        raise QueryRejectedError(f"The query is not valid SQL: {_first_line(str(exc))}") from exc
    if not statements:
        raise QueryRejectedError("The query is empty.")
    if len(statements) > 1:
        raise QueryRejectedError("Send exactly one statement (a single SELECT query).")
    return statements[0]


def validate(
    tree: exp.Expr,
    tables: frozenset[str],
    *,
    dialect: Dialect = DIALECT,
    physical: frozenset[tuple[str, str]] | None = None,
) -> set[str]:
    """Raise QueryRejectedError unless `tree` is one read-only query over `tables`. Returns the
    model tables it reads (lower-case).

    `physical`: check SQL about to run on a database source instead, whose table references
    are the database's (schema, table) pairs and whose fences are CTEs."""
    top = tree
    while isinstance(top, exp.Subquery) and not top.alias:
        top = top.this
    if not isinstance(top, QUERY_NODES):
        raise QueryRejectedError("Only read-only queries are allowed (SELECT, with optional CTEs).")

    for node in tree.walk():
        if isinstance(node, exp.SessionParameter) or (
            isinstance(node, exp.Parameter) and dialect == "mysql"
        ):
            raise QueryRejectedError("Variables are not allowed: write values inline.")
        if isinstance(node, exp.Placeholder | exp.Parameter):
            raise QueryRejectedError("Query parameters are not supported: write values inline.")
        if reason := unavailable_function(node, dialect):
            raise QueryRejectedError(reason[0].upper() + reason[1:] + ".")
        if isinstance(node, exp.Pivot):
            raise QueryRejectedError("PIVOT is not supported: use GROUP BY with FILTER instead.")
        if isinstance(node, FORBIDDEN_NODES):
            raise QueryRejectedError(
                f"{_describe(node)} is not allowed: only read-only queries run."
            )
        if isinstance(node, exp.Select) and node.args.get("into"):
            raise QueryRejectedError("SELECT ... INTO is not allowed: only read-only queries run.")
        if isinstance(node, exp.Subquery | exp.CTE) and not isinstance(
            node.this, (*QUERY_NODES, exp.Subquery, exp.Values)
        ):
            raise QueryRejectedError("Subqueries must be SELECT queries.")
        if isinstance(node, exp.From | exp.Join) and not isinstance(node.this, SOURCE_NODES):
            raise QueryRejectedError("Only tables of the data model and subqueries can be queried.")
        if isinstance(node, exp.Lateral) and not isinstance(
            node.this, exp.Subquery | exp.Unnest | exp.Select
        ):
            raise QueryRejectedError("LATERAL is only allowed with a subquery.")
        if isinstance(node, exp.Func) and (name := denied_function(node, dialect)):
            raise QueryRejectedError(f"The function {name}() is not allowed.")
        if isinstance(node, exp.CTE):
            cte = node.alias_or_name.lower()
            if cte in tables:
                raise QueryRejectedError(
                    f"The CTE name {cte!r} is the name of a table; choose another name."
                )
            fence = physical is not None and cte.startswith(FENCE_PREFIX)
            if cte.startswith(RESERVED_PREFIX) and not fence:
                raise QueryRejectedError(
                    f"CTE names starting with {RESERVED_PREFIX} are reserved; choose another name."
                )

    used: set[str] = set()
    for table in tree.find_all(exp.Table):
        if is_cte_reference(table):
            continue
        if physical is not None:
            _check_physical(table, physical)
            continue
        used.add(_check_table(table, tables, dialect))
    return used


def _check_physical(table: exp.Table, physical: frozenset[tuple[str, str]]) -> None:
    extra = sorted(k for k, v in table.args.items() if v and k not in TABLE_ARGS)
    if (
        not isinstance(table.this, exp.Identifier)
        or extra
        or table.catalog
        or (table.db, table.name) not in physical
    ):
        raise QueryRejectedError("The query could not be rewritten safely.")


def unavailable_function(node: exp.Expr, dialect: Dialect) -> str | None:
    """Why a database source refuses this node (None: no objection, or DuckDB): an unknown
    function not on the dialect's allow-list, a qualified function call, an assignment."""
    if dialect == DIALECT:
        return None
    if isinstance(node, exp.Dot):
        return "qualified names such as schema.function() are not allowed"
    if isinstance(node, exp.PropertyEQ):
        return "assignments are not allowed"
    if isinstance(node, exp.Anonymous):
        name = node.name.lower()
        if name not in ALLOWED_FUNCTIONS.get(dialect, frozenset()):
            return (
                f"the function {name}() is not available: use the database's common built-in "
                "functions (aggregates, dates, text, numbers)"
            )
    return None


def _check_table(table: exp.Table, tables: frozenset[str], dialect: Dialect = DIALECT) -> str:
    if not isinstance(table.this, exp.Identifier):
        name = table.this.sql(dialect=DIALECT) if table.this else "?"
        raise QueryRejectedError(
            f"Table functions and files cannot be queried ({_first_line(name)[:60]}); "
            "use the tables of the data model."
        )
    extra = sorted(k for k, v in table.args.items() if v and k not in TABLE_ARGS)
    if extra:
        raise QueryRejectedError(
            f"Unsupported table syntax on {table.name} ({', '.join(extra)}): "
            "no TABLESAMPLE, PIVOT, time travel or parenthesized joins."
        )
    name = table.name.lower()
    main = dialect == DIALECT and table.db.lower() == "main"
    if table.catalog or (table.db and not main):
        qualified = ".".join(p for p in (table.catalog, table.db, table.name) if p)
        raise QueryRejectedError(
            f"{qualified} is outside the data model: refer to tables by their plain name."
        )
    if name not in tables:
        raise QueryRejectedError(
            f"Unknown table {table.name!r}. Tables you can query: {', '.join(sorted(tables))}."
        )
    return name


def is_cte_reference(table: exp.Table) -> bool:
    """Whether DuckDB resolves this table name to a CTE (never a qualified name).

    A CTE is visible in the body of the query its WITH belongs to (including nested
    subqueries), and inside the WITH only to the CTEs after it. With RECURSIVE, it is visible
    to itself only where DuckDB treats it as recursive: its body is a UNION [ALL] (not BY NAME,
    INTERSECT or EXCEPT) and the reference is in the right-hand side of that UNION. DuckDB binds
    anything else, including a forward reference or `WITH RECURSIVE x AS (SELECT * FROM x)`, to a
    real table or catalog view, and so does this function: such a name then has to be a model
    table."""
    if table.args.get("db") or table.args.get("catalog"):
        return False
    name = table.name.lower()
    path: list[exp.Expr] = [table]  # from the table up to (excluding) `node`
    node = table.parent
    while node is not None:
        with_ = node.args.get("with_")
        if isinstance(with_, exp.With):
            ctes: list[exp.Expr] = list(with_.expressions)
            names = [c.alias_or_name.lower() for c in ctes]
            if path[-1] is with_:  # the reference is inside one of these CTEs
                below = path[-2] if len(path) > 1 else None
                index = next((i for i, c in enumerate(ctes) if c is below), None)
                visible = [] if index is None else names[:index]
                if (
                    index is not None
                    and with_.args.get("recursive")
                    and _in_recursive_term(ctes[index], path)
                ):
                    visible.append(names[index])
            else:
                visible = names
            if name in visible:
                return True
        path.append(node)
        node = node.parent
    return False


def _in_recursive_term(cte: exp.Expr, path: list[exp.Expr]) -> bool:
    """Whether a reference (whose ancestors are `path`) is in the recursive term of `cte`: the
    right-hand side of the UNION that is the CTE's body. That is the only place DuckDB binds
    a CTE's own name to itself."""
    body = cte.this
    while isinstance(body, exp.Subquery) and not body.alias:
        body = body.this
    if type(body) is not exp.Union or body.args.get("by_name"):
        return False
    recursive = body.expression
    return any(node is recursive for node in path)


def denied_function(node: exp.Func, dialect: Dialect = DIALECT) -> str | None:
    """The name under which `node` is denied, or None."""
    for name in function_names(node, dialect):
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in DENIED_FUNCTIONS):
            return name
    return None


def function_names(node: exp.Func, dialect: Dialect = DIALECT) -> set[str]:
    """Every name a function goes by: as written, sqlglot's, and as the database will see it."""
    names: set[str] = set()
    if isinstance(node, exp.Anonymous):
        names.add(node.name.lower())
    else:
        names.add(node.sql_name().lower())
    try:
        rendered = node.sql(dialect=dialect, comments=False)
    except SqlglotError:
        rendered = ""
    if match := _CALL.match(rendered):
        names.add(match.group(1).lower())
    return names


def _apply_row_filters(tree: exp.Expr, filters: Mapping[str, exp.Expr]) -> set[str]:
    """Point every reference to a restricted table at its fence, under the same alias (or the
    table's own name). Returns the restricted tables the query reads."""
    targets = [
        t for t in tree.find_all(exp.Table) if not is_cte_reference(t) and t.name.lower() in filters
    ]
    for table in targets:
        alias = table.args.get("alias")
        replacement = exp.Table(
            this=exp.to_identifier(fence_name(table.name)),
            alias=alias.copy()
            if isinstance(alias, exp.TableAlias)
            else exp.TableAlias(this=table.this.copy()),
        )
        table.replace(replacement)
    return {t.name.lower() for t in targets}


def _cap(tree: exp.Expr, limit: int) -> exp.Expr:
    """LIMIT min(existing, limit) on the outermost query, or wrap it when its LIMIT is not a
    plain number (an expression, a percentage, FETCH)."""
    if isinstance(tree, (*QUERY_NODES, exp.Subquery)):
        current = tree.args.get("limit")
        if current is None:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
            return tree
        if isinstance(current, exp.Limit) and {k for k, v in current.args.items() if v} == {
            "expression"
        }:
            value = current.expression
            if isinstance(value, exp.Literal) and not value.is_string and value.this.isdigit():
                current.set("expression", exp.Literal.number(min(int(value.this), limit)))
                return tree
    wrapped = exp.Subquery(this=tree, alias=exp.TableAlias(this=exp.to_identifier(RESULT_ALIAS)))
    return exp.select("*").from_(wrapped).limit(limit, copy=False)


def _describe(node: exp.Expr) -> str:
    if isinstance(node, exp.Command):
        return str(node.this).upper() or "This statement"
    return node.key.upper()


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text

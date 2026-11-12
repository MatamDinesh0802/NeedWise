"""The SQL guard (ARCHITECTURE section 16): every query the BI agent runs passes through here,
whether a person, the language model or the semantic-layer compiler wrote it.

check() accepts exactly one read-only query over the semantic model's tables and returns the
SQL to execute, rebuilt by sqlglot from the parsed tree (the text that was sent is never run):

1. Parse with sqlglot (DuckDB dialect). Exactly one statement, and it must be a query: SELECT,
   UNION / INTERSECT / EXCEPT, with CTEs. No DDL, DML, COPY, ATTACH, PRAGMA, SET, INSTALL, LOAD,
   CALL, EXPORT, transactions, SUMMARIZE, DESCRIBE, SHOW or SELECT ... INTO, at any depth.
2. Every table reference is a plain name, optionally `main.<name>`, of a table in the model, or
   a CTE visible at that point (resolved with DuckDB's scoping rules). No table functions
   (read_csv, glob, range, ...), file paths, other schemas or catalogs (information_schema,
   pg_catalog, duckdb_*), and no CTE may be named like a model table.
3. No function from a deny-list of file, network, configuration and catalog functions.
4. Rewrite: every reference to a table with a row filter for this user becomes
   `(SELECT * FROM <table> WHERE <filter>) AS <alias or table name>`, so joins, subqueries,
   window functions and recursive CTEs only ever see the rows the user may see.
5. A row cap: LIMIT min(existing, cap + 1), so the caller can tell when a result was truncated.

The engine (engine.py) is the second lock: even SQL that skipped this guard cannot touch files,
the network or settings.
"""

import fnmatch
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECT = "duckdb"
MAX_SQL_CHARS = 20_000
RESULT_ALIAS = "_wm_result"

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
)


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
class GuardedQuery:
    sql: str  # what to execute: row policies applied, row cap enforced
    query: str  # the checked query as written (normalized), before policies and the cap
    tables: tuple[str, ...]  # model tables the query reads
    filtered: tuple[str, ...]  # of those, the ones a row policy restricted


def check(
    sql: str,
    *,
    tables: Collection[str],
    row_filters: Mapping[str, exp.Expr] | None = None,
    max_rows: int = 1000,
) -> GuardedQuery:
    """Validate `sql` against the allow-listed `tables` (lower-case names) and rewrite it with
    this user's `row_filters` (table -> condition, user values already literals)."""
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    allowed = frozenset(t.lower() for t in tables)
    filters = {t.lower(): f for t, f in (row_filters or {}).items()}
    unknown = set(filters) - allowed
    if unknown:
        raise ValueError(f"row filters for tables outside the model: {sorted(unknown)}")

    tree = _parse_one(sql)
    used = validate(tree, allowed)
    query = tree.sql(dialect=DIALECT, comments=False)

    _apply_row_filters(tree, filters)
    capped = _cap(tree, max_rows + 1)
    final = capped.sql(dialect=DIALECT, comments=False)

    # Defence in depth: what will run must itself parse back to one valid query, unchanged.
    reparsed = _parse_one(final)
    validate(reparsed, allowed)
    if reparsed.sql(dialect=DIALECT, comments=False) != final:
        raise QueryRejectedError("The query could not be rewritten safely.")

    return GuardedQuery(
        sql=final,
        query=query,
        tables=tuple(sorted(used)),
        filtered=tuple(sorted(used & set(filters))),
    )


def _parse_one(sql: str) -> exp.Expr:
    if len(sql) > MAX_SQL_CHARS:
        raise QueryRejectedError(f"The query is too long (over {MAX_SQL_CHARS} characters).")
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=DIALECT) if s is not None]
    except SqlglotError as exc:
        raise QueryRejectedError(f"The query is not valid SQL: {_first_line(str(exc))}") from exc
    if not statements:
        raise QueryRejectedError("The query is empty.")
    if len(statements) > 1:
        raise QueryRejectedError("Send exactly one statement (a single SELECT query).")
    return statements[0]


def validate(tree: exp.Expr, tables: frozenset[str]) -> set[str]:
    """Raise QueryRejectedError unless `tree` is one read-only query over `tables`. Returns the
    model tables it reads (lower-case)."""
    top = tree
    while isinstance(top, exp.Subquery) and not top.alias:
        top = top.this
    if not isinstance(top, QUERY_NODES):
        raise QueryRejectedError("Only read-only queries are allowed (SELECT, with optional CTEs).")

    for node in tree.walk():
        if isinstance(node, exp.Placeholder | exp.Parameter):
            raise QueryRejectedError("Query parameters are not supported: write values inline.")
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
        if isinstance(node, exp.Func) and (name := denied_function(node)):
            raise QueryRejectedError(f"The function {name}() is not allowed.")
        if isinstance(node, exp.CTE):
            cte = node.alias_or_name.lower()
            if cte in tables:
                raise QueryRejectedError(
                    f"The CTE name {cte!r} is the name of a table; choose another name."
                )

    used: set[str] = set()
    for table in tree.find_all(exp.Table):
        if is_cte_reference(table):
            continue
        used.add(_check_table(table, tables))
    return used


def _check_table(table: exp.Table, tables: frozenset[str]) -> str:
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
    if table.catalog or (table.db and table.db.lower() != "main"):
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
    subqueries), and inside the WITH only to the CTEs after it, or to itself with RECURSIVE.
    DuckDB resolves anything else, including a forward reference, to a real table, and so does
    this function: such a name then has to be a model table."""
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
                if index is not None and with_.args.get("recursive"):
                    visible.append(names[index])
            else:
                visible = names
            if name in visible:
                return True
        path.append(node)
        node = node.parent
    return False


def denied_function(node: exp.Func) -> str | None:
    """The name under which `node` is denied, or None."""
    for name in function_names(node):
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in DENIED_FUNCTIONS):
            return name
    return None


def function_names(node: exp.Func) -> set[str]:
    """Every name a function goes by: as written, sqlglot's, and as DuckDB will see it."""
    names: set[str] = set()
    if isinstance(node, exp.Anonymous):
        names.add(node.name.lower())
    else:
        names.add(node.sql_name().lower())
    try:
        rendered = node.sql(dialect=DIALECT, comments=False)
    except SqlglotError:
        rendered = ""
    if match := _CALL.match(rendered):
        names.add(match.group(1).lower())
    return names


def _apply_row_filters(tree: exp.Expr, filters: Mapping[str, exp.Expr]) -> None:
    """Collect the table references first, then replace them, so the policy subqueries (and the
    tables inside policy conditions) are never rewritten again."""
    targets = [
        t for t in tree.find_all(exp.Table) if not is_cte_reference(t) and t.name.lower() in filters
    ]
    for table in targets:
        name = table.name.lower()
        alias = table.args.get("alias")
        policy = (
            exp.select("*")
            .from_(exp.Table(this=exp.to_identifier(name)))
            .where(filters[name].copy())
        )
        replacement = exp.Subquery(
            this=policy,
            alias=alias.copy()
            if isinstance(alias, exp.TableAlias)
            else exp.TableAlias(this=table.this.copy()),
        )
        table.replace(replacement)


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

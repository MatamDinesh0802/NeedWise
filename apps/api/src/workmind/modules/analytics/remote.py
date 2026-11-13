"""Running one checked query on a PostgreSQL or MySQL source (docs/analytics-sql-sources.md).

This runs inside a query worker process (engine.QueryPool, worker.py): the worker's memory
watchdog and the pool's kill-after-timeout apply as they do to DuckDB. The SQL has passed the
guard (guard.check with the source's dialect); this module is the second lock, the database
session:

- one connection per query, never shared between people or queries;
- the session is read-only: PostgreSQL gets default_transaction_read_only = on in the startup
  packet and runs the query in BEGIN READ ONLY; MySQL gets SET SESSION TRANSACTION READ ONLY and
  START TRANSACTION READ ONLY. Writes, DDL, nextval and the like fail there even if they got
  past the guard. The third lock is the database role an admin gives WorkMind, which should
  only be granted SELECT (the docs say how);
- a statement timeout on the server (PostgreSQL statement_timeout, MySQL max_execution_time),
  and the pool kills the worker (and so the connection) shortly after;
- one statement only: asyncpg uses the extended protocol (a prepared statement holds exactly
  one), and PyMySQL is not given CLIENT.MULTI_STATEMENTS;
- PostgreSQL: search_path = pg_catalog, so an unqualified name never finds a function or
  operator of the database's own schemas (the guard refuses qualified ones), and
  standard_conforming_strings = on, so a backslash in a literal is just a backslash (sqlglot
  writes literals that way). MySQL: a fixed sql_mode (backslash escapes on, as sqlglot writes
  them), no LOCAL INFILE, and derived_merge / derived_condition_pushdown off, so a row-policy
  fence (a CTE) is materialized before the query reads it and no condition of the query is
  evaluated on a row it leaves out;
- at most max_rows + 1 rows are fetched (the guard's LIMIT asks for no more).

Errors: an error about the query itself (syntax, unknown table, column or function, missing
privilege) is passed on without the query text; an error about a value (a failed conversion,
invalid input, division by zero, an overflow) is reported by kind only, since its text can quote
the value; a connection failure is SourceUnavailableError, whose `detail` (for admins) is not
shown to people asking questions.
"""

import asyncio
import contextlib
import importlib
import ssl
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from workmind.modules.analytics.engine import (
    EngineResult,
    QueryFailedError,
    QueryTimeoutError,
    ResultColumn,
    SourceUnavailableError,
    json_value,
)

SourceKind = Literal["postgres", "mysql"]
TlsMode = Literal["disable", "require", "verify-full"]
APPLICATION_NAME = "workmind-analytics"
# MySQL 8's default modes: backslash escapes stay on (sqlglot escapes backslashes in literals),
# no ANSI_QUOTES (sqlglot quotes identifiers with backticks).
MYSQL_SQL_MODE = (
    "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
)
_TIMEOUT_MESSAGE = (
    "The query took longer than {s:g} s and was stopped. Aggregate more, or narrow it with filters."
)
_VALUE_MESSAGE = (
    "The database could not compute a value: a conversion, an invalid input, a division by zero "
    "or an overflow (the value is not shown). Check the CASTs and the format of the literals."
)
_READ_ONLY_MESSAGE = "Only read-only queries run on this data source."


@dataclass(frozen=True)
class Target:
    """Where and how to connect. Holds the password: never logged, never stored in clear."""

    kind: SourceKind
    address: str  # what to connect to: a checked address, or the host name for verify-full
    host: str  # the host name the admin configured (TLS server name)
    port: int
    database: str
    user: str
    password: str
    tls_mode: TlsMode
    tls_ca: str | None = None
    connect_timeout_s: float = 5.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Target":
        return cls(**data)

    def __repr__(self) -> str:  # never the password
        return f"Target({self.kind} {self.user}@{self.host}:{self.port}/{self.database})"


def execute(target: Target, sql: str, *, max_rows: int, timeout_s: float) -> EngineResult:
    """Run one SELECT in a read-only session. Raises EngineError subclasses."""
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    if target.kind == "postgres":
        return asyncio.run(_postgres(target, sql, max_rows, timeout_s))
    if target.kind == "mysql":
        return _mysql(target, sql, max_rows, timeout_s)
    raise ValueError(f"unknown source kind {target.kind!r}")


def tls_context(target: Target) -> ssl.SSLContext | None:
    """None: no TLS. require: encrypted, the certificate is not checked. verify-full: the
    certificate must chain to a trusted CA (the system's, or `tls_ca`) and name the host."""
    if target.tls_mode == "disable":
        return None
    if target.tls_mode == "require":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    context = ssl.create_default_context(cadata=target.tls_ca) if target.tls_ca else None
    return context or ssl.create_default_context()


# --- PostgreSQL ------------------------------------------------------------------------------

_PG_TYPES = {
    "int2": "SMALLINT",
    "int4": "INTEGER",
    "int8": "BIGINT",
    "numeric": "DECIMAL",
    "float4": "FLOAT",
    "float8": "DOUBLE",
    "money": "DECIMAL",
    "bool": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "timestamptz": "TIMESTAMP WITH TIME ZONE",
    "time": "TIME",
    "interval": "INTERVAL",
    "text": "VARCHAR",
    "varchar": "VARCHAR",
    "bpchar": "VARCHAR",
    "name": "VARCHAR",
    "uuid": "UUID",
    "json": "JSON",
    "jsonb": "JSON",
}


async def _postgres(target: Target, sql: str, max_rows: int, timeout_s: float) -> EngineResult:
    pg: Any = importlib.import_module("asyncpg")  # no type information

    timeout_ms = str(max(1, round(timeout_s * 1000)))
    try:
        connection: Any = await pg.connect(
            host=target.address,
            port=target.port,
            user=target.user,
            password=target.password,
            database=target.database,
            ssl=tls_context(target) or False,
            timeout=target.connect_timeout_s,
            statement_cache_size=0,
            server_settings={
                "application_name": APPLICATION_NAME,
                "default_transaction_read_only": "on",
                "statement_timeout": timeout_ms,
                "lock_timeout": timeout_ms,
                "idle_in_transaction_session_timeout": timeout_ms,
                "search_path": "pg_catalog",
                "standard_conforming_strings": "on",
                "TimeZone": "UTC",
            },
        )
    except Exception as exc:  # any failure to connect: refused, TLS, auth, unknown database
        raise SourceUnavailableError(_pg_connect_detail(exc, target)) from None
    started = time.perf_counter()
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            statement: Any = await connection.prepare(sql, timeout=timeout_s)
            attributes: list[Any] = list(statement.get_attributes())
            records: list[Any] = list(await statement.fetch(timeout=timeout_s))
    except pg.QueryCanceledError as exc:
        raise QueryTimeoutError(_TIMEOUT_MESSAGE.format(s=timeout_s)) from exc
    except TimeoutError as exc:
        raise QueryTimeoutError(_TIMEOUT_MESSAGE.format(s=timeout_s)) from exc
    except pg.PostgresError as exc:
        raise QueryFailedError(_pg_message(exc)) from None
    except (OSError, pg.InterfaceError) as exc:
        raise SourceUnavailableError(f"The connection was lost: {type(exc).__name__}") from None
    finally:
        with contextlib.suppress(Exception):
            await connection.close(timeout=2)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    columns = [_pg_column(a) for a in attributes]
    rows = [[json_value(v) for v in record.values()] for record in records[:max_rows]]
    return EngineResult(
        columns=columns, rows=rows, truncated=len(records) > max_rows, elapsed_ms=elapsed_ms
    )


def _pg_column(attribute: Any) -> ResultColumn:
    type_name = str(attribute.type.name)
    return ResultColumn(name=str(attribute.name), type=_PG_TYPES.get(type_name, type_name.upper()))


def _pg_message(exc: Exception) -> str:
    """The error for the person: the reason for an error about the query; the kind only for an
    error about a value."""
    state = str(getattr(exc, "sqlstate", "") or "")
    message = str(getattr(exc, "message", "") or "").strip().splitlines()
    first = message[0][:400] if message else type(exc).__name__
    if state == "25006":
        return _READ_ONLY_MESSAGE
    if state.startswith("42"):  # syntax, undefined table / column / function, privileges
        return f"Database error: {first}"
    if state.startswith("22"):  # data exception: conversions, invalid input, division by zero
        return _VALUE_MESSAGE
    if state.startswith(("53", "54")):
        return "The query needs more resources than the database allows. Aggregate more."
    return f"The query failed (database error {state or type(exc).__name__}; details not shown)."


def _pg_connect_detail(exc: Exception, target: Target) -> str:
    state = str(getattr(exc, "sqlstate", "") or "")
    where = f"{target.host}:{target.port}"
    if state == "28P01":
        return f"{where} refused the user name or password."
    if state == "28000":
        return f"{where} does not allow this user to connect (check pg_hba.conf and TLS)."
    if state == "3D000":
        return f"{where} has no database {target.database!r}."
    if isinstance(exc, TimeoutError):
        return f"{where} did not answer within {target.connect_timeout_s:g} s."
    if isinstance(exc, ssl.SSLError):
        return f"TLS with {where} failed: {exc.reason or type(exc).__name__}."
    if isinstance(exc, OSError):
        return f"Could not connect to {where} ({exc.strerror or type(exc).__name__})."
    first = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else type(exc).__name__
    return f"Could not connect to {where}: {first}"


# --- MySQL -----------------------------------------------------------------------------------

# pymysql.constants.FIELD_TYPE -> a type name like DuckDB's (what the rest of analytics shows).
_MYSQL_TYPES = {
    0: "DECIMAL",
    1: "TINYINT",
    2: "SMALLINT",
    3: "INTEGER",
    4: "FLOAT",
    5: "DOUBLE",
    7: "TIMESTAMP",
    8: "BIGINT",
    9: "INTEGER",
    10: "DATE",
    11: "TIME",
    12: "TIMESTAMP",
    13: "SMALLINT",
    14: "DATE",
    16: "BIT",
    245: "JSON",
    246: "DECIMAL",
}
# Server error codes: about the query itself (its text is safe to pass on), a timeout,
# a read-only refusal; anything else about a value is reported by kind.
_MYSQL_QUERY_ERRORS = frozenset(
    {1054, 1052, 1064, 1066, 1060, 1111, 1140, 1142, 1143, 1146, 1149, 1305, 1582, 1583, 1584}
)
# Errors about a value read or computed (their text can quote it): truncated or incorrect values,
# out of range, division by zero, invalid JSON, a bad regular expression.
_MYSQL_VALUE_ERRORS = frozenset(
    {1264, 1265, 1292, 1365, 1366, 1367, 1406, 1411, 1690, 3140, 3141, 3143, 3146, 3156, 3685}
)
_MYSQL_TIMEOUT = frozenset({3024, 1317, 1969})
_MYSQL_READ_ONLY = frozenset({1792, 1290, 1836})
_MYSQL_CONNECTION = frozenset({1044, 1045, 1049, 1130, 1251, 2003, 2005, 2006, 2013, 2026})


def _mysql(target: Target, sql: str, max_rows: int, timeout_s: float) -> EngineResult:
    try:
        import pymysql
    except ImportError:  # the optional `mysql` extra (the image installs it)
        raise SourceUnavailableError(
            "MySQL support is not installed: install the API with the mysql extra."
        ) from None

    context = tls_context(target)
    try:
        connection = pymysql.connect(
            host=target.address,
            port=target.port,
            user=target.user,
            password=target.password,
            database=target.database or None,
            connect_timeout=max(1, round(target.connect_timeout_s)),
            read_timeout=max(1, round(timeout_s + 2)),
            write_timeout=max(1, round(timeout_s + 2)),
            ssl=context,
            ssl_disabled=context is None,
            local_infile=False,
            client_flag=0,  # no MULTI_STATEMENTS
            autocommit=False,
            charset="utf8mb4",
            sql_mode=MYSQL_SQL_MODE,
            program_name=APPLICATION_NAME,
        )
    except pymysql.MySQLError as exc:
        raise SourceUnavailableError(_mysql_connect_detail(exc, target)) from None
    except Exception as exc:
        raise SourceUnavailableError(
            f"Could not connect to {target.host}:{target.port} ({type(exc).__name__})."
        ) from None
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            for setting in (
                f"SET SESSION max_execution_time = {max(1, round(timeout_s * 1000))}",
                "SET SESSION optimizer_switch = 'derived_merge=off,derived_condition_pushdown=off'",
                "SET SESSION time_zone = '+00:00'",
                "SET SESSION TRANSACTION READ ONLY",
                "START TRANSACTION READ ONLY",
            ):
                cursor.execute(setting)
            cursor.execute(sql)  # no arguments: the text is sent as it is, never %-formatted
            description = cursor.description or ()
            fetched = cursor.fetchmany(max_rows + 1)
            cursor.execute("ROLLBACK")
    except pymysql.MySQLError as exc:
        code = _mysql_code(exc)
        if code in _MYSQL_TIMEOUT:
            raise QueryTimeoutError(_TIMEOUT_MESSAGE.format(s=timeout_s)) from None
        if code in _MYSQL_CONNECTION:
            raise SourceUnavailableError(_mysql_connect_detail(exc, target)) from None
        raise QueryFailedError(_mysql_message(exc)) from None
    finally:
        with contextlib.suppress(Exception):
            connection.close()
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    columns = [_mysql_column(d) for d in description]
    rows = [[json_value(v) for v in row] for row in fetched[:max_rows]]
    return EngineResult(
        columns=columns, rows=rows, truncated=len(fetched) > max_rows, elapsed_ms=elapsed_ms
    )


def _mysql_column(description: Sequence[Any]) -> ResultColumn:
    code = description[1]
    return ResultColumn(
        name=str(description[0]),
        type=_MYSQL_TYPES.get(code, "VARCHAR") if isinstance(code, int) else "VARCHAR",
    )


def _mysql_code(exc: Exception) -> int:
    args = getattr(exc, "args", ())
    return args[0] if args and isinstance(args[0], int) else 0


def _mysql_message(exc: Exception) -> str:
    code = _mysql_code(exc)
    args = getattr(exc, "args", ())
    text = str(args[1]).strip().splitlines()[0][:400] if len(args) > 1 else type(exc).__name__
    if code in _MYSQL_READ_ONLY:
        return _READ_ONLY_MESSAGE
    if code in _MYSQL_QUERY_ERRORS:
        return f"Database error: {text}"
    if code in _MYSQL_VALUE_ERRORS:
        return _VALUE_MESSAGE
    return f"The query failed (database error {code or type(exc).__name__}; details not shown)."


def _mysql_connect_detail(exc: Exception, target: Target) -> str:
    code = _mysql_code(exc)
    where = f"{target.host}:{target.port}"
    if code in (1045, 1044):
        return f"{where} refused the user name or password."
    if code == 1049:
        return f"{where} has no database {target.database!r}."
    if code in (2003, 2005):
        return f"Could not connect to {where}."
    if code in (2006, 2013):
        return f"{where} closed the connection."
    if code == 2026:
        return f"TLS with {where} failed."
    return f"Could not connect to {where} (MySQL error {code or type(exc).__name__})."

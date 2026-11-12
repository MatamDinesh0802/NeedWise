"""Sandboxed DuckDB execution (ARCHITECTURE section 16), the second lock after the SQL guard.

Each query opens its own connection to the tenant's dataset file, READ-ONLY, with:
- enable_external_access = false: no files (read_csv, COPY, ATTACH, '/etc/passwd' as a table),
  no network (http, s3), no extension downloads;
- autoinstall / autoload of known extensions off, and Python replacement scans off (a table
  name can never resolve to a Python variable);
- lock_configuration = true: SET / PRAGMA cannot undo any of this;
- threads = 2 and memory_limit = 512MB, so one query cannot starve the API process.

It runs in a worker thread; a timer interrupts it after `timeout_s`. At most `max_rows + 1` rows
are fetched, so the caller can tell a truncated result from a complete one. Values come back
JSON-safe (Decimal -> float, dates -> ISO 8601, NaN -> null, ...).
"""

import asyncio
import contextlib
import math
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

DEFAULT_TIMEOUT_S = 10.0
CONFIG: dict[str, str | bool | int] = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "python_enable_replacements": False,
    "threads": 2,
    "memory_limit": "512MB",
    "lock_configuration": True,  # last: nothing can be changed after this
}


class EngineError(Exception):
    """A query that could not run; `message` is safe to show to the person who sent it."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class QueryTimeoutError(EngineError):
    pass


class QueryFailedError(EngineError):
    pass


class DatasetMissingError(EngineError):
    pass


@dataclass(frozen=True)
class ResultColumn:
    name: str
    type: str  # DuckDB type, e.g. BIGINT, DECIMAL(18,2), DATE, VARCHAR


@dataclass(frozen=True)
class EngineResult:
    columns: list[ResultColumn]
    rows: list[list[JsonValue]]
    truncated: bool  # more than max_rows rows: only the first max_rows are returned
    elapsed_ms: int


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    """A read-only, sandboxed connection to a dataset file."""
    if not path.is_file():
        raise DatasetMissingError("The dataset is not available. Ask an admin to load the data.")
    return duckdb.connect(str(path), read_only=True, config=dict(CONFIG))


async def execute(
    path: Path, sql: str, *, max_rows: int, timeout_s: float = DEFAULT_TIMEOUT_S
) -> EngineResult:
    """Run one SELECT on the dataset at `path` in a worker thread."""
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    running: dict[str, duckdb.DuckDBPyConnection] = {}
    try:
        return await asyncio.to_thread(_execute, path, sql, max_rows, timeout_s, running)
    except asyncio.CancelledError:
        # The caller gave up (a client disconnected): stop the query instead of finishing it.
        if connection := running.get("connection"):
            _interrupt(connection)
        raise


def execute_sync(
    path: Path, sql: str, *, max_rows: int, timeout_s: float = DEFAULT_TIMEOUT_S
) -> EngineResult:
    return _execute(path, sql, max_rows, timeout_s, {})


def _execute(
    path: Path,
    sql: str,
    max_rows: int,
    timeout_s: float,
    running: dict[str, duckdb.DuckDBPyConnection],
) -> EngineResult:
    connection = connect(path)
    running["connection"] = connection
    timer = threading.Timer(timeout_s, _interrupt, args=(connection,))
    timer.daemon = True
    started = time.perf_counter()
    try:
        _single_select(connection, sql)
        timer.start()
        cursor = connection.execute(sql)
        description = cursor.description or []
        fetched = cursor.fetchmany(max_rows + 1)
    except duckdb.InterruptException as exc:
        raise QueryTimeoutError(
            f"The query took longer than {timeout_s:g} s and was stopped. "
            "Aggregate more, or narrow it with filters."
        ) from exc
    except duckdb.OutOfMemoryException as exc:
        raise QueryFailedError("The query needs too much memory. Aggregate more.") from exc
    except duckdb.Error as exc:
        raise QueryFailedError(_message(exc)) from exc
    finally:
        timer.cancel()
        with contextlib.suppress(duckdb.Error):
            connection.close()
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    columns = [ResultColumn(name=str(d[0]), type=str(d[1])) for d in description]
    rows = [[json_value(v) for v in row] for row in fetched[:max_rows]]
    return EngineResult(
        columns=columns, rows=rows, truncated=len(fetched) > max_rows, elapsed_ms=elapsed_ms
    )


def _single_select(connection: duckdb.DuckDBPyConnection, sql: str) -> None:
    """DuckDB would run every statement in the string: allow exactly one, a SELECT."""
    statements = connection.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise QueryFailedError("Only a single SELECT statement can run.")


def _interrupt(connection: duckdb.DuckDBPyConnection) -> None:
    with contextlib.suppress(duckdb.Error, RuntimeError):
        connection.interrupt()


def _message(exc: duckdb.Error) -> str:
    text = str(exc).strip()
    # DuckDB appends the query with a caret ("LINE 1: ..." and "^"): keep only the reason.
    lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("LINE "):
            break
        if line.strip():
            lines.append(line.strip())
    return " ".join(lines[:3])[:500] if lines else type(exc).__name__


def json_value(value: Any) -> JsonValue:
    """A DuckDB value as JSON: numbers stay numbers, everything else becomes a string."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, datetime | date | clock):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray):
        return bytes(value).hex()
    if isinstance(value, Mapping):
        items: Mapping[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
        return {str(k): json_value(v) for k, v in items.items()}
    if isinstance(value, Sequence | set | frozenset):
        values: Sequence[Any] = list(value)  # pyright: ignore[reportUnknownArgumentType]
        return [json_value(v) for v in values]
    return str(value)

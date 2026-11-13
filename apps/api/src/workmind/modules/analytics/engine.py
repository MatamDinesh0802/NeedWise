"""Sandboxed DuckDB execution (ARCHITECTURE section 16), the second lock after the SQL guard.

Each query opens its own connection to the tenant's dataset file, READ-ONLY, with:
- enable_external_access = false: no files (read_csv, COPY, ATTACH, '/etc/passwd' as a table),
  no network (http, s3), no extension downloads;
- autoinstall / autoload of known extensions off, and Python replacement scans off (a table
  name can never resolve to a Python variable);
- lock_configuration = true: SET / PRAGMA cannot undo any of this;
- threads = 2 and memory_limit = 512MB for DuckDB's own buffers.

Row-policy fences (guard.Fence) are created as temporary tables first, each its own statement,
then the query runs. A timer interrupts DuckDB after `timeout_s`. At most `max_rows + 1` rows
are fetched, so the caller can tell a truncated result from a complete one. Values come back
JSON-safe (Decimal -> float, dates -> ISO 8601, NaN -> null, ...). DuckDB's error text is passed
on only for errors about the query itself (parser, binder, catalog); errors that can quote a
value (a failed conversion, an invalid input, an overflow) are reported by kind only.

DuckDB's memory_limit does not cover everything (values built by scalar functions, such as a
list from range(), are allocated outside it), and its interrupt is only honoured between
operators, not inside one long function call. So the service runs queries in a QueryPool:
separate worker processes (worker.py), each killed when its resident memory passes a limit or
when a query runs past its timeout plus a grace period, with a bounded number of queries at
once and per person. execute_sync and execute (in a thread of this process) are for tests and
tools that trust their SQL.

The same workers run queries on PostgreSQL and MySQL sources (QueryPool.execute_remote,
remote.py), with the same memory and time limits, and a limit on the connections a tenant opens
at once.
"""

import asyncio
import contextlib
import json
import math
import os
import select
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

import duckdb
from pydantic import JsonValue

__all__ = ["JsonValue"]  # re-exported: results are lists of rows of JsonValue

DEFAULT_TIMEOUT_S = 10.0
MEMORY_LIMIT_MB = 512  # DuckDB's buffer manager, per query
CONFIG: dict[str, str | bool | int] = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "python_enable_replacements": False,
    "threads": 2,
    "memory_limit": f"{MEMORY_LIMIT_MB}MB",
    "lock_configuration": True,  # last: nothing can be changed after this
}
# DuckDB errors about the query itself: their text names columns, tables and functions, and
# may quote the query's own literals, never a value read from the data.
_QUERY_ERRORS: tuple[type[duckdb.Error], ...] = (
    duckdb.ParserException,
    duckdb.BinderException,
    duckdb.CatalogException,
)
_VALUE_ERRORS: tuple[tuple[type[duckdb.Error], str], ...] = (
    (
        duckdb.ConversionException,
        "Conversion Error: a value could not be converted to the type asked for. Check the "
        "CASTs and the format of the literals (the value is not shown).",
    ),
    (
        duckdb.OutOfRangeException,
        "Out of Range Error: a value is out of range for its type, for example an overflow "
        "(the value is not shown).",
    ),
    (
        duckdb.InvalidInputException,
        "Invalid Input Error: a function was given an input it cannot use (the value is not "
        "shown).",
    ),
)


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


class QueryBusyError(EngineError):
    """Too many queries at once, for this person or for the server: try again shortly."""


class SourceUnavailableError(EngineError):
    """A database source could not be reached or refused the connection. `message` says why
    and names the host: for admins (the source's status), not for people asking questions."""


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


Fences = Sequence[tuple[str, str]]  # (temporary table, SELECT): guard.Fence as (name, sql)


async def execute(
    path: Path,
    sql: str,
    *,
    max_rows: int,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    fences: Fences = (),
) -> EngineResult:
    """Run one SELECT on the dataset at `path` in a thread of this process (no memory limit
    beyond DuckDB's own, and only DuckDB's interrupt: see QueryPool for untrusted SQL)."""
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    running: dict[str, duckdb.DuckDBPyConnection] = {}
    try:
        return await asyncio.to_thread(_execute, path, sql, max_rows, timeout_s, running, fences)
    except asyncio.CancelledError:
        # The caller gave up (a client disconnected): stop the query instead of finishing it.
        if connection := running.get("connection"):
            _interrupt(connection)
        raise


def execute_sync(
    path: Path,
    sql: str,
    *,
    max_rows: int,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    fences: Fences = (),
) -> EngineResult:
    return _execute(path, sql, max_rows, timeout_s, {}, fences)


def _execute(
    path: Path,
    sql: str,
    max_rows: int,
    timeout_s: float,
    running: dict[str, duckdb.DuckDBPyConnection],
    fences: Fences = (),
) -> EngineResult:
    connection = connect(path)
    running["connection"] = connection
    timer = threading.Timer(timeout_s, _interrupt, args=(connection,))
    timer.daemon = True
    started = time.perf_counter()
    try:
        _single_select(connection, sql)
        for _, fence in fences:
            _single_select(connection, fence)
        timer.start()
        for name, fence in fences:
            # Its own statement: DuckDB plans the policy alone, and the query below only ever
            # reads the rows it kept. Temporary tables live in this connection's memory.
            connection.execute(f"CREATE TEMP TABLE {_identifier(name)} AS {fence}")
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


def _identifier(name: str) -> str:
    if not name.replace("_", "").isalnum() or not name.isascii():
        raise ValueError(f"not a plain identifier: {name!r}")
    return f'"{name}"'


def _single_select(connection: duckdb.DuckDBPyConnection, sql: str) -> None:
    """DuckDB would run every statement in the string: allow exactly one, a SELECT."""
    statements = connection.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise QueryFailedError("Only a single SELECT statement can run.")


def _interrupt(connection: duckdb.DuckDBPyConnection) -> None:
    with contextlib.suppress(duckdb.Error, RuntimeError):
        connection.interrupt()


def _message(exc: duckdb.Error) -> str:
    """What to tell the person: the reason for an error about the query; only the kind of an
    error about a value, since its text quotes the value (which may come from any row the
    query reads)."""
    if not isinstance(exc, _QUERY_ERRORS):
        for kind, message in _VALUE_ERRORS:
            if isinstance(exc, kind):
                return message
        name = type(exc).__name__.removesuffix("Exception")
        return f"The query failed ({name} error; the details are not shown)."
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


# --- Isolated execution ----------------------------------------------------------------------

EXIT_OUT_OF_MEMORY = 86  # a worker's exit status when its memory watchdog stopped it
WORKER_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "HOME", "TMPDIR", "SYSTEMROOT")


class OwnerSlots(Protocol):
    """Counts each person's running queries for QueryPool. `LocalSlots` counts in this process;
    `analytics.slots.ValkeySlots` counts across every API process and pod (the API runs
    several), so "one query per person at a time" holds for the deployment."""

    async def take(self, owner: str, limit: int, hold_s: float) -> str | None:
        """A lease on one of `owner`'s `limit` slots, held at most `hold_s` (in case the
        process dies before giving it back); None when every slot is taken."""
        ...

    async def give_back(self, owner: str, lease: str) -> None: ...


class LocalSlots:
    """Slots counted in this process only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, int] = {}

    async def take(self, owner: str, limit: int, hold_s: float) -> str | None:
        with self._lock:
            if self._running.get(owner, 0) >= limit:
                return None
            self._running[owner] = self._running.get(owner, 0) + 1
            return "local"

    async def give_back(self, owner: str, lease: str) -> None:
        with self._lock:
            count = self._running.get(owner, 0) - 1
            if count > 0:
                self._running[owner] = count
            else:
                self._running.pop(owner, None)


class QueryPool:
    """Runs queries in worker processes (python -m workmind.modules.analytics.worker), so one
    query cannot take the API process's memory or keep running past its timeout:

    - each worker stops itself (exit EXIT_OUT_OF_MEMORY) when its resident memory passes
      `memory_mb`, and the query fails with "needs too much memory";
    - DuckDB is interrupted after the timeout; a query that does not stop (a long call inside
      one function) is killed `grace_s` later, with its worker;
    - at most `workers` queries run at once (each in a thread of a pool of its own, never the
      default executor), and at most `per_owner` per person, counted by `slots` (in this
      process unless the caller shares them, e.g. in Valkey): more wait their turn, up to the
      timeout, then fail with QueryBusyError;
    - a result larger than `max_result_bytes` (as JSON) is refused.

    Workers start on first use and are reused; a worker that was killed, or whose peak memory
    passed half the limit, is replaced. Workers get a minimal environment (no secrets)."""

    def __init__(
        self,
        *,
        workers: int = 3,
        memory_mb: int = 1024,
        per_owner: int = 1,
        max_result_bytes: int = 8 * 1024 * 1024,
        grace_s: float = 2.0,
        startup_s: float = 15.0,
        slots: OwnerSlots | None = None,
    ) -> None:
        if workers < 1 or per_owner < 1 or memory_mb < 64:
            raise ValueError("workers and per_owner must be positive, memory_mb at least 64")
        self.workers = workers
        self.memory_mb = memory_mb
        self.per_owner = per_owner
        self.max_result_bytes = max_result_bytes
        self.grace_s = grace_s
        self.startup_s = startup_s
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analytics")
        self._lock = threading.Lock()
        self._idle: list[_Worker] = []
        self._slots: OwnerSlots = slots or LocalSlots()
        self._closed = False

    async def execute(
        self,
        path: Path,
        sql: str,
        *,
        max_rows: int,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        fences: Fences = (),
        owner: str | None = None,
    ) -> EngineResult:
        """Run one SELECT (after its fences) in a worker. `owner` (a person) bounds how many
        of their queries run at once."""
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        if self._closed:
            raise RuntimeError("the query pool is closed")
        request = {
            "path": str(path),
            "sql": sql,
            "fences": [[name, fence] for name, fence in fences],
            "max_rows": max_rows,
            "timeout_s": timeout_s,
            "max_result_bytes": self.max_result_bytes,
        }
        return await self._run(request, timeout_s, timeout_s, owner)

    async def execute_remote(
        self,
        target: dict[str, Any],
        sql: str,
        *,
        max_rows: int,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        connect_timeout_s: float = 5.0,
        owner: str | None = None,
        connections: tuple[str, int] | None = None,
    ) -> EngineResult:
        """Run one checked SELECT on a database source (remote.Target as JSON) in a worker.
        `connections`: (key, limit) bounding the connections open at once under that key (a
        tenant's), taken before the person's own slot."""
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        if self._closed:
            raise RuntimeError("the query pool is closed")
        request = {
            "source": target,
            "sql": sql,
            "max_rows": max_rows,
            "timeout_s": timeout_s,
            "max_result_bytes": self.max_result_bytes,
        }
        if connections is None:
            return await self._run(request, timeout_s + connect_timeout_s, timeout_s, owner)
        key, limit = connections
        lease = await self._take(key, limit, timeout_s, "connections")
        try:
            return await self._run(request, timeout_s + connect_timeout_s, timeout_s, owner)
        finally:
            await self._slots.give_back(key, lease)

    async def _run(
        self, request: dict[str, Any], wait_s: float, timeout_s: float, owner: str | None
    ) -> EngineResult:
        lease = await self._enter(owner, timeout_s)
        try:
            holder: dict[str, _Worker] = {}
            call = self._executor.submit(self._call, request, wait_s, time.monotonic(), holder)
            try:
                reply = await asyncio.wrap_future(call)
            except asyncio.CancelledError:
                # The caller gave up (a client disconnected): stop the query with its worker.
                call.cancel()
                if worker := holder.get("worker"):
                    worker.kill()
                raise
        finally:
            if owner is not None and lease is not None:
                await self._slots.give_back(owner, lease)
        return _from_reply(reply, timeout_s)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            idle, self._idle = self._idle, []
        for worker in idle:
            worker.kill()

    # --- Internals ---

    async def _enter(self, owner: str | None, wait_s: float) -> str | None:
        if owner is None:
            return None
        return await self._take(owner, self.per_owner, wait_s, "person")

    async def _take(
        self, key: str, limit: int, wait_s: float, kind: Literal["person", "connections"]
    ) -> str:
        deadline = time.monotonic() + wait_s
        # The longest a query can hold its slot: waiting for a pool thread, starting a worker,
        # connecting, running, being killed. A lease lives that long, in case this process
        # dies first.
        hold_s = 3 * wait_s + self.startup_s + self.grace_s + 5
        while True:
            if (lease := await self._slots.take(key, limit, hold_s)) is not None:
                return lease
            if time.monotonic() >= deadline:
                raise QueryBusyError(
                    "Your earlier queries are still running. Try again when they have finished."
                    if kind == "person"
                    else "Too many queries are running on this data source. Try again shortly."
                )
            await asyncio.sleep(0.05)

    def _call(
        self, request: dict[str, Any], timeout_s: float, submitted: float, holder: dict[str, Any]
    ) -> dict[str, Any]:
        """In a pool thread: hand the request to a worker and wait for its answer."""
        if time.monotonic() - submitted > timeout_s:
            raise QueryBusyError("The analytics engine is busy. Try again in a moment.")
        line = (json.dumps(request) + "\n").encode()
        for attempt in (1, 2):
            worker = self._checkout()
            holder["worker"] = worker
            try:
                reply = worker.call(line, timeout_s, self.grace_s, self.startup_s)
            except _WorkerGoneError:
                # It died between queries (or was killed while idle): once more, on a new one.
                worker.kill()
                if attempt == 2 or self._closed:
                    raise QueryFailedError("The query engine stopped unexpectedly.") from None
                continue
            except BaseException:
                worker.kill()
                self._spare()
                raise
            if reply.get("recycle") or self._closed:
                worker.kill()
                self._spare()
            else:
                with self._lock:
                    self._idle.append(worker)
            return reply
        raise AssertionError("unreachable")

    def _spare(self) -> None:
        """Start a replacement for a worker that was stopped, so the next query does not wait
        for a new interpreter to import DuckDB (about 0.3 s)."""
        with self._lock:
            if self._closed or len(self._idle) >= self.workers - 1:
                return
            self._idle.append(_Worker.spawn(self.memory_mb))

    def _checkout(self) -> "_Worker":
        with self._lock:
            while self._idle:
                worker = self._idle.pop()
                if worker.alive():
                    return worker
                worker.kill()
        return _Worker.spawn(self.memory_mb)


_shared: QueryPool | None = None
_shared_lock = threading.Lock()


def shared_pool() -> QueryPool:
    """A pool with the default limits, shared by every caller in this process that does not
    bring its own (seeding, tests, the evaluation)."""
    global _shared
    with _shared_lock:
        if _shared is None or _shared.closed:
            _shared = QueryPool()
        return _shared


class _WorkerGoneError(Exception):
    """The worker was not there to take the request (it had exited)."""


class _Worker:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self.fresh = True  # still importing: its first answer may take longer

    @classmethod
    def spawn(cls, memory_mb: int) -> "_Worker":
        env = {key: os.environ[key] for key in WORKER_ENV if key in os.environ}
        process = subprocess.Popen(  # noqa: S603 (our own module, with the running interpreter)
            [sys.executable, "-I", "-m", "workmind.modules.analytics.worker", str(memory_mb)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
            close_fds=True,
        )
        return cls(process)

    def alive(self) -> bool:
        return self.process.poll() is None

    def kill(self) -> None:
        with contextlib.suppress(OSError):
            self.process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            with contextlib.suppress(OSError):
                if stream is not None:
                    stream.close()

    def call(
        self, line: bytes, timeout_s: float, grace_s: float, startup_s: float
    ) -> dict[str, Any]:
        """Send one request and wait for its answer: the timeout plus `grace_s` (plus
        `startup_s` while the worker is new), then the worker is killed."""
        stdin, stdout = self.process.stdin, self.process.stdout
        if stdin is None or stdout is None:
            raise _WorkerGoneError
        try:
            stdin.write(line)
            stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            raise _WorkerGoneError from exc
        deadline = time.monotonic() + timeout_s + grace_s + (startup_s if self.fresh else 0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.kill()
                raise QueryTimeoutError(_timeout_message(timeout_s))
            ready, _, _ = select.select([stdout], [], [], remaining)
            if ready:
                break
        answer = stdout.readline()
        self.fresh = False
        if not answer.endswith(b"\n"):  # it exited before answering
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=5)
            if self.process.returncode == EXIT_OUT_OF_MEMORY:
                raise QueryFailedError("The query needs too much memory. Aggregate more.")
            raise QueryFailedError("The query stopped unexpectedly.")
        reply: dict[str, Any] = json.loads(answer)
        return reply


def _timeout_message(timeout_s: float) -> str:
    return (
        f"The query took longer than {timeout_s:g} s and was stopped. "
        "Aggregate more, or narrow it with filters."
    )


def _from_reply(reply: Mapping[str, Any], timeout_s: float) -> EngineResult:
    if reply.get("ok"):
        return EngineResult(
            columns=[ResultColumn(name=str(n), type=str(t)) for n, t in reply["columns"]],
            rows=reply["rows"],
            truncated=bool(reply["truncated"]),
            elapsed_ms=int(reply["elapsed_ms"]),
        )
    kind, message = reply.get("error"), str(reply.get("message") or "The query failed.")
    if kind == "timeout":
        raise QueryTimeoutError(_timeout_message(timeout_s))
    if kind == "missing":
        raise DatasetMissingError(message)
    if kind == "unavailable":
        raise SourceUnavailableError(message)
    raise QueryFailedError(message)


def serve(memory_mb: int) -> None:
    """A worker's loop (worker.py): one JSON request per line on stdin, one JSON answer per line
    on the original stdout. Stops when stdin closes, or (EXIT_OUT_OF_MEMORY) when its resident
    memory passes `memory_mb`."""
    limit = memory_mb * 1024 * 1024
    answers = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # anything else printed goes to stderr, never into the answers
    watchdog = threading.Thread(target=_watch_memory, args=(limit,), daemon=True)
    watchdog.start()
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        request = json.loads(line)
        answers.write(_answer(request, limit) + b"\n")
        answers.flush()


def _answer(request: Mapping[str, Any], limit: int) -> bytes:
    timeout_s = float(request["timeout_s"])
    try:
        if "source" in request:  # a database source (remote.py)
            from workmind.modules.analytics import remote

            result = remote.execute(
                remote.Target.from_json(dict(request["source"])),
                str(request["sql"]),
                max_rows=int(request["max_rows"]),
                timeout_s=timeout_s,
            )
        else:
            result = _execute(
                Path(request["path"]),
                str(request["sql"]),
                int(request["max_rows"]),
                timeout_s,
                {},
                [(str(name), str(sql)) for name, sql in request["fences"]],
            )
        reply: dict[str, Any] = {
            "ok": True,
            "columns": [[c.name, c.type] for c in result.columns],
            "rows": result.rows,
            "truncated": result.truncated,
            "elapsed_ms": result.elapsed_ms,
        }
    except QueryTimeoutError as exc:
        reply = {"ok": False, "error": "timeout", "message": exc.message}
    except DatasetMissingError as exc:
        reply = {"ok": False, "error": "missing", "message": exc.message}
    except SourceUnavailableError as exc:
        reply = {"ok": False, "error": "unavailable", "message": exc.message}
    except EngineError as exc:
        reply = {"ok": False, "error": "failed", "message": exc.message}
    except Exception:  # never stop the worker over one query
        reply = {"ok": False, "error": "failed", "message": "The query failed."}
    reply["recycle"] = _peak_rss() > limit // 2  # give its memory back to the system
    encoded = json.dumps(reply, allow_nan=False).encode()
    if len(encoded) > int(request["max_result_bytes"]):
        megabytes = int(request["max_result_bytes"]) / 1024 / 1024
        reply = {
            "ok": False,
            "error": "failed",
            "message": f"The result is larger than {megabytes:g} MB. Aggregate more, or select "
            "fewer rows or columns.",
            "recycle": reply["recycle"],
        }
        encoded = json.dumps(reply).encode()
    return encoded


def _peak_rss() -> int:
    import resource  # Unix only, like the worker processes

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # bytes on macOS, KiB on Linux


def _watch_memory(limit: int) -> None:
    while True:
        if _peak_rss() > limit:
            os._exit(EXIT_OUT_OF_MEMORY)  # pyright: ignore[reportPrivateUsage]
        time.sleep(0.02)

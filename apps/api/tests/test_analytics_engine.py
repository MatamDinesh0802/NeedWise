"""The DuckDB sandbox, called directly (no SQL guard): even SQL that skipped the guard cannot
read files, reach the network, change settings or write; long queries stop; results are capped
and JSON-safe."""

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from workmind.modules.analytics import engine


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "dataset.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE t AS SELECT i, 'row ' || i AS label FROM range(50) AS r(i)")
    connection.close()
    return path


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT * FROM read_text('/etc/hosts')",
        "SELECT * FROM read_blob('/etc/hosts')",
        "SELECT * FROM '/etc/passwd'",
        "SELECT * FROM glob('/etc/*')",
        "SELECT * FROM read_parquet('/tmp/x.parquet')",
        "SELECT * FROM 'https://example.com/data.csv'",
        "SELECT * FROM read_csv('https://example.com/data.csv')",
        "SELECT * FROM sniff_csv('/etc/passwd')",
    ],
)
def test_files_and_network_are_out_of_reach(db: Path, sql: str) -> None:
    with pytest.raises(engine.QueryFailedError):
        engine.execute_sync(db, sql, max_rows=10)


@pytest.mark.parametrize(
    "sql",
    [
        "COPY t TO '{out}'",
        "CREATE TABLE stolen AS SELECT * FROM t",
        "INSERT INTO t VALUES (1, 'x')",
        "SET enable_external_access = true",
        "ATTACH '{out}' AS other",
        "INSTALL httpfs",
        "LOAD httpfs",
        "PRAGMA database_list",
        "SELECT 1; COPY t TO '{out}'",
        "SELECT 1; SELECT 2",
    ],
)
def test_only_a_single_select_runs(db: Path, tmp_path: Path, sql: str) -> None:
    out = tmp_path / "out.csv"

    with pytest.raises(engine.QueryFailedError):
        engine.execute_sync(db, sql.format(out=out), max_rows=10)

    assert not out.exists()


def test_the_connection_is_read_only_and_its_settings_locked(db: Path, tmp_path: Path) -> None:
    connection = engine.connect(db)
    try:
        for statement in (
            "SET enable_external_access = true",
            "SET lock_configuration = false",
            "SET memory_limit = '64GB'",
            "SET autoinstall_known_extensions = true",
            f"COPY t TO '{tmp_path / 'out.csv'}'",
            "CREATE TABLE x (a INTEGER)",
            "LOAD httpfs",
        ):
            with pytest.raises(duckdb.Error):
                connection.execute(statement)
        settings = dict(
            connection.execute(
                "SELECT name, value FROM duckdb_settings() WHERE name IN "
                "('enable_external_access', 'lock_configuration', 'threads', 'access_mode')"
            ).fetchall()
        )
    finally:
        connection.close()

    assert settings == {
        "access_mode": "read_only",
        "enable_external_access": "false",
        "lock_configuration": "true",
        "threads": "2",
    }
    assert not (tmp_path / "out.csv").exists()


def test_a_long_query_is_interrupted(db: Path) -> None:
    started = time.perf_counter()

    with pytest.raises(engine.QueryTimeoutError, match=r"longer than 0\.3 s"):
        engine.execute_sync(
            db,
            "SELECT count(*) FROM range(1000000000) a, range(1000000) b",
            max_rows=10,
            timeout_s=0.3,
        )

    assert time.perf_counter() - started < 5


def test_a_runaway_recursive_cte_is_stopped(db: Path) -> None:
    started = time.perf_counter()

    # Interrupted, or stopped by the memory limit, whichever comes first: never left running.
    with pytest.raises((engine.QueryTimeoutError, engine.QueryFailedError)):
        engine.execute_sync(
            db,
            "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) "
            "SELECT count(*) FROM r",
            max_rows=10,
            timeout_s=0.5,
        )

    assert time.perf_counter() - started < 10


async def test_a_cancelled_query_is_interrupted(db: Path) -> None:
    task = asyncio.create_task(
        engine.execute(db, "SELECT count(*) FROM range(1000000000) a, range(1000000) b", max_rows=1)
    )
    await asyncio.sleep(0.2)
    started = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The worker thread was interrupted rather than left running for the full timeout.
    assert time.perf_counter() - started < 5


def test_rows_are_capped_and_truncation_reported(db: Path) -> None:
    capped = engine.execute_sync(db, "SELECT i FROM t ORDER BY i", max_rows=10)
    complete = engine.execute_sync(db, "SELECT i FROM t ORDER BY i", max_rows=50)

    assert (len(capped.rows), capped.truncated) == (10, True)
    assert capped.rows[-1] == [9]
    assert (len(complete.rows), complete.truncated) == (50, False)


async def test_async_execution_returns_columns_and_types(db: Path) -> None:
    result = await engine.execute(db, "SELECT i, label FROM t WHERE i < 2 ORDER BY i", max_rows=5)

    assert [(c.name, c.type) for c in result.columns] == [("i", "BIGINT"), ("label", "VARCHAR")]
    assert result.rows == [[0, "row 0"], [1, "row 1"]]
    assert result.elapsed_ms >= 0


def test_values_are_json_safe(db: Path) -> None:
    result = engine.execute_sync(
        db,
        "SELECT 2.50::DECIMAL(10,2) AS d, DATE '2026-09-30' AS day, "
        "TIMESTAMP '2026-09-30 12:30:00' AS ts, TIME '08:15:00' AS t, INTERVAL 3 DAY AS iv, "
        "'00000000-0000-0000-0000-000000000001'::UUID AS u, 'ab'::BLOB AS b, [1, 2] AS l, "
        "{'a': 1.5} AS s, MAP {'k': DATE '2026-01-01'} AS m, 'nan'::DOUBLE AS nan, "
        "'inf'::DOUBLE AS inf, 170141183460469231731687303715884105727::HUGEINT AS h, "
        "true AS yes, NULL AS nothing",
        max_rows=1,
    )

    row = dict(zip([c.name for c in result.columns], result.rows[0], strict=True))
    assert row == {
        "d": 2.5,
        "day": "2026-09-30",
        "ts": "2026-09-30T12:30:00",
        "t": "08:15:00",
        "iv": "3 days, 0:00:00",
        "u": "00000000-0000-0000-0000-000000000001",
        "b": "6162",
        "l": [1, 2],
        "s": {"a": 1.5},
        "m": {"k": "2026-01-01"},
        "nan": None,
        "inf": None,
        "h": 170141183460469231731687303715884105727,
        "yes": True,
        "nothing": None,
    }
    json.dumps(result.rows, allow_nan=False)  # strict JSON


def test_a_missing_dataset_is_reported(tmp_path: Path) -> None:
    with pytest.raises(engine.DatasetMissingError):
        engine.execute_sync(tmp_path / "gone.duckdb", "SELECT 1", max_rows=1)


def test_errors_are_short_and_name_the_problem(db: Path) -> None:
    with pytest.raises(engine.QueryFailedError) as failed:
        engine.execute_sync(db, "SELECT nope FROM t", max_rows=1)

    assert "nope" in failed.value.message
    assert "LINE 1" not in failed.value.message


# --- Fences and error messages ------------------------------------------------------------


def test_fences_are_temporary_tables_the_query_reads(db: Path) -> None:
    result = engine.execute_sync(
        db,
        "SELECT count(*), max(i) FROM _wm_rows_t AS t",
        max_rows=1,
        fences=[("_wm_rows_t", "SELECT * FROM t WHERE i < 5")],
    )

    assert result.rows == [[5, 4]]


@pytest.mark.parametrize("name", ['x"; DROP TABLE t; --', "two words", ""])
def test_fence_names_must_be_plain_identifiers(db: Path, name: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        engine.execute_sync(db, "SELECT 1", max_rows=1, fences=[(name, "SELECT * FROM t")])


def test_a_fence_must_be_a_single_select(db: Path) -> None:
    with pytest.raises(engine.QueryFailedError):
        engine.execute_sync(db, "SELECT 1", max_rows=1, fences=[("f", "DROP TABLE t")])


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("SELECT CAST(label AS INTEGER) FROM t", "Conversion Error"),
        ("SELECT CAST(label AS DATE) FROM t", "Conversion Error"),
        ("SELECT (i + 9223372036854775800)::BIGINT * 10 FROM t", "Out of Range Error"),
    ],
)
def test_errors_about_values_do_not_quote_them(db: Path, sql: str, kind: str) -> None:
    with pytest.raises(engine.QueryFailedError) as failed:
        engine.execute_sync(db, sql, max_rows=50)

    assert failed.value.message.startswith(kind)
    assert "row " not in failed.value.message
    assert "9223372036854775" not in failed.value.message


# --- The worker pool --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pool() -> Iterator[engine.QueryPool]:
    pool = engine.QueryPool(workers=2, memory_mb=320, grace_s=0.5, startup_s=20)
    yield pool
    pool.close()


async def test_the_pool_runs_queries_like_the_engine(db: Path, pool: engine.QueryPool) -> None:
    result = await pool.execute(
        db,
        "SELECT i, label FROM _wm_rows_t AS t ORDER BY i",
        max_rows=2,
        fences=[("_wm_rows_t", "SELECT * FROM t WHERE i >= 3")],
    )

    assert [(c.name, c.type) for c in result.columns] == [("i", "BIGINT"), ("label", "VARCHAR")]
    assert result.rows == [[3, "row 3"], [4, "row 4"]]
    assert result.truncated


async def test_the_pool_reports_engine_errors(db: Path, pool: engine.QueryPool) -> None:
    with pytest.raises(engine.QueryFailedError, match="nope"):
        await pool.execute(db, "SELECT nope FROM t", max_rows=1)
    with pytest.raises(engine.DatasetMissingError):
        await pool.execute(db.parent / "gone.duckdb", "SELECT 1", max_rows=1)
    with pytest.raises(engine.QueryTimeoutError):
        await pool.execute(
            db,
            "SELECT count(*) FROM range(1000000000) a, range(1000000) b",
            max_rows=1,
            timeout_s=0.3,
        )


async def test_a_query_over_the_memory_limit_is_stopped(db: Path, pool: engine.QueryPool) -> None:
    # One row, one value: a list DuckDB builds outside its own memory limit.
    with pytest.raises(engine.QueryFailedError, match="too much memory"):
        await pool.execute(db, "SELECT range(200000000) AS r", max_rows=1, timeout_s=30)

    # Its worker was replaced: the next query runs.
    assert (await pool.execute(db, "SELECT 1", max_rows=1)).rows == [[1]]


async def test_a_query_stuck_in_one_function_is_killed(db: Path, pool: engine.QueryPool) -> None:
    await pool.execute(db, "SELECT 1", max_rows=1)  # warm: the worker has started
    started = time.perf_counter()

    # DuckDB's interrupt is not honoured inside this call: it would take a minute.
    with pytest.raises(engine.QueryTimeoutError, match=r"longer than 0\.5 s"):
        await pool.execute(
            db,
            "SELECT levenshtein(repeat('a', 100000), repeat('b', 100000))",
            max_rows=1,
            timeout_s=0.5,
        )

    assert time.perf_counter() - started < 5
    assert (await pool.execute(db, "SELECT 2", max_rows=1)).rows == [[2]]


async def test_a_large_result_is_refused(db: Path) -> None:
    small = engine.QueryPool(workers=1, max_result_bytes=100_000)
    try:
        with pytest.raises(engine.QueryFailedError, match="larger than"):
            await small.execute(db, "SELECT repeat('a', 200000) AS s", max_rows=1)
    finally:
        small.close()


async def test_one_person_cannot_take_every_worker(db: Path, pool: engine.QueryPool) -> None:
    slow = "SELECT levenshtein(repeat('a', 100000), repeat('b', 100000))"
    await asyncio.gather(*(pool.execute(db, "SELECT 1", max_rows=1) for _ in range(2)))  # warm

    first = asyncio.create_task(pool.execute(db, slow, max_rows=1, timeout_s=1.0, owner="dan"))
    await asyncio.sleep(0.2)
    # Dan's second query waits for his first; Carol's runs at once on the other worker.
    second = asyncio.create_task(
        pool.execute(db, "SELECT 1", max_rows=1, timeout_s=0.5, owner="dan")
    )
    started = time.perf_counter()
    carol = await pool.execute(db, "SELECT 3", max_rows=1, owner="carol")

    assert carol.rows == [[3]]
    assert time.perf_counter() - started < 1
    with pytest.raises(engine.QueryBusyError):
        await second
    with pytest.raises(engine.QueryTimeoutError):
        await first


async def test_pools_that_share_slots_share_the_per_person_limit(db: Path) -> None:
    # Two pools are two API processes; with shared slots (Valkey in the API, analytics/slots.py)
    # one person's second query waits in the other pool too.
    slots = engine.LocalSlots()
    one, two = (
        engine.QueryPool(workers=1, memory_mb=320, grace_s=0.5, startup_s=20, slots=slots)
        for _ in range(2)
    )
    try:
        await asyncio.gather(
            one.execute(db, "SELECT 1", max_rows=1), two.execute(db, "SELECT 1", max_rows=1)
        )
        slow = "SELECT levenshtein(repeat('a', 100000), repeat('b', 100000))"
        first = asyncio.create_task(one.execute(db, slow, max_rows=1, timeout_s=1.0, owner="dan"))
        await asyncio.sleep(0.2)

        with pytest.raises(engine.QueryBusyError):
            await two.execute(db, "SELECT 1", max_rows=1, timeout_s=0.3, owner="dan")
        assert (await two.execute(db, "SELECT 3", max_rows=1, owner="carol")).rows == [[3]]
        with pytest.raises(engine.QueryTimeoutError):
            await first
        assert (await two.execute(db, "SELECT 4", max_rows=1, owner="dan")).rows == [[4]]
    finally:
        one.close()
        two.close()


async def test_a_cancelled_query_stops_its_worker(db: Path, pool: engine.QueryPool) -> None:
    await pool.execute(db, "SELECT 1", max_rows=1)
    task = asyncio.create_task(
        pool.execute(db, "SELECT levenshtein(repeat('a', 100000), repeat('b', 100000))", max_rows=1)
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (await pool.execute(db, "SELECT 4", max_rows=1)).rows == [[4]]

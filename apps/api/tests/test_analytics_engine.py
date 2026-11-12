"""The DuckDB sandbox, called directly (no SQL guard): even SQL that skipped the guard cannot
read files, reach the network, change settings or write; long queries stop; results are capped
and JSON-safe."""

import asyncio
import json
import time
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

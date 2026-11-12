"""Tenant datasets: versioned DuckDB files built from uploaded tables (CSV, or XLSX: first sheet,
header row).

Files live at <root>/<tenant_id>/<version>.duckdb, where root is <object_storage_path>/analytics.
A change never edits the current file: build() copies the current tables into a new version
(written under a temporary name and renamed when complete), and the caller then switches the
tenant's `analytics_models.dataset_version` to it in one transaction (service.py). Readers only
open the version that row names, so they never see a half-written file. The previous file is
deleted after the switch.

Building is trusted code working on files we name, so this connection may read files; queries
never use it (engine.py opens the result read-only, without file access).
"""

import contextlib
import csv
import hashlib
import io
import re
import secrets
import shutil
import tempfile
import uuid
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Literal

import duckdb
import openpyxl
from sqlglot import exp

from workmind.modules.analytics.semantic import IDENTIFIER, RESERVED_PREFIXES

MAX_TABLES = 50
# An upload's limits beyond its size in bytes: a wide header or a small XLSX that unzips into
# millions of cells would otherwise take minutes (pure Python, while the model row is locked).
MAX_COLUMNS = 500
MAX_XLSX_CELLS = 2_000_000
VERSION = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_TYPES = frozenset({"text/csv", "application/csv", "text/plain", "text/x-csv"})
BUILD_CONFIG: dict[str, str | bool | int] = {
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "python_enable_replacements": False,
    "threads": 2,
    "memory_limit": "1GB",
}
Kind = Literal["csv", "xlsx"]


class DatasetError(ValueError):
    """An upload that cannot become a table; the message says why, for the admin."""


@dataclass(frozen=True)
class TableSource:
    name: str
    data: bytes
    content_type: str
    filename: str | None = None


@dataclass(frozen=True)
class TableInfo:
    rows: int
    columns: list[dict[str, str]]  # [{"name", "type"}]
    source: str
    sha256: str
    loaded_at: str

    def as_json(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "columns": self.columns,
            "source": self.source,
            "sha256": self.sha256,
            "loaded_at": self.loaded_at,
        }


@dataclass(frozen=True)
class BuiltDataset:
    version: str
    path: Path
    tables: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])


def tenant_dir(root: Path, tenant_id: uuid.UUID) -> Path:
    return root / str(tenant_id)


def dataset_path(root: Path, tenant_id: uuid.UUID, version: str) -> Path:
    if not VERSION.match(version):  # never a path from anywhere else
        raise ValueError(f"invalid dataset version {version!r}")
    return tenant_dir(root, tenant_id) / f"{version}.duckdb"


def new_version() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{secrets.token_hex(4)}"


def check_table_name(name: str) -> str:
    if not IDENTIFIER.match(name) or name.startswith(RESERVED_PREFIXES):
        raise DatasetError(
            f"Invalid table name {name!r}: use lower-case letters, digits and underscores, "
            "starting with a letter (and not a reserved prefix such as pg_ or duckdb_)."
        )
    return name


def build(
    root: Path,
    tenant_id: uuid.UUID,
    *,
    base: Path | None,
    base_tables: dict[str, dict[str, Any]] | None = None,
    put: Sequence[TableSource] = (),
    drop: Collection[str] = (),
) -> BuiltDataset:
    """Write a new dataset version: the tables of `base` (minus `drop`, and those replaced by
    `put`) plus `put`. Blocking: run it in a worker thread."""
    for source in put:
        check_table_name(source.name)
    replaced = {s.name for s in put} | set(drop)
    kept_info = {n: i for n, i in (base_tables or {}).items() if n not in replaced}

    version = new_version()
    final = dataset_path(root, tenant_id, version)
    final.parent.mkdir(parents=True, exist_ok=True)
    building = final.with_name(f".{version}.building.duckdb")
    connection = duckdb.connect(str(building), config=dict(BUILD_CONFIG))
    try:
        kept = _copy_base(connection, base, replaced) if base is not None else []
        tables = {name: kept_info.get(name, _describe_existing(connection, name)) for name in kept}
        with tempfile.TemporaryDirectory(prefix="wm-dataset-") as scratch:
            for source in put:
                tables[source.name] = _load(connection, source, Path(scratch)).as_json()
        if len(tables) > MAX_TABLES:
            raise DatasetError(f"A dataset holds at most {MAX_TABLES} tables.")
        connection.execute("CHECKPOINT")
        connection.close()
        building.replace(final)  # atomic: the complete file appears under its final name
    except BaseException:
        with contextlib.suppress(duckdb.Error):
            connection.close()
        for leftover in (building, building.with_name(building.name + ".wal"), final):
            leftover.unlink(missing_ok=True)
        raise
    return BuiltDataset(version=version, path=final, tables=tables)


def remove(path: Path) -> None:
    """Delete a dataset version that is no longer current."""
    for leftover in (path, path.with_name(path.name + ".wal")):
        leftover.unlink(missing_ok=True)


def remove_tenant(root: Path, tenant_id: uuid.UUID) -> None:
    shutil.rmtree(tenant_dir(root, tenant_id), ignore_errors=True)


def table_names(path: Path) -> list[str]:
    connection = duckdb.connect(str(path), read_only=True, config=dict(BUILD_CONFIG))
    try:
        rows = connection.execute(
            "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main' ORDER BY 1"
        ).fetchall()
    finally:
        connection.close()
    return [str(r[0]) for r in rows]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _copy_base(
    connection: duckdb.DuckDBPyConnection, base: Path, skip: Collection[str]
) -> list[str]:
    connection.execute(f"ATTACH {_literal(str(base))} AS wm_base (READ_ONLY)")
    try:
        names = [
            str(r[0])
            for r in connection.execute(
                "SELECT table_name FROM duckdb_tables() "
                "WHERE database_name = 'wm_base' AND schema_name = 'main' ORDER BY 1"
            ).fetchall()
        ]
        kept = [n for n in names if n not in skip and IDENTIFIER.match(n)]
        for name in kept:
            table = _identifier(name)
            # Identifiers are quoted by sqlglot (and checked against IDENTIFIER above).
            copy = f"CREATE TABLE {table} AS SELECT * FROM wm_base.main.{table}"  # noqa: S608
            connection.execute(copy)
    finally:
        connection.execute("DETACH wm_base")
    return kept


def _describe_existing(connection: duckdb.DuckDBPyConnection, name: str) -> dict[str, Any]:
    return TableInfo(
        rows=_count(connection, name),
        columns=_columns(connection, name),
        source="",
        sha256="",
        loaded_at=datetime.now(UTC).isoformat(),
    ).as_json()


def _load(connection: duckdb.DuckDBPyConnection, source: TableSource, scratch: Path) -> TableInfo:
    kind = _kind(source)
    csv_path = scratch / f"{source.name}.csv"
    if kind == "xlsx":
        csv_path.write_text(_xlsx_to_csv(source.data), encoding="utf-8")
    else:
        _check_csv_width(source)
        csv_path.write_bytes(source.data)
    table = _identifier(source.name)
    try:
        connection.execute(
            f"CREATE TABLE {table} AS SELECT * FROM read_csv({_literal(str(csv_path))}, "  # noqa: S608
            "header = true, auto_detect = true, sample_size = -1)"
        )
    except duckdb.Error as exc:
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise DatasetError(
            f"{source.filename or source.name}: could not read the table ({detail})"
        ) from exc
    _normalize_columns(connection, source.name)
    rows = _count(connection, source.name)
    if rows == 0:
        raise DatasetError(f"{source.filename or source.name}: the table has no rows")
    return TableInfo(
        rows=rows,
        columns=_columns(connection, source.name),
        source=source.filename or f"{source.name}.{kind}",
        sha256=sha256(source.data),
        loaded_at=datetime.now(UTC).isoformat(),
    )


def _kind(source: TableSource) -> Kind:
    content_type = source.content_type.split(";")[0].strip().lower()
    suffix = Path(source.filename or "").suffix.lower()
    zipped = source.data[:4] == b"PK\x03\x04"
    if content_type == XLSX or (suffix == ".xlsx" and zipped):
        if not zipped:
            raise DatasetError(f"{source.filename or source.name}: not a valid XLSX file")
        return "xlsx"
    if content_type in CSV_TYPES or suffix == ".csv":
        if zipped or b"\x00" in source.data[:4096]:
            raise DatasetError(f"{source.filename or source.name}: not a text CSV file")
        return "csv"
    raise DatasetError(
        f"{source.filename or source.name}: unsupported file type {content_type or '?'} "
        "(upload CSV or XLSX)"
    )


def _check_csv_width(source: TableSource) -> None:
    """Refuse a header wider than MAX_COLUMNS, whichever delimiter DuckDB will detect."""
    header = source.data[: 1024 * 1024].split(b"\n", 1)[0]
    widest = max(header.count(d) for d in (b",", b";", b"\t", b"|")) + 1
    if widest > MAX_COLUMNS:
        raise DatasetError(
            f"{source.filename or source.name}: the table has more than {MAX_COLUMNS} columns"
        )


def _xlsx_to_csv(data: bytes) -> str:
    """The first sheet, its first non-empty row as the header, as CSV text (at most
    MAX_COLUMNS columns and MAX_XLSX_CELLS cells)."""
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for broken files
        raise DatasetError(f"not a readable XLSX file ({type(exc).__name__})") from exc
    try:
        sheet = workbook.worksheets[0]
        rows: Iterable[tuple[Any, ...]] = sheet.iter_rows(values_only=True)
        header: list[str] | None = None
        body: list[list[Any]] = []
        for row in rows:
            cells = list(row)
            if all(c is None or (isinstance(c, str) and not c.strip()) for c in cells):
                continue
            if header is None:
                header = [str(c).strip() if c is not None else "" for c in cells]
                while header and not header[-1]:
                    header.pop()
                if not header or any(not h for h in header):
                    raise DatasetError("the first row of the sheet must name every column")
                if len(header) > MAX_COLUMNS:
                    raise DatasetError(f"the sheet has more than {MAX_COLUMNS} columns")
                continue
            if (len(body) + 1) * len(header) > MAX_XLSX_CELLS:
                raise DatasetError(
                    f"the sheet has more than {MAX_XLSX_CELLS:,} cells: upload it as CSV"
                )
            body.append((cells + [None] * len(header))[: len(header)])
        if header is None:
            raise DatasetError("the first sheet is empty")
    finally:
        workbook.close()
    # A column is written as timestamps if any of its values has a time of day, else as
    # dates, so DuckDB types the whole column as one or the other.
    timed = [
        any(isinstance(r[i], datetime) and r[i].time() != datetime.min.time() for r in body)
        for i in range(len(header))
    ]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for row in body:
        writer.writerow([_cell(value, timed[i]) for i, value in enumerate(row)])
    return out.getvalue()


def _cell(value: Any, timed: bool) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime | date):
        moment = value if isinstance(value, datetime) else datetime.combine(value, time.min)
        return moment.isoformat(sep=" ") if timed else moment.date().isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def normalized_column(name: str) -> str:
    """'Amount (EUR)' -> 'amount_eur': the identifiers the semantic model can refer to."""
    text = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "column"
    return text if re.match(r"^[a-z_]", text) else f"c_{text}"


def _normalize_columns(connection: duckdb.DuckDBPyConnection, table: str) -> None:
    taken: set[str] = set()
    renames: list[tuple[str, str]] = []
    for column in _columns(connection, table):
        current = column["name"]
        wanted = normalized_column(current)[:63]
        candidate, n = wanted, 2
        while candidate in taken:
            candidate, n = f"{wanted[:59]}_{n}", n + 1
        taken.add(candidate)
        if candidate != current:
            renames.append((current, candidate))
    # Two passes, so a new name never collides with a column not renamed yet.
    for step in (
        [(old, f"_wm_rename_{i}") for i, (old, _) in enumerate(renames)],
        [(f"_wm_rename_{i}", new) for i, (_, new) in enumerate(renames)],
    ):
        for old, new in step:
            connection.execute(
                f"ALTER TABLE {_identifier(table)} RENAME COLUMN "
                f"{_identifier(old)} TO {_identifier(new)}"
            )


def _columns(connection: duckdb.DuckDBPyConnection, table: str) -> list[dict[str, str]]:
    rows = connection.execute(
        "SELECT column_name, data_type FROM duckdb_columns() "
        "WHERE database_name = current_database() AND schema_name = 'main' AND table_name = ? "
        "ORDER BY column_index",
        [table],
    ).fetchall()
    return [{"name": str(r[0]), "type": str(r[1])} for r in rows]


def _count(connection: duckdb.DuckDBPyConnection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {_identifier(table)}").fetchone()  # noqa: S608
    return int(row[0]) if row else 0


def _identifier(name: str) -> str:
    return exp.to_identifier(name, quoted=True).sql(dialect="duckdb")


def _literal(value: str) -> str:
    return exp.Literal.string(value).sql(dialect="duckdb")

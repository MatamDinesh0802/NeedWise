"""Dataset storage: CSV and XLSX uploads become tables in versioned DuckDB files; a change writes
a new version and leaves the current one untouched until the caller switches."""

import io
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from workmind.modules.analytics import dataset, engine

TENANT = uuid.UUID(int=42)
CUSTOMERS = b"customer_id,name,since\nC1,Alpha,2026-01-01\nC2,Beta,2026-02-01\n"


def csv_source(name: str, data: bytes = CUSTOMERS) -> dataset.TableSource:
    return dataset.TableSource(
        name=name, data=data, content_type="text/csv", filename=f"{name}.csv"
    )


def xlsx(rows: Sequence[Sequence[Any]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(list(row))
    workbook.create_sheet("Ignored").append(["only the first sheet is read"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def query(path: Path, sql: str) -> list[list[engine.JsonValue]]:
    return engine.execute_sync(path, sql, max_rows=100).rows


def files(root: Path) -> list[str]:
    return sorted(p.name for p in dataset.tenant_dir(root, TENANT).iterdir())


def test_csv_tables_are_typed_and_described(tmp_path: Path) -> None:
    built = dataset.build(tmp_path, TENANT, base=None, put=[csv_source("customers")])

    assert built.path == tmp_path / str(TENANT) / f"{built.version}.duckdb"
    assert files(tmp_path) == [built.path.name]  # no temporary files left behind
    info = built.tables["customers"]
    assert info["rows"] == 2
    assert info["columns"] == [
        {"name": "customer_id", "type": "VARCHAR"},
        {"name": "name", "type": "VARCHAR"},
        {"name": "since", "type": "DATE"},
    ]
    assert info["source"] == "customers.csv"
    assert info["sha256"] == dataset.sha256(CUSTOMERS)
    assert query(built.path, "SELECT name FROM customers WHERE since > DATE '2026-01-15'") == [
        ["Beta"]
    ]


def test_xlsx_first_sheet_with_a_header_row(tmp_path: Path) -> None:
    data = xlsx(
        [
            [None, None, None],  # leading empty rows are skipped
            ["Month", "Product Line", "Revenue (EUR)"],
            [datetime(2026, 1, 1), "AMR-200", 1_180_000],
            [date(2026, 2, 1), "AMR-200", 1_240_000.5],
            [None, None, None],
            [datetime(2026, 3, 1, 9, 30), "FleetOS", 1_130_000],
        ]
    )
    source = dataset.TableSource(
        name="revenue", data=data, content_type=dataset.XLSX, filename="revenue.xlsx"
    )

    built = dataset.build(tmp_path, TENANT, base=None, put=[source])

    assert built.tables["revenue"]["columns"] == [
        {"name": "month", "type": "TIMESTAMP"},
        {"name": "product_line", "type": "VARCHAR"},
        {"name": "revenue_eur", "type": "DOUBLE"},
    ]
    assert query(built.path, "SELECT sum(revenue_eur) FROM revenue") == [[3_550_000.5]]


def test_column_names_become_identifiers(tmp_path: Path) -> None:
    data = b"Customer ID,Amount (EUR),amount_eur,2026\nC1,1,2,3\n"

    built = dataset.build(tmp_path, TENANT, base=None, put=[csv_source("t", data)])

    names = [c["name"] for c in built.tables["t"]["columns"]]
    assert names == ["customer_id", "amount_eur", "amount_eur_2", "c_2026"]


def test_a_change_writes_a_new_version_and_keeps_the_rest(tmp_path: Path) -> None:
    first = dataset.build(
        tmp_path, TENANT, base=None, put=[csv_source("customers"), csv_source("prospects")]
    )
    changed = b"customer_id,name,since\nC3,Gamma,2026-03-01\n"

    second = dataset.build(
        tmp_path,
        TENANT,
        base=first.path,
        base_tables=first.tables,
        put=[csv_source("customers", changed)],
    )

    assert second.version != first.version
    assert set(second.tables) == {"customers", "prospects"}
    assert second.tables["prospects"] == first.tables["prospects"]
    assert query(second.path, "SELECT name FROM customers") == [["Gamma"]]
    assert query(second.path, "SELECT count(*) FROM prospects") == [[2]]
    # Readers of the current version are unaffected until the switch.
    assert query(first.path, "SELECT count(*) FROM customers") == [[2]]

    dataset.remove(first.path)
    assert files(tmp_path) == [second.path.name]


def test_drop_a_table(tmp_path: Path) -> None:
    first = dataset.build(
        tmp_path, TENANT, base=None, put=[csv_source("customers"), csv_source("prospects")]
    )

    second = dataset.build(
        tmp_path, TENANT, base=first.path, base_tables=first.tables, drop=["prospects"]
    )

    assert set(second.tables) == {"customers"}
    assert dataset.table_names(second.path) == ["customers"]


def test_a_failed_build_leaves_nothing_behind(tmp_path: Path) -> None:
    first = dataset.build(tmp_path, TENANT, base=None, put=[csv_source("customers")])

    with pytest.raises(dataset.DatasetError, match="no rows"):
        dataset.build(
            tmp_path,
            TENANT,
            base=first.path,
            base_tables=first.tables,
            put=[csv_source("ok"), csv_source("empty", b"a,b\n")],
        )

    assert files(tmp_path) == [first.path.name]


@pytest.mark.parametrize(
    "name", ["Customers", "1st", "pg_users", "duckdb_x", "information_schema", "a-b", "x" * 64]
)
def test_table_names_follow_the_model_rules(tmp_path: Path, name: str) -> None:
    with pytest.raises(dataset.DatasetError, match="Invalid table name"):
        dataset.build(tmp_path, TENANT, base=None, put=[csv_source(name)])


@pytest.mark.parametrize(
    ("data", "content_type", "filename", "message"),
    [
        (b"%PDF-1.7", "application/pdf", "x.pdf", "unsupported file type"),
        (b"PK\x03\x04zip", "text/csv", "x.csv", "not a text CSV"),
        (b"not a zip", dataset.XLSX, "x.xlsx", "not a valid XLSX"),
        (b"PK\x03\x04broken", dataset.XLSX, "x.xlsx", "not a readable XLSX"),
    ],
)
def test_unsupported_or_broken_files(
    tmp_path: Path, data: bytes, content_type: str, filename: str, message: str
) -> None:
    source = dataset.TableSource(name="t", data=data, content_type=content_type, filename=filename)

    with pytest.raises(dataset.DatasetError, match=message):
        dataset.build(tmp_path, TENANT, base=None, put=[source])


def test_xlsx_needs_a_full_header_row(tmp_path: Path) -> None:
    source = dataset.TableSource(
        name="t", data=xlsx([["a", None, "c"], [1, 2, 3]]), content_type=dataset.XLSX
    )

    with pytest.raises(dataset.DatasetError, match="name every column"):
        dataset.build(tmp_path, TENANT, base=None, put=[source])


@pytest.mark.parametrize("separator", [b",", b";", b"\t"])
def test_a_csv_wider_than_the_column_limit_is_refused(tmp_path: Path, separator: bytes) -> None:
    columns = dataset.MAX_COLUMNS + 1
    header = separator.join(b"Col %d" % i for i in range(columns))
    source = csv_source("wide", header + b"\n" + separator.join([b"1"] * columns) + b"\n")

    with pytest.raises(dataset.DatasetError, match="more than 500 columns"):
        dataset.build(tmp_path, TENANT, base=None, put=[source])


def test_an_xlsx_with_too_many_cells_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dataset, "MAX_XLSX_CELLS", 20)
    data = xlsx([["a", "b"], *([[1, 2]] * 11)])  # 22 cells
    source = dataset.TableSource(name="t", data=data, content_type=dataset.XLSX)

    with pytest.raises(dataset.DatasetError, match="more than 20 cells"):
        dataset.build(tmp_path, TENANT, base=None, put=[source])
    smaller = dataset.TableSource(
        name="t", data=xlsx([["a", "b"], *([[1, 2]] * 9)]), content_type=dataset.XLSX
    )
    assert dataset.build(tmp_path, TENANT, base=None, put=[smaller]).tables["t"]["rows"] == 9


@pytest.mark.parametrize("version", ["../../etc/passwd", "latest", "20261112T000000-zz"])
def test_dataset_paths_only_take_generated_versions(tmp_path: Path, version: str) -> None:
    with pytest.raises(ValueError, match="invalid dataset version"):
        dataset.dataset_path(tmp_path, TENANT, version)

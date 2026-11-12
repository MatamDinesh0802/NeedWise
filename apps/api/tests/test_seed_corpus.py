"""The corpus generator's spreadsheets: complete sheet names and reproducible bytes."""

import io
import zipfile

import pytest
from openpyxl import load_workbook

from workmind.modules.knowledge.parsing import detect_mime, parse
from workmind.seed.corpus import DEFAULT_OUT, SHEET_NAME_MAX, sheet_name, to_xlsx

MATRIX = DEFAULT_OUT / "legal-contracts" / "contract-signature-authority-matrix.xlsx"


@pytest.mark.parametrize(
    ("heading", "taken", "expected"),
    [
        ("Signature authority", [], "Signature authority"),
        # Excel allows 31 characters: a longer heading loses whole words, never half of one.
        ("Rules that apply to every contract", [], "Rules that apply to every"),
        ("Supercalifragilisticexpialidocious-and-more", [], "Supercalifragilisticexpialidoci"),
        ("Revenue: 2026/Q1 [draft]", [], "Revenue 2026 Q1 draft"),
        ("", [], "Sheet"),
        # Excel compares sheet names ignoring case.
        ("Summary", ["summary"], "Summary 2"),
        ("Summary", ["Summary", "Summary 2"], "Summary 3"),
        ("A" * 31, ["A" * 31], "A" * 29 + " 2"),
    ],
)
def test_sheet_names_fit_excel_without_cutting_a_word(
    heading: str, taken: list[str], expected: str
) -> None:
    name = sheet_name(heading, taken)

    assert name == expected
    assert len(name) <= SHEET_NAME_MAX


def test_the_same_markdown_gives_the_same_workbook_bytes() -> None:
    markdown = "# Bands\n\n## Engineering\n\n| Level | Min |\n|---|---|\n| L3 | 68,000 |\n"

    first, second = to_xlsx(markdown, "Bands"), to_xlsx(markdown, "Bands")

    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert {entry.date_time for entry in archive.infolist()} == {(2026, 1, 1, 0, 0, 0)}
        core = archive.read("docProps/core.xml").decode()
    assert core.count("2026-01-01T00:00:00Z") == 2  # created and modified
    workbook = load_workbook(io.BytesIO(first))
    assert workbook.sheetnames == ["Engineering"]
    assert workbook["Engineering"]["B2"].value == 68000


def test_every_seeded_workbook_has_complete_sheet_names() -> None:
    workbooks = sorted(DEFAULT_OUT.rglob("*.xlsx"))
    assert workbooks, "the seed corpus has spreadsheets"
    for path in workbooks:
        for name in load_workbook(path, read_only=True).sheetnames:
            assert sheet_name(name) == name, f"{path.name}: {name!r}"


def test_the_signature_authority_matrix_names_its_rules_sheet_in_full() -> None:
    # It was "Rules that apply to every contr", cut at Excel's 31 characters; that name was
    # the section citations showed.
    assert load_workbook(MATRIX, read_only=True).sheetnames == [
        "Signature authority",
        "Rules for every contract",
    ]
    document = parse(MATRIX.read_bytes(), MATRIX.name, detect_mime(MATRIX.name))
    assert {s.heading_path[0] for s in document.sections} == {
        "Signature authority",
        "Rules for every contract",
    }

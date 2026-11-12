"""Number verification of the BI agent's narrative: which numbers are found, which are ignored,
and when a number counts as backed by a query result."""

import pytest

from workmind.modules.analytics.verify import extract, verify


def found(text: str) -> list[tuple[str, float]]:
    return [(m.text, m.value) for m in extract(text)]


def same(actual: list[tuple[str, float]], expected: list[tuple[str, float]]) -> bool:
    return [t for t, _ in actual] == [t for t, _ in expected] and all(
        abs(a - e) <= 1e-9 * max(1.0, abs(e))
        for (_, a), (_, e) in zip(actual, expected, strict=True)
    )


# --- Formats --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Revenue was €38.9M.", [("€38.9M", 38_900_000)]),
        ("Revenue was € 38.9 M.", [("€ 38.9 M", 38_900_000)]),
        ("FleetOS billed EUR 1,520,000.", [("EUR 1,520,000", 1_520_000)]),
        ("FleetOS billed 1,520,000 EUR.", [("1,520,000 EUR", 1_520_000)]),
        ("ARR of 2,300,040 euros", [("2,300,040 euros", 2_300_040)]),
        ("38.9 million euros", [("38.9 million euros", 38_900_000)]),
        ("1.52 M€", [("1.52 M€", 1_520_000)]),
        ("€5.02m", [("€5.02m", 5_020_000)]),
        ("€640k and €640K", [("€640k", 640_000), ("€640K", 640_000)]),
        ("3.2bn EUR", [("3.2bn EUR", 3_200_000_000)]),
        ("€1.1 billion", [("€1.1 billion", 1_100_000_000)]),
        ("$5k", [("$5k", 5_000)]),
        ("1\u202f520\u202f000 EUR", [("1\u202f520\u202f000 EUR", 1_520_000)]),
        ("478 invoice lines", [("478", 478)]),
        ("24 customers", [("24", 24)]),
        ("an average of 0.45", [("0.45", 0.45)]),
        ("1,200.50 units", [("1,200.50", 1200.5)]),
    ],
)
def test_amounts_units_and_separators(text: str, expected: list[tuple[str, float]]) -> None:
    assert same(found(text), expected), found(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A win rate of 50%.", [("50%", 50)]),
        ("A win rate of 50 %.", [("50 %", 50)]),
        ("up 12.5 percent", [("12.5 percent", 12.5)]),
        ("a 3 pp gain", [("3 pp", 3)]),
        ("2.5 percentage points", [("2.5 percentage points", 2.5)]),
        ("grew +8%", [("+8%", 8)]),
        ("fell -4.1%", [("-4.1%", -4.1)]),
        ("fell \u22124.1%", [("\u22124.1%", -4.1)]),
        ("a change of -€120k", [("-€120k", -120_000)]),
        ("grew 2.3x", [("2.3x", 2.3)]),
        ("a share of .5%", [(".5%", 0.5)]),
        ("10-15% growth", [("15%", 15)]),  # a range: the second bound is not negative
        ("(1,200)", [("1,200", 1200)]),
    ],
)
def test_percentages_signs_and_multiples(text: str, expected: list[tuple[str, float]]) -> None:
    assert same(found(text), expected), found(text)


# --- False positives ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "In 2026 revenue grew.",
        "Data through 2026-09-30, and for 2026-09.",
        "Comparing Q3 with Q1, Q3 2026, 2026-Q3, Q1-Q3, H1 and FY2026.",
        "See [Q1] and [Q2, Q3].",
        "The top 3 customers over 9 months; 12 deals; 2 lost.",
        "AMR-200, AMR-500, OPP-001, C001 and INV-00001.",
        "On 30 September, by Sep 30, 2026, and the 15th of June.",
        "the 3rd largest; 21st; 5Mbps; 10am",
        "`SELECT sum(amount_eur) FROM invoice_lines LIMIT 1000`",
        "See https://example.test/report/123456 for more.",
        "1. Bluefin Freight\n2. Meridian Auto Parts\n13. Polaris Parcel\n14) Harbor",
        "European style 5.020.000,00 is not read.",
        "FleetOS is billed for 12m",
    ],
)
def test_labels_dates_markers_and_identifiers_are_not_numbers(text: str) -> None:
    assert found(text) == []


def test_years_with_a_currency_or_separator_are_amounts() -> None:
    assert found("€2026 and 2,026 units") == [("€2026", 2026), ("2,026", 2026)]


# --- Matching -------------------------------------------------------------------------------


def test_numbers_match_cells_at_the_precision_shown() -> None:
    results = {"Q1": [["2026-09-01", 5_018_700], ["2026-08-01", 1_520_000]]}

    checked = verify("€5.02M [Q1], €5.0M, €5M, 1,520,000 and €1.5M, but not €5.03M.", results)

    assert [(n.text, n.verified) for n in checked.numbers] == [
        ("€5.02M", True),
        ("€5.0M", True),
        ("€5M", True),
        ("1,520,000", True),
        ("€1.5M", True),
        ("€5.03M", False),
    ]
    assert (checked.verified, checked.total) == (5, 6)
    assert [n.text for n in checked.unverified()] == ["€5.03M"]


def test_exact_amounts_need_the_exact_value() -> None:
    results = {"Q1": [[2_300_040]]}

    assert verify("ARR is €2,300,040.", results).verified == 1
    assert verify("ARR is €2,300,041.", results).verified == 0
    assert verify("ARR is €2.3M.", results).verified == 1


def test_rounding_goes_both_ways() -> None:
    results = {"Q1": [[12.349], [7.25], [0.4567]]}

    checked = verify("12.3%, 12.35%, 7.3%, 7.2% and 45.7%", results)

    assert [n.verified for n in checked.numbers] == [True, True, True, True, True]
    assert not verify("12.4%", results).numbers[0].verified


def test_percentages_match_fractions() -> None:
    assert verify("a 12.5% share", {"Q1": [[0.125]]}).verified == 1
    assert verify("a 50% win rate", {"Q1": [[50.0]]}).verified == 1
    assert verify("a 50% win rate", {"Q1": [[5.0]]}).verified == 0
    # Fractions only: 1.12 is a ratio, not 12%.
    assert verify("grew 12%", {"Q1": [[1.12]]}).verified == 0


def test_signs_must_agree_when_written() -> None:
    results = {"Q1": [[-4.1], [8.0]]}

    assert verify("fell 4.1%", results).verified == 1  # no sign written: absolute values
    assert verify("-4.1%", results).verified == 1
    assert verify("+4.1%", results).verified == 0
    assert verify("+8%", results).verified == 1
    assert verify("-8%", results).verified == 0


def test_only_numeric_cells_count() -> None:
    results = {"Q1": [["1520000", True, None, "2026-09-01"]]}

    checked = verify("FleetOS billed €1,520,000 and 1 deal.", results)

    assert checked.total == 1
    assert checked.verified == 0


def test_the_cited_query_is_checked_first_and_reported() -> None:
    results = {"Q1": [[1_520_000]], "Q2": [[1_520_000]]}

    first = verify("FleetOS: €1.52M [Q2]\nAlso €1.52M again.", results)
    plain = verify("FleetOS: €1.52M", results)

    assert [(n.text, n.query) for n in first.numbers] == [("€1.52M", "Q2")]  # listed once
    assert plain.numbers[0].query == "Q1"


def test_a_narrative_without_numbers_verifies_trivially() -> None:
    checked = verify("Revenue grew every month in Q3 [Q1].", {"Q1": [[1]]})

    assert (checked.verified, checked.total, checked.numbers) == (0, 0, [])


def test_a_realistic_narrative() -> None:
    narrative = (
        "**Revenue reached €38.9M from January to September 2026** [Q1], rising from "
        "€3.2M in January to €5.6M in September.\n\n"
        "The top 3 drivers of growth from Q1 to Q3 [Q2]:\n"
        "1. Bluefin Freight: +€1.43M (+68.1%)\n"
        "2. Meridian Auto Parts: +€0.9M\n"
        "3. Polaris Parcel: +€512,000\n\n"
        "FleetOS alone billed 1,520,000 EUR in September (AMR-500 deals closed in Q2)."
    )
    results = {
        "Q1": [["2026-01-01", 3_210_000], ["2026-09-01", 5_604_000], ["total", 38_900_000]],
        "Q2": [
            ["Bluefin Freight", 1_430_400, 68.12],
            ["Meridian Auto Parts", 901_000, 40.0],
            ["Polaris Parcel", 512_000, 30.0],
        ],
        "Q3": [["FleetOS", 1_520_000]],
    }

    checked = verify(narrative, results)

    assert [(n.text, n.verified, n.query) for n in checked.numbers] == [
        ("€38.9M", True, "Q1"),
        ("€3.2M", True, "Q1"),
        ("€5.6M", True, "Q1"),
        ("+€1.43M", True, "Q2"),
        ("+68.1%", True, "Q2"),
        ("+€0.9M", True, "Q2"),
        ("+€512,000", True, "Q2"),
        ("1,520,000 EUR", True, "Q3"),
    ]

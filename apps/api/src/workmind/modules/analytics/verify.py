"""Number verification for the BI agent's narrative (ARCHITECTURE section 16).

Every number the narrative states must be a value from the query results. This module finds the
numbers in a markdown narrative and checks each against the result cells:

    verification = verify(narrative, {"Q1": rows, "Q2": rows})

What counts as a number: amounts with a currency (EUR 1,520,000, €5.02M, 38.9 million euros),
unit suffixes (k/K, M, mn, million, bn/B, billion; lower-case m only next to a currency),
thousands separators (1,520,000; thin spaces), decimals, percentages (12.5%, 12.5 percent,
3 pp), multiples (2.3x) and signs (+8%, -4.1%, U+2212 minus).

What is ignored: years (1900 to 2100 written without separators, units or decimals), ISO dates
(2026-09-30, 2026-09), days next to a month name (30 September, Sep 30), quarter and half-year
labels (Q3, Q3 2026, 2026-Q3, H1, FY2026), query markers ([Q1]), numbers inside identifiers
(AMR-200, C001, OPP-001), ordinals (3rd), numbered-list markers, inline code, links, and
integers below 13 without a unit or a currency ("top 3", "9 months"). European separators
(5.020.000,00) are not recognised: such a number is skipped, not checked.

A number is verified when some numeric result cell equals it at the precision shown: the cell,
divided by the unit's scale, is within half a unit of the last digit shown (€5.02M matches
5,018,700; €2M matches anything from 1.5 M to 2.5 M). Percentages also match fractions (12.5%
matches 0.125). Without an explicit sign, absolute values are compared, since "fell 4%" is
usually computed as -4; with one (+4%), the sign must agree too.

This checks that numbers were copied from the results, not that the narrative reads them
right: a correct value attached to the wrong label still verifies.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, JsonValue

Kind = Literal["amount", "percent", "plain"]
SMALL_INTEGER = 13  # integers below this, without a unit, are counts like "top 3", not data

_MONTHS = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
# Spans blanked out before numbers are looked for (replaced by spaces, keeping positions).
_IGNORED = re.compile(
    r"`[^`\n]*`"  # inline code
    r"|\[(?:Q\d+(?:\s*[,;]\s*)?)+\]"  # query markers [Q1], [Q1, Q2]
    r"|\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+Z?)?\b"  # ISO dates and timestamps
    r"|\b\d{4}-\d{2}\b"  # 2026-09
    r"|\b(?:FY\s?)?\d{4}[-/ ]?[QH][1-4]\b"  # 2026-Q3, FY2026 Q3
    r"|\b[QH][1-4](?:[-/ ]\d{2,4})?\b"  # Q3, H1, Q3 2026, Q3/26
    r"|\bFY\s?\d{2,4}\b"  # FY2026
    r"|https?://\S+"  # links
    r"|^[ \t]*\d+[.)][ \t]",  # numbered list items
    re.IGNORECASE | re.MULTILINE,
)
_DAY_BEFORE_MONTH = re.compile(r"^\s*(?:of\s+)?" + _MONTHS + r"\b", re.IGNORECASE)
_MONTH_BEFORE_DAY = re.compile(r"\b" + _MONTHS + r"\.?\s*$", re.IGNORECASE)
_NUMBER = re.compile(
    r"""
    (?<![\w.,\u2009\u202f\u00a0])     # not inside a word, an identifier or a longer number
    (?P<pre>(?:€|\$|£|EUR|USD|GBP)\s?)?
    (?P<sign>[+\-\u2212])?
    (?P<pre2>[€$£])?
    (?P<num>\d{1,3}(?:[,\u2009\u202f\u00a0]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    (?!\d|[.,]\d)                     # the whole number
    (?:\s?(?P<unit>
        %|per\s?cent\b|percentage\s+points?\b|pp\b
        |thousand\b|k\b|K\b
        |million\b|mn\b|M\b|m\b
        |billion\b|bn\b|B\b
        |[x\u00d7](?!\w)
    ))?
    (?:\s?(?P<post>€|\bEUR\b|\beuros?\b|\bUSD\b|\bdollars?\b|\$))?
    (?!\w)                            # not followed by letters: 3rd, 5Mbps, 10am
    """,
    re.VERBOSE,
)
_SCALES = {
    "thousand": 1e3,
    "k": 1e3,
    "million": 1e6,
    "mn": 1e6,
    "m": 1e6,
    "billion": 1e9,
    "bn": 1e9,
    "b": 1e9,
}
_SIGNS = {"+": 1, "-": -1, "\u2212": -1}
_MARKER = re.compile(r"\[(Q\d+(?:\s*[,;]\s*Q\d+)*)\]")


class VerifiedNumber(BaseModel):
    text: str  # as written in the narrative, e.g. "€5.02M"
    value: float  # its value, scaled by the unit (percentages as written: 12.5 for 12.5%)
    verified: bool
    query: str | None  # the query whose results contain it


class Verification(BaseModel):
    numbers: list[VerifiedNumber]
    verified: int
    total: int

    def unverified(self) -> list[VerifiedNumber]:
        return [n for n in self.numbers if not n.verified]


@dataclass(frozen=True)
class Mention:
    text: str
    start: int
    shown: float  # the digits as written, unsigned and unscaled: 5.02 for "€5.02M"
    decimals: int  # digits after the decimal point, as written
    scale: float  # 1, 1e3, 1e6, 1e9
    sign: int  # +1 / -1 when written, 0 when not
    kind: Kind

    @property
    def value(self) -> float:
        return (self.sign or 1) * self.shown * self.scale

    def matches(self, cell: float) -> bool:
        candidates = [cell]
        if self.kind == "percent" and -1 <= cell <= 1:
            candidates.append(cell * 100)  # a fraction shown as a percentage
        tolerance = 0.5 * 10 ** (-self.decimals) + 1e-9
        for candidate in candidates:
            if self.sign and candidate != 0 and math.copysign(1, candidate) != self.sign:
                continue
            if abs(abs(candidate) / self.scale - self.shown) <= tolerance:
                return True
        return False


def extract(text: str) -> list[Mention]:
    """The numbers stated in `text`, in order (see the module docstring for what is ignored)."""
    cleaned = _IGNORED.sub(lambda m: " " * len(m.group(0)), text)
    found: list[Mention] = []
    for match in _NUMBER.finditer(cleaned):
        mention = _mention(cleaned, match)
        if mention is not None:
            found.append(mention)
    return found


def _mention(text: str, match: re.Match[str]) -> Mention | None:
    raw = match.group("num")
    unit = (match.group("unit") or "").strip()
    currency = bool(match.group("pre") or match.group("pre2") or match.group("post"))
    sign_text = match.group("sign")
    start, end, number_at = match.start(), match.end(), match.start("num")

    # "AMR-200", "OPP-001": a number after a letter and a hyphen is part of an identifier. After
    # a digit ("10-15%"), the hyphen marks a range and the number is unsigned.
    if (
        start == number_at
        and number_at >= 2
        and text[number_at - 1] in "-\u2212"
        and text[number_at - 2].isalpha()
    ):
        return None

    kind: Kind = "plain"
    scale = 1.0
    lowered = unit.lower().replace(" ", "")
    if unit == "%" or lowered.startswith(("percent", "per", "pp")):
        kind = "percent"
    elif lowered in ("x", "\u00d7"):
        pass  # a multiple: compared as written
    elif unit == "m" and not currency:
        # "12m" without a currency is ambiguous (months, metres): keep the number only.
        end, unit = match.start("unit"), ""
        if match.group("post"):
            return None
    elif unit:
        scale = _SCALES[lowered]
    if currency and kind != "percent":
        kind = "amount"

    digits = raw
    for separator in (",", "\u2009", "\u202f", "\u00a0"):
        digits = digits.replace(separator, "")
    shown = float(digits)
    decimals = len(digits.partition(".")[2])

    if kind == "plain" and not unit and decimals == 0 and digits == raw and not sign_text:
        if 1900 <= shown <= 2100:
            return None  # a year
        if shown < SMALL_INTEGER:
            return None  # "top 3", "9 months"
        if shown <= 31 and _next_to_month(text, number_at, match.end("num")):
            return None  # "30 September", "Sep 30"

    return Mention(
        text=text[start:end].strip(),
        start=start,
        shown=shown,
        decimals=decimals,
        scale=scale,
        sign=_SIGNS[sign_text] if sign_text else 0,
        kind=kind,
    )


def _next_to_month(text: str, start: int, end: int) -> bool:
    return bool(_DAY_BEFORE_MONTH.match(text[end : end + 12])) or bool(
        _MONTH_BEFORE_DAY.search(text[max(0, start - 12) : start])
    )


def numeric_cells(rows: Sequence[Sequence[JsonValue]]) -> list[float]:
    cells: list[float] = []
    for row in rows:
        for value in row:
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float) and math.isfinite(value):
                cells.append(float(value))
    return cells


def verify(text: str, results: Mapping[str, Sequence[Sequence[JsonValue]]]) -> Verification:
    """Check every number in `text` against the numeric cells of `results` (query id -> rows).
    A number on the same line as a query marker is looked up in that query first. A number
    written twice the same way is listed once."""
    cells = {query: numeric_cells(rows) for query, rows in results.items()}
    lines = _line_markers(text)
    numbers: list[VerifiedNumber] = []
    seen: set[str] = set()
    for mention in extract(text):
        if mention.text in seen:
            continue
        seen.add(mention.text)
        cited = lines.get(text.count("\n", 0, mention.start), [])
        order = [q for q in cited if q in cells] + [q for q in cells if q not in cited]
        query = next((q for q in order if any(mention.matches(c) for c in cells[q])), None)
        numbers.append(
            VerifiedNumber(
                text=mention.text, value=mention.value, verified=query is not None, query=query
            )
        )
    verified = sum(1 for n in numbers if n.verified)
    return Verification(numbers=numbers, verified=verified, total=len(numbers))


def _line_markers(text: str) -> dict[int, list[str]]:
    markers: dict[int, list[str]] = {}
    for number, line in enumerate(text.split("\n")):
        for match in _MARKER.finditer(line):
            for query in re.split(r"\s*[,;]\s*", match.group(1)):
                markers.setdefault(number, []).append(query)
    return markers

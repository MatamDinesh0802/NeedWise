"""Suggests a sensitivity label from what a document contains (ARCHITECTURE section 7.3).

A deliberately simple, fast scan for *data*, not topics: a leave policy that mentions medical
notes, or a runbook that mentions diagnosis, is not sensitive. Words alone never raise a label.
It can only raise the collection's default, never lower it. Collections that hold personal
data (salaries, reviews, payroll) are labelled through their default; Presidio can replace this
scan for named-entity detection.

- restricted: account or identity numbers that pass their checksum or format rules
- confidential: a pay word next to an actual amount ("salary ... 68,000")
"""

import re

LEVELS = ("internal", "confidential", "restricted")

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
# US SSN layout, excluding numbers that are never issued (000, 666, 9xx areas; 00 groups).
_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_PAY = re.compile(r"\b(salary|salaries|pay band|compensation|bonus|payroll|wage)s?\b", re.I)
_AMOUNT = re.compile(
    r"(?:[€$£]\s?\d[\d,.]*|\b\d{1,3}(?:[,.]\d{3})+\b|\b\d+(?:[.,]\d+)?\s?(?:k|million|euros?|eur)\b)",
    re.I,
)
PAY_WINDOW = 80  # characters between a pay word and an amount


def iban_valid(candidate: str) -> bool:
    """ISO 13616 mod-97 check: random letter-digit runs almost never pass."""
    compact = candidate.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def _pay_with_amount(text: str) -> bool:
    amounts = [m.start() for m in _AMOUNT.finditer(text)]
    if not amounts:
        return False
    return any(
        any(abs(position - word.start()) <= PAY_WINDOW for position in amounts)
        for word in _PAY.finditer(text)
    )


def scan(text: str) -> set[str]:
    findings: set[str] = set()
    if any(iban_valid(m.group(0)) for m in _IBAN.finditer(text)):
        findings.add("iban")
    if _SSN.search(text):
        findings.add("national_id")
    if _pay_with_amount(text):
        findings.add("compensation")
    return findings


RESTRICTED_SIGNALS = frozenset({"iban", "national_id"})
CONFIDENTIAL_SIGNALS = frozenset({"compensation"})


def suggest(default: str, findings: set[str]) -> str:
    level = LEVELS.index(default)
    if findings & RESTRICTED_SIGNALS:
        level = max(level, 2)
    elif findings & CONFIDENTIAL_SIGNALS:
        level = max(level, 1)
    return LEVELS[level]

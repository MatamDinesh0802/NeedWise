"""Suggests a sensitivity label from what a document contains (ARCHITECTURE section 7.3).

A deliberately simple, fast pattern scan. It can only raise the collection's default, never
lower it, and the collection owner can always override. Presidio can replace it for
named-entity detection.
"""

import re

LEVELS = ("internal", "confidential", "restricted")
PATTERNS = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b"),
    "national_id": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "compensation": re.compile(r"\b(salary|salaries|pay band|compensation|bonus|payroll)\b", re.I),
    "health": re.compile(r"\b(medical|diagnosis|sick leave note|disability)\b", re.I),
}
RESTRICTED_SIGNALS = frozenset({"iban", "national_id", "health"})
CONFIDENTIAL_SIGNALS = frozenset({"compensation"})


def scan(text: str) -> set[str]:
    return {kind for kind, pattern in PATTERNS.items() if pattern.search(text)}


def suggest(default: str, findings: set[str]) -> str:
    level = LEVELS.index(default)
    if findings & RESTRICTED_SIGNALS:
        level = max(level, 2)
    elif findings & CONFIDENTIAL_SIGNALS:
        level = max(level, 1)
    return LEVELS[level]

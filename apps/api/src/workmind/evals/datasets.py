"""Evaluation datasets (ARCHITECTURE 22.2): JSON Lines files in `evals/datasets/`.

Documents are referred to by file name (`leave-policy-2026.pdf`), which is stable across
re-seeds, unlike database ids. A fact is a string, or a list of acceptable spellings.
"""

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Fact = str | list[str]


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    user: str  # the part of the e-mail address before "@"
    question: str


class QAItem(_Item):
    expected: list[str]
    facts: list[Fact]
    category: str


class RedTeamItem(_Item):
    kind: str
    forbidden_files: list[str]
    canaries: list[str]


class AbstentionItem(_Item):
    reason: str


class CrossItem(QAItem):
    """A question that needs more than one department (the multi-agent ablation)."""

    agents: list[str]


class RoutingItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    question: str
    mode: Literal["direct", "single", "fan_out"]
    agents: list[str]


class MeetingItem(BaseModel):
    """A meeting file with its known outcome. Keywords: every group must match, any word of it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    file: str  # in seed/acme/meetings
    title: str
    date: str
    decisions: list[list[list[str]]]
    # Floated but never agreed: must appear neither as a decision nor as an action item.
    not_decisions: list[list[list[str]]] = []
    actions: list[dict[str, Any]]


Suite = Literal["qa", "redteam", "abstention", "cross", "routing", "meetings"]
FILES: dict[Suite, str] = {
    "qa": "retrieval_qa.jsonl",
    "redteam": "red_team.jsonl",
    "abstention": "abstention.jsonl",
    "cross": "cross_department.jsonl",
    "routing": "routing.jsonl",
    "meetings": "meetings.jsonl",
}


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line.strip()]


def load_qa(directory: Path) -> list[QAItem]:
    return [QAItem.model_validate(json.loads(line)) for line in _lines(directory / FILES["qa"])]


def load_red_team(directory: Path) -> list[RedTeamItem]:
    return [
        RedTeamItem.model_validate(json.loads(line))
        for line in _lines(directory / FILES["redteam"])
    ]


def load_abstention(directory: Path) -> list[AbstentionItem]:
    return [
        AbstentionItem.model_validate(json.loads(line))
        for line in _lines(directory / FILES["abstention"])
    ]


def load_cross(directory: Path) -> list[CrossItem]:
    return [
        CrossItem.model_validate(json.loads(line)) for line in _lines(directory / FILES["cross"])
    ]


def load_routing(directory: Path) -> list[RoutingItem]:
    return [
        RoutingItem.model_validate(json.loads(line))
        for line in _lines(directory / FILES["routing"])
    ]


def load_meetings(directory: Path) -> list[MeetingItem]:
    return [
        MeetingItem.model_validate(json.loads(line))
        for line in _lines(directory / FILES["meetings"])
    ]


def keywords_match(groups: list[list[str]], text: str) -> bool:
    haystack = text.lower()
    return all(any(word.lower() in haystack for word in group) for group in groups)


_SPACES = re.compile(r"\s+")


# No-break spaces, en dashes and non-breaking hyphens, as models and documents often write them.
_LOOKALIKES = str.maketrans({0x00A0: " ", 0x202F: " ", 0x2013: "-", 0x2011: "-"})


def normalize(text: str) -> str:
    text = text.translate(_LOOKALIKES).replace("**", "")
    return _SPACES.sub(" ", text).strip().lower()


def fact_present(fact: Fact, text: str) -> bool:
    haystack = normalize(text)
    options = [fact] if isinstance(fact, str) else fact
    return any(normalize(option) in haystack for option in options)

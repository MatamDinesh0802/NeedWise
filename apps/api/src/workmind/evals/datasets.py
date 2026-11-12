"""Evaluation datasets (ARCHITECTURE 22.2): JSON Lines files in `evals/datasets/`.

Documents are referred to by file name (`leave-policy-2026.pdf`), which is stable across
re-seeds, unlike database ids. A fact is a string, or a list of acceptable spellings.
"""

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

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


RedTeamSurface = Literal["chat", "agents", "research", "analytics"]


class RedTeamItem(_Item):
    """An attack, asked as `user` (scoring: evals/redteam.py).

    Context check (every retrieval mode, also with --retrieval-only: the CI gate): a search as
    the user returns none of `forbidden_files` and no canary. Answer check (live runs): the
    answer on `surface` and everything it read hold no forbidden document, canary or
    `forbidden_text` (leaks), and the answer does not obey the attack (violations).

    surface: "chat" (the assistant as --assistant runs it), "agents" (planner and department
    agents), "research" (the tool-using research agent, actions not stored), "analytics" (the BI
    agent; its query results are checked too).
    forbidden_text: strings only data the user may not see contains (e.g. another account
    executive's customer names); never a word an honest refusal might use.
    obey_tokens: a code word an injected instruction asks for and no document contains.
    forbidden_tools: tools the attack tries to make the surface call.
    answer_only: an attack on behaviour, not on access (system prompt extraction, citation
    spoofing): nothing for the context check, so it has no forbidden documents or canaries.
    """

    kind: str
    forbidden_files: list[str]
    canaries: list[str]
    surface: RedTeamSurface = "chat"
    forbidden_text: list[str] = []
    obey_tokens: list[str] = []
    forbidden_tools: list[str] = []
    answer_only: bool = False

    @model_validator(mode="after")
    def _checkable(self) -> "RedTeamItem":
        if self.answer_only and (self.forbidden_files or self.canaries):
            raise ValueError(f"{self.id}: an answer-only attack has no context checks")
        if not self.answer_only and not (self.forbidden_files or self.canaries):
            raise ValueError(
                f"{self.id}: give forbidden_files or canaries (the context gate), "
                "or mark it answer_only"
            )
        return self


class AbstentionItem(_Item):
    reason: str


class CrossItem(QAItem):
    """A question that needs more than one department (the multi-agent ablation)."""

    agents: list[str]


class RoutingItem(BaseModel):
    """`agents`: exactly the agents the plan must pick (none for "direct"). `not_agents`: a
    near miss names the agent it sounds like but must not go to (reported separately)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    question: str
    mode: Literal["direct", "single", "fan_out"]
    agents: list[str]
    not_agents: list[str] = []

    @model_validator(mode="after")
    def _consistent(self) -> "RoutingItem":
        expected = {"direct": 0, "single": 1}.get(self.mode)
        if expected is not None and len(self.agents) != expected:
            raise ValueError(f"{self.id}: mode {self.mode} needs {expected} agent(s)")
        if self.mode == "fan_out" and len(self.agents) < 2:
            raise ValueError(f"{self.id}: fan_out needs at least two agents")
        if set(self.agents) & set(self.not_agents):
            raise ValueError(f"{self.id}: an agent is both expected and excluded")
        return self


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


class AnalyticsItem(_Item):
    """A question for the BI agent (text-to-SQL), asked as `user`.

    expect "answer": `gold_sql` is run through the SQL guard AS THAT USER (their row policies
    apply) and compared with the agent's main query: "rows" (the whole result; `ordered` when
    the order matters) or "value" (a single gold cell must appear in the result).
    expect "no_access": the user may not query; the agent must refuse without calling a model.
    expect "rejected": an attack; nothing beyond what the user may see may come back.
    """

    category: str
    expect: Literal["answer", "no_access", "rejected"] = "answer"
    gold_sql: str | None = None
    compare: Literal["rows", "value"] | None = None
    ordered: bool = False
    # Dataset check: the gold result as this other user differs (a row policy narrows it).
    differs_from: str | None = None
    # Values that must not appear in any result cell, e.g. company-wide totals for Dan.
    leak_values: list[str | int | float] = []
    # Values (ids, names) this user must not see, listed by SQL run as someone who sees every
    # row: a leak check that does not rely on the row-policy enforcement it checks.
    hidden_sql: str | None = None
    # Attacks: strings only leaked content would contain (file lines, settings, catalog rows).
    forbidden: list[str] = []
    # Attacks: SQL that would carry the attack out; the guard must refuse it (dataset check).
    probe_sql: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "AnalyticsItem":
        if self.expect == "answer" and not (self.gold_sql and self.compare):
            raise ValueError(f"{self.id}: an answer item needs gold_sql and compare")
        if self.expect != "answer" and (self.gold_sql or self.compare):
            raise ValueError(f"{self.id}: only answer items have gold_sql and compare")
        if self.probe_sql and self.expect != "rejected":
            raise ValueError(f"{self.id}: only attack items have probe_sql")
        return self


Suite = Literal["qa", "redteam", "abstention", "cross", "routing", "meetings", "analytics"]
FILES: dict[Suite, str] = {
    "qa": "retrieval_qa.jsonl",
    "redteam": "red_team.jsonl",
    "abstention": "abstention.jsonl",
    "cross": "cross_department.jsonl",
    "routing": "routing.jsonl",
    "meetings": "meetings.jsonl",
    "analytics": "analytics.jsonl",
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


def load_analytics(directory: Path) -> list[AnalyticsItem]:
    return [
        AnalyticsItem.model_validate(json.loads(line))
        for line in _lines(directory / FILES["analytics"])
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

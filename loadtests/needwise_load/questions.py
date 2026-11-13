"""Question sets from evals/datasets, so load-test answers hit real documents.

Each evaluation question names the person who asks it ("user": "carol"); permissions differ by
person, so a load-test user asks its own login's questions when the dataset has some, and any of
the dataset's questions otherwise (routing.jsonl names nobody).

    chat      retrieval_qa.jsonl       one-area questions, answered from the documents
    agents    cross_department.jsonl   questions that span two areas (agents and auto modes)
    insights  analytics.jsonl          business questions, with the SQL that answers them
"""

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DATASETS = {
    "chat": "retrieval_qa.jsonl",
    "agents": "cross_department.jsonl",
    "insights": "analytics.jsonl",
}


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    user: str | None  # the login's local part ("carol", "hannah.schmidt"), or None
    sql: str | None = None  # analytics: the query that answers it
    expect: str | None = None  # analytics: "no_access" for questions that must be refused


def read_dataset(path: Path) -> list[Question]:
    questions: list[Question] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        text = row.get("question")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{path.name}:{number}: no question")
        questions.append(
            Question(
                id=str(row.get("id") or f"{path.stem}-{number}"),
                text=text.strip(),
                user=row.get("user"),
                sql=row.get("gold_sql"),
                expect=row.get("expect"),
            )
        )
    return questions


def local_part(email: str) -> str:
    return email.split("@", 1)[0].lower()


class QuestionBank:
    """The questions of each set, by asker."""

    def __init__(self, sets: dict[str, list[Question]]) -> None:
        self.sets = sets

    @classmethod
    def load(cls, datasets: Path) -> "QuestionBank":
        return cls({name: read_dataset(datasets / file) for name, file in DATASETS.items()})

    def for_login(self, kind: str, email: str) -> list[Question]:
        """This login's questions of the set, or every question of it if it has none."""
        questions = self.sets.get(kind, [])
        own = [q for q in questions if q.user == local_part(email)]
        return own or list(questions)

    def analysts(self, emails: Sequence[str]) -> list[str]:
        """Logins with analytics questions they may ask (and the SQL that answers them)."""
        able = {q.user for q in self.sets.get("insights", []) if q.sql and q.expect is None}
        return [e for e in emails if local_part(e) in able]

    def insights_for(self, email: str) -> list[Question]:
        return [
            q
            for q in self.sets.get("insights", [])
            if q.user == local_part(email) and q.expect is None
        ]


def pick(questions: Sequence[Question], rng: random.Random) -> Question:
    if not questions:
        raise LookupError("no questions to ask")
    return questions[rng.randrange(len(questions))]

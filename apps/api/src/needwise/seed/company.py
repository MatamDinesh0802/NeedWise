"""Schema and validation for seed/<company>/company.yaml."""

from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from needwise.modules.identity.models import SLUG_PATTERN, DepartmentRole, OrgRole

Scope = Literal["personal", "department", "enterprise", "restricted"]
Trust = Literal["official", "reference", "discussion"]
Sensitivity = Literal["internal", "confidential", "restricted"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TenantPolicy(_Strict):
    # ARCHITECTURE 11.6: may `restricted` sources be sent to an external model API?
    restricted_data: Literal["allow", "local_only"] = "local_only"


class CompanyInfo(_Strict):
    name: str
    slug: str = Field(pattern=SLUG_PATTERN)
    domain: str
    industry: str = ""
    headcount: int | None = None
    founded: int | None = None
    timezone: str = "UTC"
    policy: TenantPolicy = TenantPolicy()


class DepartmentSpec(_Strict):
    key: str = Field(pattern=SLUG_PATTERN)
    name: str


class GroupSpec(_Strict):
    key: str = Field(pattern=SLUG_PATTERN)
    description: str = ""


class PersonSpec(_Strict):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str
    title: str | None = None
    department: str | None = None
    dept_role: DepartmentRole = "member"
    org_role: OrgRole = "member"
    groups: list[str] = []
    demo: bool = False  # gets a Keycloak login with the demo password
    red_team: bool = False

    @property
    def first_name(self) -> str:
        return self.name.split(" ", 1)[0]

    @property
    def last_name(self) -> str:
        parts = self.name.split(" ", 1)
        return parts[1] if len(parts) > 1 else parts[0]


class CollectionSpec(_Strict):
    key: str = Field(pattern=SLUG_PATTERN)
    name: str
    scope: Scope
    department: str | None = None
    viewers: list[str] = []
    editors: list[str] = []
    trust: Trust = "reference"
    sensitivity: Sensitivity = "internal"
    description: str = ""
    # Governance (docs/governance.md): the owner (an e-mail) is accountable for the
    # collection's content, approves official status and owns the seeded documents; documents
    # are due for review `review_interval_days` after `last_reviewed` (a fixed date, so the
    # demo is the same on every machine).
    owner: str | None = None
    review_interval_days: int | None = Field(default=None, ge=1, le=3650)
    last_reviewed: date | None = None


class AgentSpec(_Strict):
    key: str = Field(pattern=SLUG_PATTERN)
    name: str
    description: str
    instructions: str = ""
    collections: list[str]
    examples: list[str] = []
    max_searches: int = Field(default=2, ge=1, le=5)


class RoutingRuleSpec(_Strict):
    """Request routing (docs/automation.md): what sends a request to a department's queue."""

    department: str
    keywords: list[str] = []
    categories: list[str] = []


class CompanySpec(_Strict):
    company: CompanyInfo
    departments: list[DepartmentSpec]
    groups: list[GroupSpec] = []
    people: list[PersonSpec]
    collections: list[CollectionSpec] = []
    agents: list[AgentSpec] = []
    request_routing: list[RoutingRuleSpec] = []

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        problems: list[str] = []
        departments = _unique("department", [d.key for d in self.departments], problems)
        groups = _unique("group", [g.key for g in self.groups], problems)
        emails = _unique("person", [p.email.lower() for p in self.people], problems)
        _unique("collection", [c.key for c in self.collections], problems)

        for person in self.people:
            if not person.email.lower().endswith("@" + self.company.domain):
                problems.append(f"{person.email}: email must use @{self.company.domain}")
            if person.department and person.department not in departments:
                problems.append(f"{person.email}: unknown department '{person.department}'")
            problems += [
                f"{person.email}: unknown group '{g}'" for g in person.groups if g not in groups
            ]
        if not any(p.org_role == "owner" for p in self.people):
            problems.append("at least one person needs org_role: owner")

        known = {
            "department": departments,
            "department-manager": departments,
            "group": groups,
            "user": emails,
        }
        for collection in self.collections:
            if collection.scope == "department" and not collection.department:
                problems.append(f"collection {collection.key}: department scope needs a department")
            if collection.department and collection.department not in departments:
                problems.append(f"collection {collection.key}: unknown '{collection.department}'")
            if collection.scope == "restricted" and not collection.viewers:
                problems.append(f"collection {collection.key}: restricted needs viewers")
            if collection.owner and collection.owner.lower() not in emails:
                problems.append(f"collection {collection.key}: unknown owner '{collection.owner}'")
            for principal in [*collection.viewers, *collection.editors]:
                kind, _, key = principal.partition(":")
                if kind not in known or key.lower() not in known[kind]:
                    problems.append(f"collection {collection.key}: unknown viewer '{principal}'")
            if collection.owner and collection.owner.lower() not in emails:
                problems.append(f"collection {collection.key}: unknown owner '{collection.owner}'")
            if collection.last_reviewed and not collection.review_interval_days:
                problems.append(f"collection {collection.key}: last_reviewed needs an interval")

        collection_keys = {c.key for c in self.collections}
        _unique("agent", [a.key for a in self.agents], problems)
        for agent in self.agents:
            problems += [
                f"agent {agent.key}: unknown collection '{c}'"
                for c in agent.collections
                if c not in collection_keys
            ]

        for rule in self.request_routing:
            if rule.department not in departments:
                problems.append(f"request_routing: unknown department '{rule.department}'")
            if not rule.keywords and not rule.categories:
                problems.append(f"request_routing {rule.department}: give keywords or categories")

        if problems:
            raise ValueError("invalid company file:\n  - " + "\n  - ".join(problems))
        return self

    @property
    def demo_people(self) -> list[PersonSpec]:
        return [p for p in self.people if p.demo]


def _unique(kind: str, keys: list[str], problems: list[str]) -> set[str]:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            problems.append(f"duplicate {kind} '{key}'")
        seen.add(key)
    return seen


def load_company(path: Path) -> CompanySpec:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CompanySpec.model_validate(raw)

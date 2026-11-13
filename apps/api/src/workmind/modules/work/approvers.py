"""Who approves an action: approver rules beyond a named person (docs/automation.md).

A rule is written in a workflow step (`approver:`) or by an admin per tool (Admin > Automation,
for the actions the assistant proposes):

    approver: olivia.grant@acme.test                     # one named person (as before)
    approver: {role: org_admins}                         # any org admin
    approver: {department_manager: requester}            # a manager of the requester's department
    approver: {department_manager: finance}              # a manager of the Finance department
    approver: {any_of: [hannah.schmidt@acme.test, {role: org_admins}]}   # any one of them
    approver: {chain: [{department_manager: requester}, hannah.schmidt@acme.test]}
                                                         # all of them, in this order

Stored normalized: a list of stages, each a list of selectors ({"user": email},
{"role": "org_admins"}, {"department_manager": "requester" | <department key>}). Each stage is
an any-of group: everyone it resolves to gets a copy of the approval, the first decision
answers for the stage, and a chain's next stage starts only when the previous one approved.

Never self-approval: every rule other than a single named person leaves out the person who
asked. A chain needs a different person at every stage: whoever approved an earlier stage of a
request gets no copy of a later one (nor an escalated copy), and cannot approve it. A stage left
with nobody is refused when the action is proposed (also when no assignment of distinct people
to the stages exists), and, for a chain's later stage, when it starts. People are resolved when
their stage starts, with the directory as it is then: active members (never guests).
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import Any, Final, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.modules.identity.models import Department, DepartmentMember, User

Selector = dict[str, str]
Stage = list[Selector]
Rule = list[Stage]

MAX_STAGES: Final = 5
MAX_SELECTORS: Final = 20
ADMIN_ROLES: Final = ("owner", "admin")
APPROVER_ROLES: Final = ("owner", "admin", "member", "auditor")  # guests never approve
ROLES: Final = ("org_admins",)
REQUESTER: Final = "requester"


class RuleError(ValueError):
    """A rule that cannot be read or resolved; the message says which part, in plain words."""


# --- Reading ---------------------------------------------------------------------------------


def _mapping(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {str(k): v for k, v in cast(dict[object, Any], raw).items()}


def _items(raw: object) -> list[Any] | None:
    return list(cast(list[Any], raw)) if isinstance(raw, list) else None


def _selector(raw: object, where: str) -> Selector:
    if isinstance(raw, str):
        email = raw.strip().lower()
        if "@" not in email or len(email) > 254 or any(c.isspace() for c in email):
            raise RuleError(f"{where}: {raw!r} is not an email address")
        return {"user": email}
    mapping = _mapping(raw)
    if mapping is not None:
        keys = set(mapping)
        if keys == {"user"}:
            return _selector(mapping["user"], where)
        if keys == {"role"}:
            role = mapping["role"]
            if role not in ROLES:
                raise RuleError(f"{where}: role must be one of {', '.join(ROLES)}")
            return {"role": str(role)}
        if keys == {"department_manager"}:
            value = mapping["department_manager"]
            if not isinstance(value, str) or not value.strip():
                raise RuleError(f"{where}: department_manager is `requester` or a department key")
            return {"department_manager": value.strip().lower()}
    raise RuleError(
        f"{where}: expected an email address, {{role: org_admins}} or "
        "{department_manager: requester | <department key>}"
    )


def _stage(raw: object, where: str) -> Stage:
    mapping = _mapping(raw)
    if mapping is not None and set(mapping) == {"any_of"}:
        members = _items(mapping["any_of"])
        if not members:
            raise RuleError(f"{where}: any_of needs a list of approvers")
        if len(members) > MAX_SELECTORS:
            raise RuleError(f"{where}: any_of takes at most {MAX_SELECTORS} approvers")
        stage = [_selector(m, f"{where}.any_of[{i}]") for i, m in enumerate(members)]
        return [dict(s) for s in {tuple(sorted(s.items())): s for s in stage}.values()]
    return [_selector(raw, where)]


def parse_rule(raw: object, where: str = "approver") -> Rule:
    """A rule as written (a string or an object) -> its stages. Raises RuleError."""
    mapping = _mapping(raw)
    if mapping is not None and "chain" in mapping:
        if set(mapping) != {"chain"}:
            raise RuleError(f"{where}: chain stands alone")
        stages = _items(mapping["chain"])
        if not stages:
            raise RuleError(f"{where}: chain needs a list of stages")
        if len(stages) > MAX_STAGES:
            raise RuleError(f"{where}: a chain has at most {MAX_STAGES} stages")
        return [_stage(s, f"{where}.chain[{i}]") for i, s in enumerate(stages)]
    return [_stage(raw, where)]


def is_single_person(rule: Rule) -> bool:
    """A single named approver: the one case that may name the person who asked (as before:
    a workflow's owner may approve their own workflow's actions)."""
    return len(rule) == 1 and len(rule[0]) == 1 and "user" in rule[0][0]


def describe(rule: Rule) -> str:
    """The rule in words, e.g. "a manager of the requester's department, then
    hannah.schmidt@acme.test"."""

    def selector(s: Selector) -> str:
        if "user" in s:
            return s["user"]
        if s.get("role") == "org_admins":
            return "an org admin"
        department = s.get("department_manager", "")
        if department == REQUESTER:
            return "a manager of the requester's department"
        return f"a manager of {department}"

    stages = [" or ".join(selector(s) for s in stage) for stage in rule]
    return ", then ".join(stages)


def rule_json(rule: Rule) -> list[list[dict[str, str]]]:
    return [[dict(s) for s in stage] for stage in rule]


def stored_rule(value: Any) -> Rule | None:
    """A rule as stored on an approval (already normalized); None for a named approver."""
    if not isinstance(value, list) or not value:
        return None
    stages: list[Any] = list(cast(list[Any], value))
    return parse_rule({"chain": [{"any_of": stage} for stage in stages]})


# --- Resolving -------------------------------------------------------------------------------


async def _active(session: AsyncSession, ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    found = set(
        (
            await session.scalars(
                select(User.id).where(
                    User.id.in_(wanted),
                    User.status == "active",
                    User.org_role.in_(APPROVER_ROLES),
                )
            )
        ).all()
    )
    return [i for i in wanted if i in found]


async def org_admins(session: AsyncSession) -> list[uuid.UUID]:
    return list(
        (
            await session.scalars(
                select(User.id)
                .where(User.org_role.in_(ADMIN_ROLES), User.status == "active")
                .order_by(User.email)
            )
        ).all()
    )


async def managers_of_person(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    """The managers of every department this person belongs to (not the person themselves)."""
    departments = select(DepartmentMember.department_id).where(DepartmentMember.user_id == user_id)
    return list(
        (
            await session.scalars(
                select(DepartmentMember.user_id)
                .where(
                    DepartmentMember.department_id.in_(departments),
                    DepartmentMember.role == "manager",
                    DepartmentMember.user_id != user_id,
                )
                .order_by(DepartmentMember.created_at)
            )
        ).all()
    )


async def managers_of_department(session: AsyncSession, key: str) -> list[uuid.UUID] | None:
    """None when there is no such department."""
    department_id = await session.scalar(select(Department.id).where(Department.slug == key))
    if department_id is None:
        return None
    return await managers_of_department_id(session, department_id)


async def managers_of_department_id(
    session: AsyncSession, department_id: uuid.UUID
) -> list[uuid.UUID]:
    """The department's managers who are active members (never guests)."""
    managers = (
        await session.scalars(
            select(DepartmentMember.user_id)
            .where(
                DepartmentMember.department_id == department_id,
                DepartmentMember.role == "manager",
            )
            .order_by(DepartmentMember.created_at)
        )
    ).all()
    return await _active(session, managers)


async def resolve_stage(
    session: AsyncSession, stage: Stage, requester: uuid.UUID, *, single: bool = False
) -> list[uuid.UUID]:
    """Everyone who may decide this stage, in a stable order. Raises RuleError for a person or
    department that does not exist. `single`: the rule is one named person (who may be the
    requester); otherwise the requester is always left out."""
    people: list[uuid.UUID] = []
    for selector in stage:
        if "user" in selector:
            user = await session.scalar(select(User).where(User.email == selector["user"]))
            if user is None:
                raise RuleError(f"No colleague with the address {selector['user']}")
            people.append(user.id)
        elif selector.get("role") == "org_admins":
            people += await org_admins(session)
        elif (department := selector.get("department_manager")) == REQUESTER:
            people += await managers_of_person(session, requester)
        elif department:
            managers = await managers_of_department(session, department)
            if managers is None:
                raise RuleError(f"No department {department!r}")
            people += managers
    resolved = await _active(session, people)
    if single:
        return resolved
    return [p for p in resolved if p != requester]


def distinct_people(stages: Sequence[Sequence[uuid.UUID]]) -> bool:
    """Whether every stage can be approved by a different person (a matching of stages to
    people, by augmenting paths: chains have at most MAX_STAGES stages)."""
    holder: dict[uuid.UUID, int] = {}

    def place(stage: int, tried: set[uuid.UUID]) -> bool:
        for candidate in stages[stage]:
            if candidate in tried:
                continue
            tried.add(candidate)
            if candidate not in holder or place(holder[candidate], tried):
                holder[candidate] = stage
                return True
        return False

    return all(place(index, set()) for index in range(len(stages)))


async def first_stage(session: AsyncSession, rule: Rule, requester: uuid.UUID) -> list[uuid.UUID]:
    """The first stage's approvers, after checking that every stage resolves to somebody other
    than the requester right now, and that the stages can be approved by different people (a
    chain that cannot finish is refused at once, not days later). Raises RuleError naming the
    stage."""
    single = is_single_person(rule)
    resolved: list[list[uuid.UUID]] = []
    for index, stage in enumerate(rule):
        people = await resolve_stage(session, stage, requester, single=single)
        if not people:
            where = f"stage {index + 1} of {len(rule)}" if len(rule) > 1 else "the approver rule"
            raise RuleError(
                f"Nobody can approve {where} ({describe([stage])}): approvers must be active "
                "colleagues other than the person who asked"
            )
        resolved.append(people)
    if not distinct_people(resolved):
        raise RuleError(
            f"The chain ({describe(rule)}) needs a different person at each of its "
            f"{len(rule)} stages, and these stages do not have that many people between them"
        )
    return resolved[0]


# --- Escalation targets ----------------------------------------------------------------------

EscalateTo = Literal["backup", "manager", "org_admins"]
ESCALATION_ORDER: Final[tuple[EscalateTo, ...]] = ("backup", "manager", "org_admins")


async def escalation_targets(
    session: AsyncSession,
    approver: uuid.UUID,
    *,
    first_choice: EscalateTo,
    backup: uuid.UUID | None,
    exclude: Sequence[uuid.UUID],
) -> tuple[EscalateTo | None, list[uuid.UUID]]:
    """Who an approval goes to when its approver has not decided in time: the approver's backup
    approver, else their department's managers, else the org admins, starting at
    `first_choice`. Never the requester or anyone in `exclude`."""
    skip = {*exclude, approver}
    for choice in ESCALATION_ORDER[ESCALATION_ORDER.index(first_choice) :]:
        if choice == "backup":
            candidates = [backup] if backup else []
        elif choice == "manager":
            candidates = await managers_of_person(session, approver)
        else:
            candidates = await org_admins(session)
        people = [p for p in await _active(session, candidates) if p not in skip]
        if people:
            return choice, people
    return None, []

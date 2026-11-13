"""Budgets: spending limits in USD per UTC day or calendar month (docs/usage.md).

Pure pieces: periods, a budget's status, and the error that refuses a model call. Loading,
spent-so-far and enforcement live in meter.py.

Scopes: "tenant" (the whole organization), "department" (Enterprise, "department_budgets"),
"user" (one person's cap; a user budget without a subject is the default cap for everyone
without a cap of their own). Action "block" refuses model calls once the limit is reached;
"warn" only notifies. Every budget notifies once per period when `warn_at` of it is spent.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from needwise.core.errors import ApiError
from needwise.providers.llm import LLMError

Scope = Literal["tenant", "department", "user"]
Period = Literal["day", "month"]
Action = Literal["block", "warn"]
State = Literal["ok", "warning", "exceeded"]

DEFAULT_WARN_AT = 0.8


def period_window(period: str, now: datetime) -> tuple[datetime, datetime]:
    """The UTC period that contains `now`: [start, end). Budgets reset at `end`."""
    now = now.astimezone(UTC)
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


def state_of(spent: float, limit: float, warn_at: float) -> State:
    if spent >= limit:
        return "exceeded"
    if spent >= warn_at * limit:
        return "warning"
    return "ok"


@dataclass(frozen=True)
class BudgetRule:
    """A stored budget."""

    id: uuid.UUID
    scope: str
    subject_id: uuid.UUID | None  # tenant: None; user: None is the default cap
    period: str
    limit_usd: float
    warn_at: float
    action: str

    @property
    def is_default(self) -> bool:
        return self.scope == "user" and self.subject_id is None


@dataclass(frozen=True)
class Binding:
    """A budget applied to what it limits: the organization, one department or one person
    (for the default cap: the person it applies to)."""

    rule: BudgetRule
    subject_id: uuid.UUID | None  # None only for the tenant budget

    @property
    def key(self) -> tuple[str, uuid.UUID | None, str]:
        """What the spent-so-far is counted over: (scope, subject, period)."""
        return (self.rule.scope, self.subject_id, self.rule.period)


@dataclass(frozen=True)
class NamedRef:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class BudgetStatus:
    id: uuid.UUID
    scope: str
    subject: NamedRef | None
    period: str
    limit_usd: float
    spent_usd: float
    warn_at: float
    status: State
    resets_at: datetime
    action: str

    def as_dict(self, *, with_action: bool = False) -> dict[str, Any]:
        """The API's "Budget status" (docs/usage.md); `action` only for administrators."""
        body: dict[str, Any] = {
            "id": str(self.id),
            "scope": self.scope,
            "subject": {"id": str(self.subject.id), "name": self.subject.name}
            if self.subject
            else None,
            "period": self.period,
            "limit_usd": round(self.limit_usd, 4),
            "spent_usd": round(self.spent_usd, 6),
            "warn_at": self.warn_at,
            "status": self.status,
            "resets_at": self.resets_at.isoformat().replace("+00:00", "Z"),
        }
        if with_action:
            body["action"] = self.action
        return body


def status_of(
    binding: Binding, spent: float, subject: NamedRef | None, now: datetime
) -> BudgetStatus:
    rule = binding.rule
    _, end = period_window(rule.period, now)
    return BudgetStatus(
        id=rule.id,
        scope=rule.scope,
        subject=subject,
        period=rule.period,
        limit_usd=rule.limit_usd,
        spent_usd=spent,
        warn_at=rule.warn_at,
        status=state_of(spent, rule.limit_usd, rule.warn_at),
        resets_at=end,
        action=rule.action,
    )


def usd(amount: float) -> str:
    return f"${amount:,.2f}"


def budget_name(status: BudgetStatus) -> str:
    """In words: the monthly organization budget, the daily budget for Finance, your cap."""
    period = "daily" if status.period == "day" else "monthly"
    if status.scope == "tenant":
        return f"the {period} organization budget"
    if status.scope == "department":
        return (
            f"the {period} budget for {status.subject.name if status.subject else 'a department'}"
        )
    return f"your {period} cap"


def reset_words(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def exceeded_detail(status: BudgetStatus) -> str:
    name = budget_name(status)
    return (
        f"{name[0].upper()}{name[1:]} of {usd(status.limit_usd)} is used up "
        f"({usd(status.spent_usd)} spent). Requests that use an AI model are paused until it "
        f"resets at {reset_words(status.resets_at)}."
    )


class BudgetExceededError(LLMError):
    """A blocking budget is used up, so the model call was not made.

    An LLMError, so every place that already handles a model that cannot answer fails closed
    with it; the API turns it into 429 "budget_exceeded" (main.py), chat runs into a
    `run.failed` event with that code, meetings and workflow runs into a failure that says why.
    """

    def __init__(self, status: BudgetStatus) -> None:
        super().__init__(exceeded_detail(status))
        self.status = status

    @property
    def detail(self) -> str:
        return str(self)

    def event(self) -> dict[str, Any]:
        """The `run.failed` event of a chat run it stopped."""
        return {
            "code": "budget_exceeded",
            "message": self.detail,
            "detail": self.detail,
            "budget": self.status.as_dict(),
        }

    def problem(self) -> ApiError:
        """429 "budget_exceeded" with the budget's status."""
        return ApiError(
            429,
            "budget_exceeded",
            "Budget exceeded",
            self.detail,
            extra={"budget": self.status.as_dict()},
        )

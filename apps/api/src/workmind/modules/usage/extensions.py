"""Where Enterprise plugs into usage accounting (docs/enterprise.md, docs/usage.md).

The core stores every budget and records every event; two features are Enterprise:

- department budgets ("department_budgets"): which departments a budget may name, which of
  them bind a person, which a person may see, and who is warned. Without an implementation
  (Community), department budgets are refused (402) and never enforced;
- the chargeback report ("chargeback_reports"): the CSV of cost per department.

workmind_ee registers implementations of these hooks in its `setup`; the core never imports it.
"""

import uuid
from collections.abc import Awaitable, Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from workmind.core.context import RequestContext
from workmind.core.extensions import ExtensionRegistry, Hook


class DepartmentBudgets(Protocol):
    """Department budgets. Every method runs inside the caller's tenant transaction."""

    async def names(
        self, session: AsyncSession, ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """The departments among `ids` that exist, with their names."""
        ...

    async def binding(self, session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
        """The departments whose budgets limit this person's model calls."""
        ...

    async def visible(self, session: AsyncSession, ctx: RequestContext) -> list[uuid.UUID] | None:
        """The departments whose budgets the caller may see; None for all of them."""
        ...

    async def recipients(self, session: AsyncSession, department_id: uuid.UUID) -> list[uuid.UUID]:
        """Who is warned when the department's budget reaches its warning level."""
        ...


@dataclass(frozen=True)
class ChargebackQuery:
    """The chargeback report's range: UTC calendar days `first` to `last`, both included, as
    the instants [start, end)."""

    tenant_id: uuid.UUID
    first: date
    last: date
    start: datetime
    end: datetime


DEPARTMENT_BUDGETS = Hook[[], DepartmentBudgets]("usage.department_budgets")
CHARGEBACK_REPORT = Hook[[AsyncSession, ChargebackQuery], Awaitable[str]]("usage.chargeback_report")


def department_budgets(extensions: ExtensionRegistry) -> DepartmentBudgets | None:
    """The licensed implementation, or None (Community, or the feature is not licensed)."""
    found = extensions.call(DEPARTMENT_BUDGETS)
    return found[0] if found else None

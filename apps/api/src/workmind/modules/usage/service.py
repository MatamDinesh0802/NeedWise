"""Administering budgets (Admin > Budgets, docs/usage.md): read them with their status, and
replace them all at once.

Department budgets exist only with the Enterprise feature "department_budgets": without it,
they are neither returned, stored nor enforced, and rows kept from an earlier license are
left untouched (they come back if the license does).
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.modules.usage.budgets import Binding
from workmind.modules.usage.meter import UsageMeter, rule_of
from workmind.modules.usage.models import Budget

BudgetKey = tuple[str, uuid.UUID | None, str]  # (scope, subject, period)


@dataclass(frozen=True)
class BudgetSpec:
    scope: str
    subject_id: uuid.UUID | None
    period: str
    limit_usd: float
    warn_at: float
    action: str

    @property
    def key(self) -> BudgetKey:
        return (self.scope, self.subject_id, self.period)


async def admin_view(
    meter: UsageMeter, session: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any]:
    """GET /admin/budgets: every budget with its status and action, and the default cap."""
    rules = [rule_of(row) for row in (await session.scalars(select(Budget))).all()]
    with_departments = meter.department_provider() is not None
    bindings = [
        Binding(r, r.subject_id)
        for r in rules
        if not r.is_default and (r.scope != "department" or with_departments)
    ]
    statuses = await meter.statuses(session, tenant_id, bindings)
    order = {"tenant": 0, "department": 1, "user": 2}
    statuses.sort(
        key=lambda s: (order.get(s.scope, 3), s.subject.name if s.subject else "", s.period)
    )
    default = next((r for r in rules if r.is_default), None)
    return {
        "budgets": [s.as_dict(with_action=True) for s in statuses],
        "user_default": {
            "period": default.period,
            "limit_usd": round(default.limit_usd, 4),
            "action": default.action,
        }
        if default
        else None,
    }


async def replace_budgets(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    specs: list[BudgetSpec],
    with_departments: bool,
    updated_by: uuid.UUID,
) -> None:
    """Makes the stored budgets exactly `specs` (a budget keeps its id while its scope, subject
    and period stay the same, so its warnings are not sent again)."""
    managed: set[str] = {"tenant", "user", *(["department"] if with_departments else [])}
    wanted = {spec.key: spec for spec in specs}
    existing = {
        (row.scope, row.subject_id, row.period): row
        for row in (await session.scalars(select(Budget))).all()
    }
    for key, row in existing.items():
        if row.scope in managed and key not in wanted:
            await session.delete(row)
    await session.flush()
    for key, spec in wanted.items():
        row = existing.get(key)
        if row is None:
            row = Budget(
                tenant_id=tenant_id,
                scope=spec.scope,
                subject_id=spec.subject_id,
                period=spec.period,
            )
            session.add(row)
        row.limit_usd = Decimal(str(spec.limit_usd))
        row.warn_at = spec.warn_at
        row.action = spec.action
        row.updated_by = updated_by
    await session.flush()

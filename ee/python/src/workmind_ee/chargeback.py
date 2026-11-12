"""The chargeback report ("chargeback_reports"): model usage and cost per department, as CSV.

One row per department that used a model in the range (UTC days), largest cost first, then one
for usage without a department (people in none, evaluations). Cost is the sum of the priced
requests; `unpriced_requests` counts the requests whose model has no price, which the cost
leaves out. Department names are written so a spreadsheet never reads them as formulas.
"""

import csv
import io
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from workmind.modules.identity.models import Department
from workmind.modules.usage.extensions import ChargebackQuery
from workmind.modules.usage.models import UsageEvent

COLUMNS = (
    "department",
    "department_id",
    "people",
    "requests",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "unpriced_requests",
)
NO_DEPARTMENT = "No department"
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(text: str) -> str:
    """A cell a spreadsheet shows as text, never runs (OWASP: CSV injection)."""
    return f"'{text}" if text.startswith(_FORMULA_START) else text


async def chargeback_csv(session: AsyncSession, query: ChargebackQuery) -> str:
    rows = (
        await session.execute(
            select(
                UsageEvent.department_id,
                func.count(func.distinct(UsageEvent.user_id)),
                func.count(),
                func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                func.coalesce(func.sum(UsageEvent.cost_usd), 0),
                func.count().filter(UsageEvent.cost_usd.is_(None)),
            )
            .where(UsageEvent.occurred_at >= query.start, UsageEvent.occurred_at < query.end)
            .group_by(UsageEvent.department_id)
        )
    ).tuples()
    totals = list(rows)
    ids = [row[0] for row in totals if row[0] is not None]
    names: dict[uuid.UUID, str] = {}
    if ids:
        found = await session.execute(
            select(Department.id, Department.name).where(Department.id.in_(ids))
        )
        names = {department_id: name for department_id, name in found.tuples()}
    totals.sort(
        key=lambda row: (
            row[0] is None,
            -Decimal(row[5] or 0),
            names.get(row[0] or uuid.UUID(int=0), ""),
        )
    )

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for department_id, people, requests, input_tokens, output_tokens, cost, unpriced in totals:
        name = (
            names.get(department_id, "(deleted department)")
            if department_id is not None
            else NO_DEPARTMENT
        )
        writer.writerow(
            [
                safe_cell(name),
                str(department_id) if department_id else "",
                people,
                requests,
                input_tokens,
                output_tokens,
                f"{Decimal(cost or 0):.6f}",
                unpriced,
            ]
        )
    return out.getvalue()

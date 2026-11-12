"""What else refers to catalog models, so a model in use is not deleted or disabled under it.

The tenant's own roles and data policy are checked by the catalog. Other modules register a
check here (`register_reference_check`); each returns a short description per reference, such
as "the agent 'Legal'". Agent definitions (their `model`) are checked out of the box.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.modules.agents.models import AgentDefinition

ReferenceCheck = Callable[[AsyncSession, frozenset[str]], Awaitable[list[str]]]

_checks: list[ReferenceCheck] = []


def register_reference_check(check: ReferenceCheck) -> None:
    if check not in _checks:
        _checks.append(check)


async def agents_using(session: AsyncSession, model_ids: frozenset[str]) -> list[str]:
    names = (
        await session.scalars(
            select(AgentDefinition.name)
            .where(AgentDefinition.model.in_(sorted(model_ids)))
            .order_by(AgentDefinition.name)
        )
    ).all()
    return [f"the agent {name!r}" for name in names]


register_reference_check(agents_using)


async def references(session: AsyncSession, model_ids: frozenset[str]) -> list[str]:
    """Every registered reference to these models (run in the tenant's transaction)."""
    found: list[str] = []
    for check in _checks:
        found += await check(session, model_ids)
    return found

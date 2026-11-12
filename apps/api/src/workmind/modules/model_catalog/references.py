"""What else refers to catalog models, so a model in use is not deleted or disabled under it.

The tenant's own roles and data policy are checked by the catalog. Other modules register a
check here (`register_reference_check`); each returns a short description per reference, such
as "the agent 'Legal'". Agent definitions are checked out of the box when their table has a
`model` (or `model_id`) column.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ReferenceCheck = Callable[[AsyncSession, frozenset[str]], Awaitable[list[str]]]

_checks: list[ReferenceCheck] = []

_AGENT_MODEL_COLUMN = text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = current_schema() AND table_name = 'agent_definitions' "
    "AND column_name IN ('model', 'model_id') ORDER BY column_name LIMIT 1"
)
_AGENTS_USING = {
    "model": text("SELECT name FROM agent_definitions WHERE model = ANY(:ids) ORDER BY name"),
    "model_id": text("SELECT name FROM agent_definitions WHERE model_id = ANY(:ids) ORDER BY name"),
}


def register_reference_check(check: ReferenceCheck) -> None:
    if check not in _checks:
        _checks.append(check)


async def agents_using(session: AsyncSession, model_ids: frozenset[str]) -> list[str]:
    column = await session.scalar(_AGENT_MODEL_COLUMN)
    if column not in _AGENTS_USING:
        return []
    names = (await session.scalars(_AGENTS_USING[column], {"ids": sorted(model_ids)})).all()
    return [f"the agent {name!r}" for name in names]


register_reference_check(agents_using)


async def references(session: AsyncSession, model_ids: frozenset[str]) -> list[str]:
    """Every registered reference to these models (run in the tenant's transaction)."""
    found: list[str] = []
    for check in _checks:
        found += await check(session, model_ids)
    return found

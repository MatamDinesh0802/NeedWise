"""Seeded knowledge governance (docs/governance.md): owners and review dates for the documents
loaded from the corpus, from each collection's `owner`, `review_interval_days` and
`last_reviewed` in company.yaml.

Deterministic and idempotent: the dates come from the company file, never from the clock, and
only what the product has not changed is touched (an owner still equal to the uploader, a
document never reviewed in the product). Running it again changes nothing.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.tenancy import tenant_transaction
from needwise.modules.identity.models import User
from needwise.modules.knowledge.models import Collection, Document
from needwise.seed.company import CompanySpec

REVIEWED_AT = time(9, 0, tzinfo=UTC)  # seeded reviews happened at 09:00 UTC


@dataclass(frozen=True)
class GovernanceResult:
    owners_set: int
    reviews_set: int


async def seed_governance(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, spec: CompanySpec
) -> GovernanceResult:
    owners_set = reviews_set = 0
    async with tenant_transaction(sessions, tenant_id) as session:
        users = {u.email.lower(): u.id for u in await session.scalars(select(User))}
        collections = {c.key: c for c in await session.scalars(select(Collection))}
        for item in spec.collections:
            collection = collections.get(item.key)
            if collection is None:
                continue
            owner = users.get(item.owner.lower()) if item.owner else None
            documents = (
                await session.scalars(
                    select(Document).where(
                        Document.collection_id == collection.id, Document.source == "seed"
                    )
                )
            ).all()
            for document in documents:
                untouched = document.owner_id in (None, document.created_by)
                if owner is not None and untouched and document.owner_id != owner:
                    document.owner_id = owner
                    owners_set += 1
                if (
                    item.last_reviewed
                    and item.review_interval_days
                    and document.reviewed_at is None
                ):
                    reviewed = datetime.combine(item.last_reviewed, REVIEWED_AT)
                    document.reviewed_at, document.reviewed_by = reviewed, owner
                    document.next_review_at = reviewed + timedelta(days=item.review_interval_days)
                    reviews_set += 1
        await session.flush()
    return GovernanceResult(owners_set=owners_set, reviews_set=reviews_set)

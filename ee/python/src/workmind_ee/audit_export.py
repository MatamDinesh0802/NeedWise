"""Advanced audit export (Enterprise feature "advanced_audit_export"; docs/enterprise.md).

GET /api/v1/admin/audit-events/export.jsonl streams the tenant's whole hash chain, oldest first,
one JSON object per line, for a SIEM. Each line has the fields the hash covers plus `prev_hash`
and `hash` (hex), so the receiver can verify the chain itself:

    hash = sha256(bytes.fromhex(prev_hash or "") + canonical(fields))
    canonical = JSON of the fields (without prev_hash and hash), keys sorted, separators ","/":"

`after_seq` resumes an incremental pull: only events after that one. The export is itself
audited ("audit.export"). The core keeps the hash chain and the CSV export of a filtered view;
this adds the complete, verifiable stream.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workmind.api.deps import resources_of
from workmind.api.v1.admin import AuditContext
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, canonical_fields, record_event
from workmind.modules.governance.models import AuditEvent

FEATURE: Final = "advanced_audit_export"
BATCH: Final = 500

router = APIRouter(tags=["enterprise"])


def line(event: AuditEvent) -> str:
    record = {
        **canonical_fields(event),
        "prev_hash": event.prev_hash.hex() if event.prev_hash else None,
        "hash": event.hash.hex(),
    }
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str) + "\n"


async def chain_lines(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, after_seq: int
) -> AsyncIterator[str]:
    """Oldest first, in batches, each batch in its own tenant transaction (no long locks)."""
    last = after_seq
    while True:
        async with tenant_transaction(sessions, tenant_id) as session:
            events = (
                await session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.seq > last)
                    .order_by(AuditEvent.seq)
                    .limit(BATCH)
                )
            ).all()
        if not events:
            return
        for event in events:
            yield line(event)
        last = events[-1].seq


@router.get("/admin/audit-events/export.jsonl")
async def export_chain(
    ctx: AuditContext,
    request: Request,
    after_seq: Annotated[int, Query(ge=0, description="only events after this one")] = 0,
) -> StreamingResponse:
    sessions = resources_of(request).db
    async with tenant_transaction(sessions, ctx.tenant_id) as session:
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="audit.export",
            resource_type="audit_log",
            details={"format": "jsonl", "after_seq": after_seq},
        )
    return StreamingResponse(
        chain_lines(sessions, ctx.tenant_id, after_seq),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": 'attachment; filename="audit-chain.jsonl"',
            "Cache-Control": "no-store",
        },
    )

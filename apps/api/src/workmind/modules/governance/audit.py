"""Append-only, hash-chained audit log.

Each event stores the SHA-256 of (previous event's hash + this event's canonical content), per
tenant, ordered by `seq`. Editing or deleting any past event breaks every later hash, which
`verify_chain` detects. The runtime database role cannot UPDATE or DELETE audit rows at all
(migration 0002); the chain also catches changes made with higher privileges.
"""

import hashlib
import ipaddress
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.modules.governance.models import AuditEvent

ActorType = Literal["user", "service", "system"]


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: uuid.UUID | None = None

    @classmethod
    def system(cls) -> "Actor":
        return cls(type="system")


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    checked: int
    first_broken_seq: int | None = None


def canonical_fields(event: AuditEvent) -> dict[str, Any]:
    """The fields the hash covers, as JSON values. Exports carry them so an outside system
    can recompute every hash: sha256(prev_hash + canonical JSON of these fields)."""
    ip = str(ipaddress.ip_address(event.ip)) if event.ip is not None else None
    return {
        "seq": event.seq,
        "tenant_id": str(event.tenant_id),
        "occurred_at": event.occurred_at.astimezone(UTC).isoformat(),
        "actor_type": event.actor_type,
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "details": event.details,
        "ip": ip,
        "user_agent": event.user_agent,
    }


def _canonical(event: AuditEvent) -> bytes:
    payload = canonical_fields(event)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _digest(prev_hash: bytes | None, event: AuditEvent) -> bytes:
    return hashlib.sha256((prev_hash or b"") + _canonical(event)).digest()


async def record_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor: Actor,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditEvent:
    """Append an event. Must run inside a tenant transaction (see core.tenancy)."""
    # Serialize appends per tenant so seq and prev_hash can never fork.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"audit:{tenant_id}"},
    )
    last = (
        await session.execute(
            select(AuditEvent.seq, AuditEvent.hash)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        )
    ).first()
    prev_seq, prev_hash = (last[0], last[1]) if last else (0, None)

    event = AuditEvent(
        tenant_id=tenant_id,
        seq=prev_seq + 1,
        occurred_at=datetime.now(UTC),
        actor_type=actor.type,
        actor_id=actor.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip=ip,
        user_agent=user_agent[:512] if user_agent else None,
        prev_hash=prev_hash,
    )
    event.hash = _digest(prev_hash, event)
    session.add(event)
    await session.flush()
    return event


async def verify_chain(session: AsyncSession, tenant_id: uuid.UUID) -> ChainVerification:
    """Recompute every hash for a tenant, in order. Detects edits, deletions and reordering."""
    prev_hash: bytes | None = None
    expected_seq = 1
    stream = await session.stream_scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
    )
    async for event in stream:
        if (
            event.seq != expected_seq
            or event.prev_hash != prev_hash
            or event.hash != _digest(prev_hash, event)
        ):
            return ChainVerification(ok=False, checked=expected_seq - 1, first_broken_seq=event.seq)
        prev_hash = event.hash
        expected_seq += 1
    return ChainVerification(ok=True, checked=expected_seq - 1)


async def count_events(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    )
    return total or 0

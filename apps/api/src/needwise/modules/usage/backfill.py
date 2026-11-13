"""`nw usage backfill`: usage from before usage events were recorded (docs/usage.md).

Where usage was stored before, and what is imported from it:

- chat answers (messages.usage: model, tokens, cost): one event per answer, feature "research"
  when the answer has tool steps, else "chat"; ref ("message", id);
- BI answers (analytics_runs.answer.usage: model, tokens, cost): one event per run, feature
  "analytics"; ref ("analytics_run", id);
- meetings (meetings.timings.model: the extraction model's name, no token counts): one event
  per processed meeting with 0 tokens and no cost (unknown); ref ("meeting", id).

Each record is imported at most once, and never when it already has live events:

- a record with any live event that refers to it (same ref) is skipped: it was recorded live;
- backfilled events are unique per (ref type, ref id) (index uq_usage_events_backfill), and
  the insert skips conflicts, so running it again adds nothing.

The department is the person's department now (when the command runs), not when the record
was made: the history of memberships is not kept. Provider and latency are unknown.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import String, cast, exists, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.tenancy import tenant_transaction
from needwise.modules.analytics.models import AnalyticsRun
from needwise.modules.conversations.models import Conversation, Message
from needwise.modules.meetings.models import Meeting
from needwise.modules.usage.departments import primary_department
from needwise.modules.usage.models import UsageEvent

BATCH = 500
MESSAGE_STATUS = {"complete": "ok", "cancelled": "cancelled", "failed": "failed"}


@dataclass
class BackfillResult:
    messages: int = 0
    analytics_runs: int = 0
    meetings: int = 0

    @property
    def total(self) -> int:
        return self.messages + self.analytics_runs + self.meetings


def _live(ref_type: str, ref_id: Any) -> Any:
    """Whether live events refer to this record."""
    return exists().where(
        UsageEvent.source == "live",
        UsageEvent.ref_type == ref_type,
        UsageEvent.ref_id == cast(ref_id, String),
    )


def _cost(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _tokens(usage: dict[str, Any], name: str) -> int:
    value = usage.get(name)
    return max(0, int(value)) if isinstance(value, int | float) else 0


class _Writer:
    """Collects events and inserts them in batches, skipping ones already backfilled."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.rows: list[dict[str, Any]] = []
        self.inserted = 0
        self._departments: dict[uuid.UUID, uuid.UUID | None] = {}

    async def add(
        self,
        *,
        user_id: uuid.UUID,
        occurred_at: datetime,
        feature: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: Decimal | None,
        ref: tuple[str, uuid.UUID],
        status: str = "ok",
    ) -> None:
        if user_id not in self._departments:
            self._departments[user_id] = await primary_department(self.session, user_id)
        self.rows.append(
            {
                "tenant_id": self.tenant_id,
                "occurred_at": occurred_at,
                "user_id": user_id,
                "department_id": self._departments[user_id],
                "agent_key": None,
                "feature": feature,
                "model": (model or "unknown")[:200],
                "model_id": None,
                "provider": "unknown",
                "local": False,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "ref_type": ref[0],
                "ref_id": str(ref[1]),
                "latency_ms": None,
                "status": status,
                "source": "backfill",
            }
        )
        if len(self.rows) >= BATCH:
            await self.flush()

    async def flush(self) -> int:
        if not self.rows:
            return self.inserted
        result = await self.session.scalars(
            pg_insert(UsageEvent)
            .values(self.rows)
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "ref_type", "ref_id"],
                index_where=text("source = 'backfill'"),
            )
            .returning(UsageEvent.id)
        )
        self.inserted += len(result.all())
        self.rows = []
        return self.inserted


async def backfill_tenant(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> BackfillResult:
    result = BackfillResult()
    async with tenant_transaction(sessions, tenant_id) as session:
        writer = _Writer(session, tenant_id)
        answers = await session.execute(
            select(Message, Conversation.user_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.role == "assistant", ~_live("message", Message.id))
            .order_by(Message.created_at)
        )
        for message, user_id in answers:
            usage = message.usage or {}
            used = (_tokens(usage, "input_tokens"), _tokens(usage, "output_tokens"))
            if not any(used):
                continue  # no model call was recorded with it
            await writer.add(
                user_id=user_id,
                occurred_at=message.created_at,
                feature="research" if message.tool_steps else "chat",
                model=str(usage.get("model") or ""),
                input_tokens=used[0],
                output_tokens=used[1],
                cost=_cost(usage.get("cost_usd")),
                ref=("message", message.id),
                status=MESSAGE_STATUS.get(message.status, "ok"),
            )
        result.messages = await writer.flush()

        runs = await session.scalars(
            select(AnalyticsRun)
            .where(~_live("analytics_run", AnalyticsRun.id))
            .order_by(AnalyticsRun.created_at)
        )
        for run in runs:
            raw = (run.answer or {}).get("usage")
            usage: dict[str, Any] = raw if isinstance(raw, dict) else {}  # pyright: ignore[reportUnknownVariableType]
            used = (_tokens(usage, "input_tokens"), _tokens(usage, "output_tokens"))
            if not any(used):
                continue
            await writer.add(
                user_id=run.user_id,
                occurred_at=run.created_at,
                feature="analytics",
                model=str(usage.get("model") or ""),
                input_tokens=used[0],
                output_tokens=used[1],
                cost=_cost(usage.get("cost_usd")),
                ref=("analytics_run", run.id),
                status="failed" if run.status == "failed" else "ok",
            )
        result.analytics_runs = await writer.flush() - result.messages

        meetings = await session.scalars(
            select(Meeting)
            .where(~_live("meeting", Meeting.id), Meeting.timings["model"].isnot(None))
            .order_by(Meeting.created_at)
        )
        for meeting in meetings:
            await writer.add(
                user_id=meeting.owner_id,
                occurred_at=meeting.updated_at,
                feature="meetings",
                model=str(meeting.timings.get("model") or ""),
                input_tokens=0,  # not stored before usage events
                output_tokens=0,
                cost=None,  # unknown, never a guess
                ref=("meeting", meeting.id),
            )
        result.meetings = await writer.flush() - result.messages - result.analytics_runs
    return result

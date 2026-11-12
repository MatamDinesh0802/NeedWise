"""Usage accounting and budget enforcement for every model call (docs/usage.md).

The tenant resolver calls `admit` before each model call and `record` after it. Both are
cheap when a tenant has no budgets: the budgets are cached in the process for RULES_TTL_S.

Spent-so-far per budget and period is cached in Valkey under a key per (tenant, scope,
subject, period, period start). A missing key is computed from usage_events and kept for
SPENT_TTL_S; every recorded event adds its cost to the keys that exist (and only those, so a
key never holds a partial sum). Cost is known only after a call, so a budget can be overshot:
by the calls admitted while it was still below its limit (at most one call per request in
flight), plus events that land in the moment between a key's refresh from the database and
its first increment (corrected at the next refresh, within SPENT_TTL_S).
"""

import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Final, cast

import structlog
from redis.asyncio import Redis
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.extensions import ExtensionRegistry
from workmind.core.tenancy import tenant_transaction
from workmind.modules.identity.models import User
from workmind.modules.usage.budgets import (
    Binding,
    BudgetExceededError,
    BudgetRule,
    BudgetStatus,
    NamedRef,
    budget_name,
    period_window,
    reset_words,
    state_of,
    status_of,
    usd,
)
from workmind.modules.usage.calls import UNATTRIBUTED, Attribution, ModelCall
from workmind.modules.usage.departments import primary_department_query
from workmind.modules.usage.extensions import DepartmentBudgets, department_budgets
from workmind.modules.usage.models import Budget, BudgetAlert, UsageEvent
from workmind.modules.work.links import usage_link
from workmind.modules.work.notifications import notify
from workmind.providers.llm import LLMUsage
from workmind.providers.pricing import cost_usd

log = structlog.get_logger("workmind.usage")

RULES_TTL_S: Final = 10.0
BINDING_TTL_S: Final = 30.0
SPENT_TTL_S: Final = 60
ADMIN_ROLES: Final = ("owner", "admin")

# Adds to a spent-so-far key only if it exists: a missing key is computed in full instead.
_INCR_IF_EXISTS: Final = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return redis.call('INCRBYFLOAT', KEYS[1], ARGV[1])
end
return false
"""

SpentKey = tuple[str, uuid.UUID | None, str]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def call_cost(call: ModelCall) -> float | None:
    """USD for the call: the tenant catalog's price, else the static table (pricing.py).
    None when a response that used tokens has no price; 0 for a priced call that used none."""
    used = [u for u in call.usages if u.input_tokens or u.output_tokens]
    if not used:
        return cost_usd(LLMUsage(model=call.model, price_per_mtok=call.price_per_mtok))
    costs = [cost_usd(u) for u in used]
    if any(c is None for c in costs):
        return None
    return sum(c or 0.0 for c in costs)


def rule_of(row: Budget) -> BudgetRule:
    return BudgetRule(
        id=row.id,
        scope=row.scope,
        subject_id=row.subject_id,
        period=row.period,
        limit_usd=float(row.limit_usd),
        warn_at=row.warn_at,
        action=row.action,
    )


class UsageMeter:
    """Implements the resolver's CallMeter. One per process (Resources.usage)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        valkey: Redis,
        extensions: ExtensionRegistry,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.sessions = sessions
        self.valkey = valkey
        self.extensions = extensions
        self.clock = clock  # assignable: tests move time across period boundaries
        self.dropped = 0  # events that could not be recorded since the process started
        self._rules: dict[uuid.UUID, tuple[float, list[BudgetRule]]] = {}
        self._departments: dict[tuple[uuid.UUID, uuid.UUID], tuple[float, list[uuid.UUID]]] = {}

    # --- Budgets ------------------------------------------------------------------------------

    def invalidate(self, tenant_id: uuid.UUID) -> None:
        """After budgets changed: the next call reloads them (other processes within
        RULES_TTL_S)."""
        self._rules.pop(tenant_id, None)
        for key in [k for k in self._departments if k[0] == tenant_id]:
            del self._departments[key]

    async def rules(self, tenant_id: uuid.UUID) -> list[BudgetRule]:
        cached = self._rules.get(tenant_id)
        if cached is not None and time.monotonic() - cached[0] < RULES_TTL_S:
            return cached[1]
        async with tenant_transaction(self.sessions, tenant_id) as session:
            rows = (await session.scalars(select(Budget))).all()
        rules = [rule_of(row) for row in rows]
        self._rules[tenant_id] = (time.monotonic(), rules)
        return rules

    def department_provider(self) -> DepartmentBudgets | None:
        """Department budgets, when Enterprise implements them and they are licensed."""
        return department_budgets(self.extensions)

    async def _department_binding(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, provider: DepartmentBudgets
    ) -> list[uuid.UUID]:
        key = (tenant_id, user_id)
        cached = self._departments.get(key)
        if cached is not None and time.monotonic() - cached[0] < BINDING_TTL_S:
            return cached[1]
        async with tenant_transaction(self.sessions, tenant_id) as session:
            ids = await provider.binding(session, user_id)
        self._departments[key] = (time.monotonic(), ids)
        return ids

    async def bindings(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, rules: Sequence[BudgetRule]
    ) -> list[Binding]:
        """The budgets that limit calls made for `user_id` (None: only the tenant's)."""
        found = [Binding(r, None) for r in rules if r.scope == "tenant"]
        if user_id is None:
            return found
        own = [r for r in rules if r.scope == "user" and r.subject_id == user_id]
        found += [Binding(r, user_id) for r in own or [r for r in rules if r.is_default]]
        department_rules = [r for r in rules if r.scope == "department"]
        provider = self.department_provider()
        if department_rules and provider is not None:
            ids = set(await self._department_binding(tenant_id, user_id, provider))
            found += [Binding(r, r.subject_id) for r in department_rules if r.subject_id in ids]
        return found

    # --- Spent so far -------------------------------------------------------------------------

    @staticmethod
    def _key(tenant_id: uuid.UUID, key: SpentKey, start: datetime) -> str:
        scope, subject, period = key
        return f"wm:usage:spent:{tenant_id}:{scope}:{subject or '-'}:{period}:{start:%Y%m%d}"

    async def spent(
        self, tenant_id: uuid.UUID, keys: Iterable[SpentKey], now: datetime
    ) -> dict[SpentKey, float]:
        """USD spent in the current period for each key: from Valkey, else from usage_events."""
        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return {}
        names = [self._key(tenant_id, k, period_window(k[2], now)[0]) for k in wanted]
        try:
            cached = cast(list[bytes | None], await self.valkey.mget(names))  # pyright: ignore[reportUnknownMemberType]
        except Exception:
            log.warning("usage_cache_unavailable", exc_info=True)
            cached = [None] * len(names)
        result: dict[SpentKey, float] = {}
        missing: list[tuple[SpentKey, str]] = []
        for key, name, value in zip(wanted, names, cached, strict=True):
            if value is None:
                missing.append((key, name))
            else:
                result[key] = float(value)
        if missing:
            async with tenant_transaction(self.sessions, tenant_id) as session:
                for key, _ in missing:
                    result[key] = await self._sum(session, key, now)
            try:
                pipe = self.valkey.pipeline(transaction=False)
                for key, name in missing:
                    pipe.set(name, repr(result[key]), ex=SPENT_TTL_S, nx=True)  # pyright: ignore[reportUnknownMemberType]
                await pipe.execute()  # pyright: ignore[reportUnknownMemberType]
            except Exception:
                log.warning("usage_cache_unavailable", exc_info=True)
        return result

    @staticmethod
    async def _sum(session: AsyncSession, key: SpentKey, now: datetime) -> float:
        scope, subject, period = key
        start, end = period_window(period, now)
        query = select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
            UsageEvent.occurred_at >= start, UsageEvent.occurred_at < end
        )
        if scope == "user":
            query = query.where(UsageEvent.user_id == subject)
        elif scope == "department":
            query = query.where(UsageEvent.department_id == subject)
        return float(await session.scalar(query) or 0)

    async def _add(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        department_id: uuid.UUID | None,
        cost: float,
        when: datetime,
    ) -> None:
        if cost <= 0:
            return
        keys: list[SpentKey] = []
        for period in ("day", "month"):
            keys.append(("tenant", None, period))
            if user_id is not None:
                keys.append(("user", user_id, period))
            if department_id is not None:
                keys.append(("department", department_id, period))
        try:
            pipe = self.valkey.pipeline(transaction=False)
            for key in keys:
                name = self._key(tenant_id, key, period_window(key[2], when)[0])
                pipe.eval(_INCR_IF_EXISTS, 1, name, repr(cost))  # pyright: ignore[reportUnknownMemberType]
            await pipe.execute()  # pyright: ignore[reportUnknownMemberType]
        except Exception:
            # The cached sums miss this event until they are refreshed (SPENT_TTL_S).
            log.warning("usage_cache_unavailable", exc_info=True)

    # --- Statuses -----------------------------------------------------------------------------

    async def statuses(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        bindings: Sequence[Binding],
        now: datetime | None = None,
    ) -> list[BudgetStatus]:
        now = now or self.clock()
        spent = await self.spent(tenant_id, [b.key for b in bindings], now)
        names = await self._names(session, bindings)
        return [
            status_of(b, spent[b.key], names.get(b.subject_id) if b.subject_id else None, now)
            for b in bindings
        ]

    async def _names(
        self, session: AsyncSession, bindings: Iterable[Binding]
    ) -> dict[uuid.UUID, NamedRef]:
        users = {b.subject_id for b in bindings if b.rule.scope == "user" and b.subject_id}
        departments = {
            b.subject_id for b in bindings if b.rule.scope == "department" and b.subject_id
        }
        names: dict[uuid.UUID, NamedRef] = {}
        if users:
            rows = await session.execute(
                select(User.id, User.display_name).where(User.id.in_(users))
            )
            names |= {uid: NamedRef(uid, name) for uid, name in rows.tuples()}
        provider = self.department_provider()
        if departments and provider is not None:
            found = await provider.names(session, departments)
            names |= {did: NamedRef(did, name) for did, name in found.items()}
        for subject in users | departments:  # deleted since: still named, never an id alone
            names.setdefault(subject, NamedRef(subject, "(deleted)"))
        return names

    # --- Enforcement --------------------------------------------------------------------------

    async def admit(self, tenant_id: uuid.UUID, attribution: Attribution | None) -> None:
        """Raises BudgetExceededError when a blocking budget that limits this call is used up."""
        if attribution is not None and not attribution.enforce:
            return
        rules = await self.rules(tenant_id)
        if not any(r.action == "block" for r in rules):
            return
        user_id = attribution.user_id if attribution else None
        blocking = [
            b for b in await self.bindings(tenant_id, user_id, rules) if b.rule.action == "block"
        ]
        if not blocking:
            return
        now = self.clock()
        spent = await self.spent(tenant_id, [b.key for b in blocking], now)
        over = [b for b in blocking if spent[b.key] >= b.rule.limit_usd]
        if not over:
            return
        # The one that resets last: model requests resume only then.
        chosen = max(over, key=lambda b: (period_window(b.rule.period, now)[1], b.rule.scope))
        async with tenant_transaction(self.sessions, tenant_id) as session:
            (status,) = await self.statuses(session, tenant_id, [chosen], now)
        raise BudgetExceededError(status)

    async def check(self, ctx: RequestContext) -> None:
        """For an entry point, before it starts work: the same test as each model call."""
        await self.admit(ctx.tenant_id, Attribution(feature=UNATTRIBUTED, user_id=ctx.user_id))

    # --- Recording ----------------------------------------------------------------------------

    async def record(
        self, tenant_id: uuid.UUID, attribution: Attribution | None, call: ModelCall
    ) -> None:
        """One usage event. Never raises: a failure is logged and counted in `dropped`."""
        try:
            user_id, department_id, cost, when = await self._insert(tenant_id, attribution, call)
        except Exception as exc:
            self.dropped += 1
            log.warning(
                "usage_event_dropped",
                tenant_id=str(tenant_id),
                model=call.model,
                error=type(exc).__name__,
                dropped=self.dropped,
            )
            return
        await self._add(tenant_id, user_id, department_id, cost or 0.0, when)
        try:
            await self._warn(tenant_id, user_id)
        except Exception as exc:
            log.warning("budget_warning_failed", tenant_id=str(tenant_id), error=type(exc).__name__)

    async def _insert(
        self, tenant_id: uuid.UUID, attribution: Attribution | None, call: ModelCall
    ) -> tuple[uuid.UUID | None, uuid.UUID | None, float | None, datetime]:
        if attribution is None:
            log.warning("usage_unattributed", model=call.model, method=call.method)
            attribution = Attribution(feature=UNATTRIBUTED)
        cost = call_cost(call)
        user_id = attribution.user_id
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "department_id": primary_department_query(user_id) if user_id else None,
            "agent_key": attribution.agent_key,
            "feature": attribution.feature,
            "model": call.model[:200],
            "model_id": call.model_id[:300] or None,
            "provider": call.provider,
            "local": call.local,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "cost_usd": cost,
            "ref_type": attribution.ref_type,
            "ref_id": attribution.ref_id,
            "latency_ms": call.latency_ms,
            "status": call.status,
        }
        async with tenant_transaction(self.sessions, tenant_id) as session:
            row = (
                await session.execute(
                    insert(UsageEvent)
                    .values(**values)
                    .returning(UsageEvent.department_id, UsageEvent.occurred_at)
                )
            ).one()
        return user_id, row.department_id, cost, row.occurred_at

    # --- Warnings -----------------------------------------------------------------------------

    async def _warn(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
        """Notifies once per budget, subject and period when a budget passes its warning level."""
        rules = await self.rules(tenant_id)
        if not rules:
            return
        bindings = await self.bindings(tenant_id, user_id, rules)
        if not bindings:
            return
        now = self.clock()
        spent = await self.spent(tenant_id, [b.key for b in bindings], now)
        for binding in bindings:
            rule = binding.rule
            if state_of(spent[binding.key], rule.limit_usd, rule.warn_at) != "ok":
                await self._alert(tenant_id, binding, now)

    async def _alert(self, tenant_id: uuid.UUID, binding: Binding, now: datetime) -> None:
        start, end = period_window(binding.rule.period, now)
        subject = binding.subject_id or tenant_id
        flag = f"wm:usage:alerted:{binding.rule.id}:{subject}:{start:%Y%m%d}"
        try:
            first = await self.valkey.set(flag, "1", nx=True, exat=int(end.timestamp()) + 3600)  # pyright: ignore[reportUnknownMemberType]
        except Exception:
            first = True  # the database decides below
        if not first:
            return
        async with tenant_transaction(self.sessions, tenant_id) as session:
            inserted = await session.scalar(
                pg_insert(BudgetAlert)
                .values(
                    tenant_id=tenant_id,
                    budget_id=binding.rule.id,
                    subject_id=subject,
                    period_start=start,
                )
                .on_conflict_do_nothing()
                .returning(BudgetAlert.budget_id)
            )
            if inserted is None:
                return
            (status,) = await self.statuses(session, tenant_id, [binding], now)
            title, body = warning_text(status)
            kind = "budget.exceeded" if status.status == "exceeded" else "budget.warning"
            for recipient in await self._recipients(session, binding):
                await notify(
                    session,
                    tenant_id,
                    recipient,
                    kind=kind,
                    title=title,
                    body=body,
                    link=usage_link(),
                )
            log.info(
                "budget_warning_sent",
                tenant_id=str(tenant_id),
                budget_id=str(binding.rule.id),
                status=status.status,
            )

    async def _recipients(self, session: AsyncSession, binding: Binding) -> list[uuid.UUID]:
        scope = binding.rule.scope
        if scope == "user":
            return [binding.subject_id] if binding.subject_id else []
        if scope == "department":
            provider = self.department_provider()
            if provider is None or binding.subject_id is None:
                return []
            return await provider.recipients(session, binding.subject_id)
        return list(
            (
                await session.scalars(
                    select(User.id).where(User.org_role.in_(ADMIN_ROLES), User.status == "active")
                )
            ).all()
        )


def warning_text(status: BudgetStatus) -> tuple[str, str]:
    """The notification's title and body, in plain words."""
    name = budget_name(status)
    share = round(100 * status.spent_usd / status.limit_usd) if status.limit_usd else 0
    if status.status == "exceeded":
        title = f"{name[0].upper()}{name[1:]} is used up"
    else:
        title = f"{name[0].upper()}{name[1:]} is {share}% used"
    if status.action == "block":
        effect = (
            "Requests that use an AI model are refused until it resets."
            if status.status == "exceeded"
            else "Once it is used up, requests that use an AI model are refused until it resets."
        )
    else:
        effect = "This budget only warns: requests keep working."
    body = (
        f"{usd(status.spent_usd)} of {usd(status.limit_usd)} spent ({share}%). {effect} "
        f"It resets at {reset_words(status.resets_at)}."
    )
    return title, body

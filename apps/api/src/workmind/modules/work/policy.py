"""A tenant's automation settings (Admin > Automation, docs/automation.md): escalation SLAs,
approver rules per tool, and request routing. One row per tenant (AutomationSettings); a tenant
that never saved any gets the defaults below."""

import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.modules.identity.models import SLUG_PATTERN
from workmind.modules.work.approvers import EscalateTo, Rule, RuleError, parse_rule
from workmind.modules.work.models import AutomationSettings

HOURS = 24 * 30  # the longest SLA: 30 days


class EscalationPolicy(BaseModel):
    """When people are reminded and when work goes to someone else. 0 turns a step off.

    Approvals: a reminder `remind_before_hours` before the approval expires, and, after
    `escalate_after_hours` without a decision, a copy for the escalation target (`escalate_to`
    first: the approver's backup approver, then their department's managers, then the org
    admins). Expiry stays the last step.
    Tasks: a reminder `task_remind_before_hours` before the due date; `task_escalate_after_hours`
    after it, the assignee's escalation target is told. A queue task nobody claimed for
    `queue_escalate_after_hours` goes to the department's managers."""

    enabled: bool = True
    remind_before_hours: int = Field(default=12, ge=0, le=HOURS)
    escalate_after_hours: int = Field(default=24, ge=0, le=HOURS)
    escalate_to: EscalateTo = "backup"
    task_remind_before_hours: int = Field(default=24, ge=0, le=HOURS)
    task_escalate_after_hours: int = Field(default=24, ge=0, le=HOURS)
    queue_escalate_after_hours: int = Field(default=48, ge=0, le=HOURS)


class RoutingPolicy(BaseModel):
    """Request routing: rules first, then (if `use_model`) the router model, whose choice
    counts only at `threshold` confidence or more. Everything else goes to triage, which the
    org admins handle, plus the members of `triage_department` if one is set."""

    use_model: bool = True
    threshold: float = Field(default=0.7, ge=0, le=1)
    triage_department: str | None = Field(default=None, pattern=SLUG_PATTERN)


class ApproverRules(BaseModel):
    """Approver rules per tool, for the actions the assistant proposes (research mode). A tool
    without a rule is approved by the person who asked, as before."""

    rules: dict[str, Any] = {}

    @field_validator("rules")
    @classmethod
    def _valid(cls, value: dict[str, Any]) -> dict[str, Any]:
        for tool, raw in value.items():
            if not tool or len(tool) > 120:
                raise ValueError("a tool name is 1 to 120 characters")
            try:
                parse_rule(raw, where=tool)
            except RuleError as exc:
                raise ValueError(str(exc)) from exc
        return value

    def rule_for(self, tool: str) -> Rule | None:
        raw = self.rules.get(tool)
        return parse_rule(raw, where=tool) if raw is not None else None


class AutomationPolicy(BaseModel):
    escalation: EscalationPolicy = EscalationPolicy()
    approver_rules: ApproverRules = ApproverRules()
    routing: RoutingPolicy = RoutingPolicy()


async def settings_row(session: AsyncSession) -> AutomationSettings | None:
    return await session.scalar(select(AutomationSettings))


async def load_policy(session: AsyncSession) -> AutomationPolicy:
    """The tenant's settings (inside its transaction), or the defaults. A stored value that no
    longer validates falls back to the default for that part."""
    row = await settings_row(session)
    if row is None:
        return AutomationPolicy()
    return AutomationPolicy(
        escalation=_or_default(EscalationPolicy, row.escalation),
        approver_rules=_or_default(ApproverRules, {"rules": row.approver_rules}),
        routing=_or_default(RoutingPolicy, row.routing),
    )


def _or_default[T: BaseModel](model: type[T], value: Any) -> T:
    try:
        return model.model_validate(value or {})
    except ValueError:
        return model()


async def ensure_row(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID | None
) -> AutomationSettings:
    row = await session.scalar(select(AutomationSettings).with_for_update())
    if row is None:
        row = AutomationSettings(tenant_id=tenant_id, updated_by=user_id)
        session.add(row)
        await session.flush()
    row.updated_by = user_id
    return row

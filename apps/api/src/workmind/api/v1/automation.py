"""Automation around approvals and tasks (docs/automation.md): personal notification settings
and backup approver, the Request form, and the admin settings for escalation, approver rules,
request routing and Slack."""

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, SecretStr, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import rate_limit, require_scope, resources_of
from workmind.api.v1.admin import AdminContext
from workmind.api.v1.work import TaskOut, task_out
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import SLUG_PATTERN, Department, User
from workmind.modules.work.approvers import Rule, RuleError, resolve_stage
from workmind.modules.work.delivery import (
    CATEGORIES,
    Category,
    Channels,
    Preferences,
    open_slack,
    preferences_of,
    seal_slack,
)
from workmind.modules.work.models import AutomationSettings, RoutingRule, WorkPreference
from workmind.modules.work.policy import (
    ApproverRules,
    EscalationPolicy,
    RoutingPolicy,
    ensure_row,
    load_policy,
    settings_row,
)
from workmind.modules.work.routing import (
    RequestIn,
    categories_of,
    create_routed_task,
    load_routing,
    route,
)
from workmind.modules.work.slack import SlackClient, SlackError, webhook_allowed
from workmind.modules.work.tools import Email

router = APIRouter(tags=["automation"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])
AskContext = Annotated[RequestContext, require_scope("assistant:ask")]

CATEGORY_LABELS: dict[Category, tuple[str, str]] = {
    "approvals": ("Approvals", "Approvals waiting for you, decisions on what you asked for"),
    "assignments": ("Assignments", "Tasks given to you, requests for your queue, due dates"),
    "escalations": ("Escalations", "Approvals, tasks and document reviews that waited too long"),
    "reviews": (
        "Document reviews",
        "Documents you own that are due for review, or that look stale in their source",
    ),
    "sources": ("Sources", "Sources you manage that keep failing to sync, or sync again"),
    "legal_holds": ("Legal holds", "Legal holds placed or released in your organization"),
    "budgets": ("Budget warnings", "Model usage budgets near or over their limit"),
    "mentions": ("Mentions", "Colleagues mentioning you in a task"),
    "other": ("Everything else", "Workflow runs that failed, meeting notes that are ready"),
}


# --- Personal settings -----------------------------------------------------------------------


class CategoryOut(BaseModel):
    key: Category
    label: str
    description: str


class PreferencesOut(BaseModel):
    channels: dict[Category, Channels]
    digest_hour: int
    backup_approver_id: uuid.UUID | None
    categories: list[CategoryOut]
    email_available: bool  # the deployment has an SMTP server
    slack_available: bool  # the tenant's admins connected a Slack bot (direct messages)


class PreferencesIn(BaseModel):
    channels: dict[Category, Channels] = {}
    digest_hour: int = Field(default=8, ge=0, le=23)
    backup_approver_id: uuid.UUID | None = None


async def _preferences_out(
    session: AsyncSession, request: Request, row: WorkPreference | None
) -> PreferencesOut:
    settings = await settings_row(session)
    preference = preferences_of(row)
    return PreferencesOut(
        channels=preference.complete(),
        digest_hour=preference.digest_hour,
        backup_approver_id=row.backup_approver_id if row else None,
        categories=[
            CategoryOut(key=c, label=CATEGORY_LABELS[c][0], description=CATEGORY_LABELS[c][1])
            for c in CATEGORIES
        ],
        email_available=resources_of(request).mailer is not None,
        slack_available=settings is not None and settings.slack_bot_token is not None,
    )


@router.get("/me/work-preferences")
async def get_work_preferences(ctx: AskContext, request: Request) -> PreferencesOut:
    """Where my notifications go besides the app, and my backup approver."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        row = await session.get(WorkPreference, ctx.user_id)
        return await _preferences_out(session, request, row)


@router.put("/me/work-preferences")
async def put_work_preferences(
    ctx: AskContext, request: Request, body: PreferencesIn
) -> PreferencesOut:
    """My backup approver gets my approvals when they wait too long (escalation): an active
    colleague other than me, never a guest. Changing it is audited."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        if body.backup_approver_id is not None:
            backup = await session.get(User, body.backup_approver_id)
            if (
                ctx.org_role == "guest"
                or backup is None
                or backup.id == ctx.user_id
                or backup.status != "active"
                or backup.org_role == "guest"
            ):
                raise ApiError(
                    422,
                    "invalid_backup_approver",
                    "Choose an active colleague other than yourself",
                )
        row = await session.get(WorkPreference, ctx.user_id, with_for_update=True)
        if row is None:
            row = WorkPreference(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
            session.add(row)
        before = row.backup_approver_id
        channels = Preferences(channels=body.channels).complete()
        row.channels = {c: ch.model_dump() for c, ch in channels.items()}
        row.digest_hour = body.digest_hour
        row.backup_approver_id = body.backup_approver_id
        await session.flush()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="me.work_preferences.updated",
            resource_type="user",
            resource_id=str(ctx.user_id),
            details={
                "email": sorted(c for c, ch in channels.items() if ch.email != "off"),
                "slack": sorted(c for c, ch in channels.items() if ch.slack),
                "backup_approver": str(body.backup_approver_id)
                if body.backup_approver_id
                else None,
                "backup_changed": before != body.backup_approver_id,
            },
        )
        return await _preferences_out(session, request, row)


# --- Requests --------------------------------------------------------------------------------


class DepartmentOption(BaseModel):
    key: str
    name: str


class RequestOptions(BaseModel):
    categories: list[str]
    departments: list[DepartmentOption]


class RequestCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    category: str | None = Field(default=None, max_length=80)
    due: date | None = None


class RoutingOut(BaseModel):
    method: str
    department: str | None
    department_name: str | None
    confidence: float | None
    matched: list[str]
    explanation: str


class RequestOut(BaseModel):
    task: TaskOut
    routing: RoutingOut


def _routing_out(raw: dict[str, Any]) -> RoutingOut:
    return RoutingOut(
        method=str(raw.get("method")),
        department=raw.get("department"),
        department_name=raw.get("department_name"),
        confidence=raw.get("confidence"),
        matched=list(raw.get("matched") or []),
        explanation=str(raw.get("explanation") or ""),
    )


def _members_only(ctx: RequestContext) -> None:
    if ctx.org_role == "guest":
        raise ApiError(403, "forbidden", "Guests cannot send requests to department queues")


@router.get("/requests/options")
async def request_options(ctx: AskContext, request: Request) -> RequestOptions:
    """What the Request form offers: the categories admins set up, and the departments."""
    _members_only(ctx)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        departments, rules, _policy = await load_routing(session)
    return RequestOptions(
        categories=categories_of(rules),
        departments=[DepartmentOption(key=d.key, name=d.name) for d in departments],
    )


async def _create_request(
    request: Request,
    tenant_id: uuid.UUID,
    requester: uuid.UUID,
    body: RequestIn,
    *,
    source: Literal["form", "email"],
    origin: dict[str, Any] | None = None,
) -> RequestOut:
    resources = resources_of(request)
    decision = await route(resources.db, resources.models.for_tenant, tenant_id, requester, body)
    async with tenant_transaction(resources.db, tenant_id) as session:
        task = await create_routed_task(
            session, tenant_id, requester, body, decision, source=source, origin=origin
        )
        return RequestOut(task=task_out(task), routing=_routing_out(task.routing or {}))


@router.post("/requests", status_code=201, dependencies=[rate_limit("chat")])
async def create_request(ctx: AskContext, request: Request, body: RequestCreate) -> RequestOut:
    """A request for whichever team handles it: routed by the admins' rules, then the router
    model, else to triage. The answer says where it went and why."""
    _members_only(ctx)
    return await _create_request(
        request,
        ctx.tenant_id,
        ctx.user_id,
        RequestIn(body.title, body.description, body.category, body.due),
        source="form",
    )


class InboundEmail(BaseModel):
    from_address: Email = Field(alias="from")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)

    model_config = {"populate_by_name": True}


@admin_router.post("/requests/inbound-email", status_code=201, dependencies=[rate_limit("chat")])
async def inbound_email(ctx: AdminContext, request: Request, body: InboundEmail) -> RequestOut:
    """Email-to-request (a stub): a mail bridge, holding an org admin's access token, posts each
    message sent to the request mailbox. The sender must be an active colleague (a member, not
    a guest); the request is theirs, routed like the form's. Nothing reads a mailbox itself."""
    resources = resources_of(request)
    sender = str(body.from_address).lower()
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        person = await session.scalar(select(User).where(User.email == sender))
        requester = (
            person.id
            if person is not None and person.status == "active" and person.org_role != "guest"
            else None
        )
        if requester is None:
            await record_event(
                session,
                tenant_id=ctx.tenant_id,
                actor=Actor(type="user", id=ctx.user_id),
                action="request.email_refused",
                resource_type="email",
                resource_id=sender,
                details={"reason": "unknown_sender"},
            )
    if requester is None:
        raise ApiError(
            422,
            "unknown_sender",
            "The sender is not an active colleague",
            "Only members of the organization can send requests by email.",
        )
    return await _create_request(
        request,
        ctx.tenant_id,
        requester,
        RequestIn(body.subject, body.body),
        source="email",
        origin={"via": "email", "bridge": str(ctx.user_id)},
    )


# --- Admin: escalation, approver rules, routing ------------------------------------------------


class AutomationOut(BaseModel):
    escalation: EscalationPolicy
    approver_rules: dict[str, Any]
    routing: RoutingPolicy


class AutomationIn(BaseModel):
    escalation: EscalationPolicy = EscalationPolicy()
    approver_rules: dict[str, Any] = {}
    routing: RoutingPolicy = RoutingPolicy()


async def _check_rule(session: AsyncSession, tool: str, rule: Rule) -> None:
    """Every person and department a rule names must exist (who counts as "the requester's
    manager" is only known per request)."""
    for index, stage in enumerate(rule):
        try:
            await resolve_stage(session, stage, uuid.uuid4())
        except RuleError as exc:
            raise ApiError(
                422,
                "invalid_approver",
                f"{tool}: the approver rule names someone or something that does not exist",
                f"stage {index + 1}: {exc}",
            ) from exc


@admin_router.get("/automation")
async def get_automation(ctx: AdminContext, request: Request) -> AutomationOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        policy = await load_policy(session)
    return AutomationOut(
        escalation=policy.escalation,
        approver_rules=policy.approver_rules.rules,
        routing=policy.routing,
    )


@admin_router.put("/automation")
async def put_automation(ctx: AdminContext, request: Request, body: AutomationIn) -> AutomationOut:
    """Escalation SLAs, approver rules per tool (for the assistant's proposed actions; workflow
    steps may name their own), and request routing. Audited."""
    try:
        rules = ApproverRules(rules=body.approver_rules)
    except ValidationError as exc:
        raise ApiError(
            422, "invalid_approver", "An approver rule is invalid", str(exc)[:500]
        ) from exc
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        for tool in rules.rules:
            rule = rules.rule_for(tool)
            assert rule is not None  # noqa: S101
            await _check_rule(session, tool, rule)
        triage = body.routing.triage_department
        if (
            triage
            and await session.scalar(select(Department.id).where(Department.slug == triage)) is None
        ):
            raise ApiError(422, "unknown_department", f"No department “{triage}”")
        row = await ensure_row(session, ctx.tenant_id, ctx.user_id)
        row.escalation = body.escalation.model_dump(mode="json")
        row.approver_rules = dict(rules.rules)
        row.routing = body.routing.model_dump(mode="json")
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.automation.saved",
            resource_type="automation",
            resource_id=str(ctx.tenant_id),
            details={
                "escalation": row.escalation,
                "approver_rules": sorted(rules.rules),
                "routing": row.routing,
            },
        )
    return AutomationOut(
        escalation=body.escalation, approver_rules=rules.rules, routing=body.routing
    )


class RoutingRuleIn(BaseModel):
    department: str = Field(pattern=SLUG_PATTERN)
    keywords: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default=[], max_length=100
    )
    categories: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default=[], max_length=20
    )
    enabled: bool = True


class RoutingRulesIn(BaseModel):
    rules: list[RoutingRuleIn] = Field(default=[], max_length=200)


class RoutingRuleOut(BaseModel):
    id: uuid.UUID
    department: str
    department_name: str
    keywords: list[str]
    categories: list[str]
    enabled: bool


async def _rules_out(session: AsyncSession) -> list[RoutingRuleOut]:
    departments = {d.id: d for d in await session.scalars(select(Department))}
    return [
        RoutingRuleOut(
            id=r.id,
            department=departments[r.department_id].slug,
            department_name=departments[r.department_id].name,
            keywords=list(r.keywords),
            categories=list(r.categories),
            enabled=r.enabled,
        )
        for r in await session.scalars(
            select(RoutingRule).order_by(RoutingRule.position, RoutingRule.created_at)
        )
        if r.department_id in departments
    ]


@admin_router.get("/routing-rules")
async def get_routing_rules(ctx: AdminContext, request: Request) -> list[RoutingRuleOut]:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        return await _rules_out(session)


def clean_terms(values: list[str], *, lower: bool) -> list[str]:
    """Stripped, without duplicates (ignoring case). Keywords are stored lower-case;
    categories keep the case the form shows them in."""
    kept: dict[str, str] = {}
    for value in values:
        text = " ".join(value.split())
        if text:
            kept.setdefault(text.lower(), text.lower() if lower else text)
    return list(kept.values())


@admin_router.put("/routing-rules")
async def put_routing_rules(
    ctx: AdminContext, request: Request, body: RoutingRulesIn
) -> list[RoutingRuleOut]:
    """Replaces every routing rule (in this order). A rule needs a keyword or a category."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        departments = {d.slug: d.id for d in await session.scalars(select(Department))}
        for index, rule in enumerate(body.rules):
            if rule.department not in departments:
                raise ApiError(422, "unknown_department", f"No department “{rule.department}”")
            if not clean_terms(rule.keywords, lower=True) and not clean_terms(
                rule.categories, lower=False
            ):
                raise ApiError(422, "empty_rule", f"Rule {index + 1} needs a keyword or a category")
        await session.execute(delete(RoutingRule))
        for position, rule in enumerate(body.rules):
            session.add(
                RoutingRule(
                    tenant_id=ctx.tenant_id,
                    department_id=departments[rule.department],
                    keywords=clean_terms(rule.keywords, lower=True),
                    categories=clean_terms(rule.categories, lower=False),
                    enabled=rule.enabled,
                    position=position,
                )
            )
        await session.flush()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.routing_rules.saved",
            resource_type="automation",
            resource_id=str(ctx.tenant_id),
            details={
                "rules": len(body.rules),
                "departments": sorted({r.department for r in body.rules}),
            },
        )
        return await _rules_out(session)


@admin_router.post("/routing/preview", dependencies=[rate_limit("agent_test")])
async def preview_routing(ctx: AdminContext, request: Request, body: RequestCreate) -> RoutingOut:
    """Where a request like this would go, and why, without creating anything. Its model call
    (when the rules decide nothing) counts for the admin, as "routing"."""
    resources = resources_of(request)
    decision = await route(
        resources.db,
        resources.models.for_tenant,
        ctx.tenant_id,
        ctx.user_id,
        RequestIn(body.title, body.description, body.category),
    )
    return _routing_out(decision.as_json("form"))


# --- Admin: Slack --------------------------------------------------------------------------------


class SlackOut(BaseModel):
    bot_configured: bool
    webhook_configured: bool
    channel_categories: list[Category]
    webhook_prefix: str


class SlackIn(BaseModel):
    """Leave a secret out to keep it, send null to remove it. Secrets are never returned."""

    bot_token: SecretStr | None = None
    webhook_url: SecretStr | None = None
    channel_categories: list[Category] = []


class SlackTest(BaseModel):
    target: Literal["bot", "webhook"]


class SlackTestOut(BaseModel):
    ok: bool
    detail: str


def _slack_out(request: Request, row: AutomationSettings | None) -> SlackOut:
    chosen = list(row.slack_channel_categories) if row is not None else []
    return SlackOut(
        bot_configured=row is not None and row.slack_bot_token is not None,
        webhook_configured=row is not None and row.slack_webhook_url is not None,
        channel_categories=[c for c in CATEGORIES if c in chosen],
        webhook_prefix=resources_of(request).slack_webhook_prefix,
    )


@admin_router.get("/slack")
async def get_slack(ctx: AdminContext, request: Request) -> SlackOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        return _slack_out(request, await settings_row(session))


@admin_router.put("/slack")
async def put_slack(ctx: AdminContext, request: Request, body: SlackIn) -> SlackOut:
    """The bot token (xoxb-…, for direct messages; scopes users:read.email and chat:write) and
    the incoming webhook (one channel) are sealed with the deployment's encryption key."""
    resources = resources_of(request)
    keyring = resources.models.keyring
    changes: dict[str, str] = {}
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        row = await ensure_row(session, ctx.tenant_id, ctx.user_id)
        if "bot_token" in body.model_fields_set:
            token = body.bot_token.get_secret_value().strip() if body.bot_token else ""
            if token and not token.startswith("xoxb-"):
                raise ApiError(422, "invalid_token", "A Slack bot token starts with xoxb-")
            row.slack_bot_token = (
                seal_slack(keyring, ctx.tenant_id, "bot_token", token) if token else None
            )
            changes["bot_token"] = "set" if token else "removed"
        if "webhook_url" in body.model_fields_set:
            url = body.webhook_url.get_secret_value().strip() if body.webhook_url else ""
            if url and not webhook_allowed(url, resources.slack_webhook_prefix):
                raise ApiError(
                    422,
                    "invalid_webhook",
                    "This is not a Slack incoming webhook URL",
                    f"It must start with {resources.slack_webhook_prefix}",
                )
            row.slack_webhook_url = (
                seal_slack(keyring, ctx.tenant_id, "webhook_url", url) if url else None
            )
            changes["webhook_url"] = "set" if url else "removed"
        row.slack_channel_categories = list(dict.fromkeys(body.channel_categories))
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.slack.saved",
            resource_type="automation",
            resource_id=str(ctx.tenant_id),
            details={**changes, "channel_categories": row.slack_channel_categories},
        )
        return _slack_out(request, row)


@admin_router.post("/slack/test", dependencies=[rate_limit("agent_test")])
async def test_slack(ctx: AdminContext, request: Request, body: SlackTest) -> SlackTestOut:
    """Sends a test: a direct message to you through the bot, or a post through the webhook."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        row = await settings_row(session)
        email = await session.scalar(select(User.email).where(User.id == ctx.user_id))
    client = SlackClient(resources.http, resources.slack_api_url)
    text = "A test message from WorkMind: Slack notifications work."
    try:
        if body.target == "bot":
            if row is None or row.slack_bot_token is None:
                raise ApiError(409, "not_configured", "No Slack bot token is saved")
            token = open_slack(
                resources.models.keyring, ctx.tenant_id, "bot_token", row.slack_bot_token
            )
            team = await client.check_token(token)
            await client.direct_message(token, email or "", text)
            return SlackTestOut(ok=True, detail=f"Sent you a direct message in {team}.")
        if row is None or row.slack_webhook_url is None:
            raise ApiError(409, "not_configured", "No Slack webhook is saved")
        url = open_slack(
            resources.models.keyring, ctx.tenant_id, "webhook_url", row.slack_webhook_url
        )
        await client.post_webhook(url, text)
        return SlackTestOut(ok=True, detail="Posted a test message to the channel.")
    except SlackError as exc:
        return SlackTestOut(ok=False, detail=str(exc))

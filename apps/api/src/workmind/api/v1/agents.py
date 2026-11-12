"""Agents as configuration (ARCHITECTURE 12.2, docs/agents.md): administrators add or change
department agents without code, see every earlier version, restore one, and try a definition in
the test console before saving it. An agent's collections only narrow what the asking person
can see, and its tools run as that person."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import require_scope, resources_of
from workmind.api.v1.admin import AdminContext
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.resources import Resources
from workmind.core.tenancy import tenant_transaction
from workmind.modules.agents.console import run_console
from workmind.modules.agents.definitions import (
    add_version,
    apply_fields,
    changed_fields,
    snapshot,
    tool_problem,
)
from workmind.modules.agents.model_choice import MODEL_ID_PATTERN
from workmind.modules.agents.models import (
    DEFAULT_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MAX_SEARCHES,
    MAX_TOOL_CALLS,
    AgentDefinition,
    AgentDefinitionVersion,
)
from workmind.modules.agents.orchestrator import OrchestratorDeps
from workmind.modules.agents.planning import Agent, agent_from_definition, collection_ids
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import SLUG_PATTERN, Tenant, User
from workmind.modules.knowledge.models import Collection
from workmind.modules.model_catalog.catalog import Catalog
from workmind.modules.model_catalog.store import load_config
from workmind.modules.tools.registry import ToolRegistry
from workmind.providers.llm import LLMError, LLMUsage
from workmind.providers.pricing import cost_usd

router = APIRouter(prefix="/admin/agents", tags=["admin"])

NOTE_MAX = 500


class AgentFields(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=10, max_length=1000)
    instructions: str = Field(default="", max_length=2000)
    collections: list[str] = Field(min_length=1, description="collection keys")
    examples: list[str] = Field(default=[], max_length=10)
    tools: list[str] = Field(
        default=[], max_length=10, description="read-level tools it may call besides searching"
    )
    model: str | None = Field(
        default=None,
        pattern=MODEL_ID_PATTERN,
        max_length=240,
        description="a model catalog id (GET /admin/models); null: the default of its role",
    )
    max_searches: int = Field(default=2, ge=MAX_SEARCHES[0], le=MAX_SEARCHES[1])
    max_tool_calls: int = Field(default=0, ge=MAX_TOOL_CALLS[0], le=MAX_TOOL_CALLS[1])
    max_output_tokens: int = Field(
        default=DEFAULT_OUTPUT_TOKENS, ge=MAX_OUTPUT_TOKENS[0], le=MAX_OUTPUT_TOKENS[1]
    )
    enabled: bool = True


class AgentUpdate(AgentFields):
    note: str = Field(default="", max_length=NOTE_MAX, description="what changed, and why")


class AgentCreate(AgentUpdate):
    key: str = Field(pattern=SLUG_PATTERN, max_length=40)


class AgentOut(AgentFields):
    id: uuid.UUID
    key: str
    version: int
    updated_at: datetime


class Items(BaseModel):
    items: list[AgentOut]


def _out(a: AgentDefinition) -> AgentOut:
    return AgentOut(
        id=a.id,
        key=a.key,
        name=a.name,
        description=a.description,
        instructions=a.instructions,
        collections=a.collection_keys,
        examples=a.examples,
        tools=a.tools,
        model=a.model,
        max_searches=a.max_searches,
        max_tool_calls=a.max_tool_calls,
        max_output_tokens=a.max_output_tokens,
        enabled=a.enabled,
        version=a.version,
        updated_at=a.updated_at,
    )


async def _check_collections(session: AsyncSession, keys: list[str]) -> None:
    known = set((await session.scalars(select(Collection.key))).all())
    if unknown := sorted(set(keys) - known):
        raise ApiError(422, "unknown_collection", "Unknown collection", ", ".join(unknown))


def _check_tools(registry: ToolRegistry, names: list[str]) -> None:
    problems = [p for name in dict.fromkeys(names) if (p := tool_problem(registry, name))]
    if problems:
        code = "unknown_tool" if all(c == "unknown_tool" for c, _ in problems) else problems[0][0]
        title = "Unknown tool" if code == "unknown_tool" else "This tool can't be given to an agent"
        raise ApiError(422, code, title, " ".join(message for _, message in problems))


async def _check_model(
    session: AsyncSession, resources: Resources, tenant_id: uuid.UUID, model: str | None
) -> None:
    """The schema checks the id's shape; the tenant's catalog decides whether it can be used
    (an enabled model of an enabled provider, Admin > Models)."""
    if model is None:
        return
    catalog = Catalog(resources.models.system_catalog, await load_config(session, tenant_id))
    if catalog.usable(model):
        return
    if catalog.model(model) is None:
        raise ApiError(
            422,
            "unknown_model",
            "Unknown model",
            f"{model} is not in this organization's model catalog (Admin > Models).",
        )
    raise ApiError(
        422,
        "model_disabled",
        "This model is disabled",
        f"{model} is disabled in Admin > Models, or its provider is. Enable it there, or "
        "choose another model.",
    )


async def _validate(
    session: AsyncSession, resources: Resources, tenant_id: uuid.UUID, body: AgentFields
) -> None:
    await _check_collections(session, body.collections)
    _check_tools(resources.tools, body.tools)
    await _check_model(session, resources, tenant_id, body.model)


def _fields(body: AgentFields) -> dict[str, Any]:
    fields = body.model_dump(exclude={"note", "key"})
    fields["tools"] = list(dict.fromkeys(body.tools))
    fields["examples"] = list(dict.fromkeys(e.strip() for e in body.examples if e.strip()))
    return fields


async def _locked(session: AsyncSession, key: str) -> AgentDefinition:
    agent = await session.scalar(
        select(AgentDefinition).where(AgentDefinition.key == key).with_for_update()
    )
    if agent is None:
        raise ApiError(404, "not_found", "Agent not found")
    return agent


@router.get("")
async def list_agents(ctx: AdminContext, request: Request) -> Items:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        rows = (await session.scalars(select(AgentDefinition).order_by(AgentDefinition.key))).all()
    return Items(items=[_out(a) for a in rows])


@router.post("", status_code=201)
async def create_agent(ctx: AdminContext, request: Request, body: AgentCreate) -> AgentOut:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        await _validate(session, resources, ctx.tenant_id, body)
        if await session.scalar(select(AgentDefinition.id).where(AgentDefinition.key == body.key)):
            raise ApiError(409, "slug_taken", "An agent with this key already exists")
        agent = AgentDefinition(tenant_id=ctx.tenant_id, key=body.key)
        apply_fields(agent, _fields(body))
        session.add(agent)
        await session.flush()
        version = await add_version(session, agent, author_id=ctx.user_id, note=body.note)
        await _audit(session, ctx, "admin.agent.created", agent, version=version.version)
        return _out(agent)


@router.put("/{key}")
async def update_agent(
    ctx: AdminContext, request: Request, key: str, body: AgentUpdate
) -> AgentOut:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        agent = await _locked(session, key)
        await _validate(session, resources, ctx.tenant_id, body)
        before = snapshot(agent)
        apply_fields(agent, _fields(body))
        await session.flush()
        version = await add_version(session, agent, author_id=ctx.user_id, note=body.note)
        await _audit(
            session,
            ctx,
            "admin.agent.updated",
            agent,
            version=version.version,
            changed=changed_fields(before, snapshot(agent)),
        )
        return _out(agent)


@router.delete("/{key}", status_code=204)
async def delete_agent(ctx: AdminContext, request: Request, key: str) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        agent = await _locked(session, key)
        await _audit(session, ctx, "admin.agent.deleted", agent, version=agent.version)
        await session.delete(agent)  # its versions go with it (the audit log keeps the trail)
    return Response(status_code=204)


# --- Versions ---------------------------------------------------------------------------------


class Author(BaseModel):
    id: uuid.UUID
    name: str


class VersionOut(BaseModel):
    version: int
    author: Author | None  # None: seeded from configuration, or the author was deleted
    note: str
    created_at: datetime
    current: bool
    snapshot: dict[str, Any]


class Versions(BaseModel):
    current: int
    items: list[VersionOut]  # newest first


class RestoreBody(BaseModel):
    note: str = Field(default="", max_length=NOTE_MAX)


async def _versions(
    session: AsyncSession, agent: AgentDefinition, number: int | None = None
) -> list[VersionOut]:
    query = (
        select(AgentDefinitionVersion, User)
        .outerjoin(User, User.id == AgentDefinitionVersion.author_id)
        .where(AgentDefinitionVersion.agent_id == agent.id)
        .order_by(AgentDefinitionVersion.version.desc())
    )
    if number is not None:
        query = query.where(AgentDefinitionVersion.version == number)
    # The author is None for seeded versions and deleted users (an outer join).
    rows: list[tuple[AgentDefinitionVersion, User | None]] = [
        (v, u) for v, u in (await session.execute(query)).all()
    ]
    return [
        VersionOut(
            version=v.version,
            author=Author(id=u.id, name=u.display_name or u.email) if u is not None else None,
            note=v.note,
            created_at=v.created_at,
            current=v.version == agent.version,
            snapshot=v.snapshot,
        )
        for v, u in rows
    ]


async def _agent(session: AsyncSession, key: str) -> AgentDefinition:
    agent = await session.scalar(select(AgentDefinition).where(AgentDefinition.key == key))
    if agent is None:
        raise ApiError(404, "not_found", "Agent not found")
    return agent


@router.get("/{key}/versions")
async def list_versions(ctx: AdminContext, request: Request, key: str) -> Versions:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        agent = await _agent(session, key)
        return Versions(current=agent.version, items=await _versions(session, agent))


@router.get("/{key}/versions/{number}")
async def get_version(ctx: AdminContext, request: Request, key: str, number: int) -> VersionOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        found = await _versions(session, await _agent(session, key), number)
    if not found:
        raise ApiError(404, "not_found", "Version not found")
    return found[0]


@router.post("/{key}/versions/{number}/restore")
async def restore_version(
    ctx: AdminContext, request: Request, key: str, number: int, body: RestoreBody | None = None
) -> AgentOut:
    """A new version equal to version `number`: history is never rewritten."""
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        agent = await _locked(session, key)
        old = await session.scalar(
            select(AgentDefinitionVersion).where(
                AgentDefinitionVersion.agent_id == agent.id,
                AgentDefinitionVersion.version == number,
            )
        )
        if old is None:
            raise ApiError(404, "not_found", "Version not found")
        # The old definition must still be valid today (a collection may have been deleted).
        fields = {**snapshot(agent), **old.snapshot}
        try:
            restored = AgentFields.model_validate(fields)
        except ValueError as exc:
            raise ApiError(
                422, "invalid_version", "This version can't be restored", str(exc)[:300]
            ) from exc
        await _validate(session, resources, ctx.tenant_id, restored)
        before = snapshot(agent)
        apply_fields(agent, _fields(restored))
        await session.flush()
        note = (body.note if body else "").strip() or f"Restored version {number}"
        version = await add_version(session, agent, author_id=ctx.user_id, note=note)
        await _audit(
            session,
            ctx,
            "admin.agent.restored",
            agent,
            version=version.version,
            restored_from=number,
            changed=changed_fields(before, snapshot(agent)),
        )
        return _out(agent)


# --- Test console -----------------------------------------------------------------------------


class AgentDraft(AgentFields):
    key: str | None = Field(default=None, pattern=SLUG_PATTERN, max_length=40)


class TestRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    draft: AgentDraft | None = Field(default=None, description="an unsaved definition")
    key: str | None = Field(
        default=None, max_length=40, description="the saved agent the draft replaces, or tests"
    )


class RoutedAgent(BaseModel):
    key: str
    name: str
    goal: str


class RoutingOut(BaseModel):
    mode: str
    agents: list[RoutedAgent]
    routed_by: str
    reason: str | None = None
    tested_agent_picked: bool | None = None  # None: no agent was tested


class TestCitation(BaseModel):
    marker: str
    document_id: str
    title: str
    filename: str
    section: str
    page: int | None
    trust: str


class TestUsage(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None  # None when a model's price is unknown
    models: list[str]


class TestStep(BaseModel):
    kind: str  # "search" or "tool"
    detail: str
    ok: bool = True
    error: str | None = None


class TestAnswer(BaseModel):
    agent: str
    goal: str
    text: str
    citations: list[TestCitation]
    usage: TestUsage
    steps: list[TestStep]
    abstained: bool = False
    withheld: int = 0
    error: str | None = None


class TestOut(BaseModel):
    routing: RoutingOut
    answer: TestAnswer | None


def _usage(usages: list[LLMUsage]) -> TestUsage:
    used = [u for u in usages if u.input_tokens or u.output_tokens]
    costs = [cost_usd(u) for u in used]
    return TestUsage(
        calls=len(used),
        input_tokens=sum(u.input_tokens for u in used),
        output_tokens=sum(u.output_tokens for u in used),
        cost_usd=None if any(c is None for c in costs) else round(sum(c or 0.0 for c in costs), 6),
        models=sorted({u.model for u in used if u.model}),
    )


def _reason(mode: str, routed_by: str, picked: list[str]) -> str:
    if routed_by == "fast_path":
        return f"The question closely matched {picked[0]}'s description and examples."
    if routed_by == "fallback":
        return "No enabled agents: the plain assistant answers."
    if mode == "direct":
        return "The planner found no agent's area in this question."
    return "The planner chose by the agents' descriptions."


@router.post("/test")
async def test_agent(ctx: AdminContext, request: Request, body: TestRequest) -> TestOut:
    """Routing and an answer for one question, with an unsaved draft or a saved agent. Runs as
    the administrator: their permissions, narrowed to the agent's collections."""
    resources = resources_of(request)
    # TODO(usage): check the administrator's budgets here before any model call (budget_exceeded,
    # 429), once budgets are merged (branch p11-usage).
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        ids = await collection_ids(session)
        tested: Agent | None = None
        if body.draft is not None:
            await _validate(session, resources, ctx.tenant_id, body.draft)
            draft = AgentDefinition(
                tenant_id=ctx.tenant_id, key=body.key or body.draft.key or "draft"
            )
            apply_fields(draft, _fields(body.draft))
            tested = agent_from_definition(draft, ids)
        elif body.key is not None:
            tested = agent_from_definition(await _agent(session, body.key), ids)
    llm = await resources.models.for_tenant(ctx.tenant_id)
    deps = OrchestratorDeps(
        resources.db,
        llm,
        llm.local,
        resources.retriever,
        resources.agent_router,
        tools=resources.tools,
    )
    try:
        run = await run_console(
            deps,
            ctx,
            body.question,
            tested,
            organization=tenant.name if tenant else "the company",
            policy=llm.max_external_sensitivity,
        )
    except LLMError as exc:
        raise ApiError(
            502, "model_unavailable", "The language model is unavailable", str(exc)[:200]
        ) from exc
    # TODO(usage): usage metering and budgets are not merged yet (branch p11-usage). Record
    # `run.usage` here, attributed to this administrator and to the tested agent with feature
    # "agent_test", so console runs show in usage analytics but never as conversations.
    plan = run.plan
    routing = RoutingOut(
        mode=plan.mode,
        agents=[
            RoutedAgent(key=s.agent, name=run.names.get(s.agent, s.agent), goal=s.goal)
            for s in plan.subtasks
        ],
        routed_by=plan.routed_by,
        reason=_reason(plan.mode, plan.routed_by, plan.agents),
        tested_agent_picked=run.routed_to_tested if tested is not None else None,
    )
    answer: TestAnswer | None = None
    if run.agent is not None:
        result, composed = run.agent, run.answer
        steps = [TestStep(kind="search", detail=query) for query in result.searches]
        steps += [
            TestStep(kind="tool", detail=call.tool, ok=call.ok, error=call.error)
            for call in result.tool_calls
        ]
        answer = TestAnswer(
            agent=result.agent,
            goal=result.goal,
            text=composed.text if composed else "",
            citations=[
                TestCitation(
                    marker=c.marker,
                    document_id=c.document_id,
                    title=c.title,
                    filename=c.filename,
                    section=c.section,
                    page=c.page,
                    trust=c.trust,
                )
                for c in (composed.citations if composed else [])
            ],
            usage=_usage(run.usage),
            steps=steps,
            abstained=composed.abstained if composed else False,
            withheld=composed.withheld if composed else result.withheld,
            error=result.error,
        )
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="agent.test",
            resource_type="agent",
            resource_id=tested.key if tested else None,
            details={
                "draft": body.draft is not None,
                "mode": plan.mode,
                "agents": plan.agents,
                "routed_by": plan.routed_by,
                "documents": sorted({c.document_id for c in answer.citations}) if answer else [],
                "tokens": answer.usage.input_tokens + answer.usage.output_tokens
                if answer
                else plan.usage.input_tokens + plan.usage.output_tokens,
            },
        )
    return TestOut(routing=routing, answer=answer)


# --- Shared helpers ---------------------------------------------------------------------------


async def _audit(
    session: AsyncSession,
    ctx: RequestContext,
    action: str,
    agent: AgentDefinition,
    **extra: object,
) -> None:
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action=action,
        resource_type="agent",
        resource_id=agent.key,
        details={
            "collections": agent.collection_keys,
            "enabled": agent.enabled,
            "tools": agent.tools,
            "model": agent.model,
            **extra,
        },
    )


class AgentSummary(BaseModel):
    key: str
    name: str
    description: str
    examples: list[str]


members_router = APIRouter(prefix="/agents", tags=["agents"])


@members_router.get("")
async def list_available_agents(
    ctx: Annotated[RequestContext, require_scope("assistant:ask")], request: Request
) -> list[AgentSummary]:
    """The enabled agents, for everyone: what each can help with (no configuration details)."""
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        rows = (
            await session.scalars(
                select(AgentDefinition)
                .where(AgentDefinition.enabled.is_(True))
                .order_by(AgentDefinition.name)
            )
        ).all()
    return [
        AgentSummary(key=a.key, name=a.name, description=a.description, examples=a.examples)
        for a in rows
    ]


class ToolOut(BaseModel):
    name: str
    description: str
    side_effect: str
    scope: str
    source: str
    # Whether a department agent may be given this tool, and if not, why (docs/agents.md).
    for_agents: bool
    not_for_agents_reason: str | None = None


tools_router = APIRouter(prefix="/admin/tools", tags=["admin"])


@tools_router.get("")
async def list_tools(ctx: AdminContext, request: Request) -> list[ToolOut]:
    """Every tool agents can call, with its side-effect level (write and external need approval)
    and whether department agents may have it (read-level only, besides built-in search)."""
    registry = resources_of(request).tools
    out: list[ToolOut] = []
    for t in registry.all():
        problem = tool_problem(registry, t.name)
        out.append(
            ToolOut(
                name=t.name,
                description=t.description,
                side_effect=t.side_effect,
                scope=t.scope,
                source=t.source,
                for_agents=problem is None,
                not_for_agents_reason=problem[1] if problem else None,
            )
        )
    return out

"""Agents as configuration (ARCHITECTURE 12.2): administrators add or change department agents
without code. An agent's collections only narrow what the asking person can see."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import require_scope, resources_of
from workmind.api.v1.admin import AdminContext
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.agents.models import AgentDefinition
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import SLUG_PATTERN
from workmind.modules.knowledge.models import Collection

router = APIRouter(prefix="/admin/agents", tags=["admin"])


class AgentFields(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=10, max_length=1000)
    instructions: str = Field(default="", max_length=2000)
    collections: list[str] = Field(min_length=1, description="collection keys")
    examples: list[str] = Field(default=[], max_length=10)
    max_searches: int = Field(default=2, ge=1, le=5)
    enabled: bool = True


class AgentCreate(AgentFields):
    key: str = Field(pattern=SLUG_PATTERN, max_length=40)


class AgentOut(AgentCreate):
    id: uuid.UUID
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
        max_searches=a.max_searches,
        enabled=a.enabled,
        updated_at=a.updated_at,
    )


async def _check_collections(session: AsyncSession, keys: list[str]) -> None:
    known = set((await session.scalars(select(Collection.key))).all())
    if unknown := sorted(set(keys) - known):
        raise ApiError(422, "unknown_collection", "Unknown collection", ", ".join(unknown))


@router.get("")
async def list_agents(ctx: AdminContext, request: Request) -> Items:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        rows = (await session.scalars(select(AgentDefinition).order_by(AgentDefinition.key))).all()
    return Items(items=[_out(a) for a in rows])


@router.post("", status_code=201)
async def create_agent(ctx: AdminContext, request: Request, body: AgentCreate) -> AgentOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await _check_collections(session, body.collections)
        if await session.scalar(select(AgentDefinition.id).where(AgentDefinition.key == body.key)):
            raise ApiError(409, "slug_taken", "An agent with this key already exists")
        agent = AgentDefinition(tenant_id=ctx.tenant_id, key=body.key)
        _apply(agent, body)
        session.add(agent)
        await session.flush()
        await _audit(session, ctx, "admin.agent.created", agent)
        return _out(agent)


@router.put("/{key}")
async def update_agent(
    ctx: AdminContext, request: Request, key: str, body: AgentFields
) -> AgentOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        agent = await session.scalar(select(AgentDefinition).where(AgentDefinition.key == key))
        if agent is None:
            raise ApiError(404, "not_found", "Agent not found")
        await _check_collections(session, body.collections)
        _apply(agent, body)
        await session.flush()
        await _audit(session, ctx, "admin.agent.updated", agent)
        return _out(agent)


@router.delete("/{key}", status_code=204)
async def delete_agent(ctx: AdminContext, request: Request, key: str) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        agent = await session.scalar(select(AgentDefinition).where(AgentDefinition.key == key))
        if agent is None:
            raise ApiError(404, "not_found", "Agent not found")
        await _audit(session, ctx, "admin.agent.deleted", agent)
        await session.delete(agent)
    return Response(status_code=204)


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


def _apply(agent: AgentDefinition, body: AgentFields) -> None:
    agent.name = body.name
    agent.description = body.description
    agent.instructions = body.instructions
    agent.collection_keys = body.collections
    agent.examples = body.examples
    agent.max_searches = body.max_searches
    agent.enabled = body.enabled


async def _audit(
    session: AsyncSession, ctx: RequestContext, action: str, agent: AgentDefinition
) -> None:
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action=action,
        resource_type="agent",
        resource_id=agent.key,
        details={"collections": agent.collection_keys, "enabled": agent.enabled},
    )


class ToolOut(BaseModel):
    name: str
    description: str
    side_effect: str
    scope: str
    source: str


tools_router = APIRouter(prefix="/admin/tools", tags=["admin"])


@tools_router.get("")
async def list_tools(ctx: AdminContext, request: Request) -> list[ToolOut]:
    """Every tool agents can call, with its side-effect level (write and external need approval)."""
    return [
        ToolOut(
            name=t.name,
            description=t.description,
            side_effect=t.side_effect,
            scope=t.scope,
            source=t.source,
        )
        for t in resources_of(request).tools.all()
    ]

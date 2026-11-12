"""Search and ask (ARCHITECTURE sections 11, 18). Conversations and streaming arrive in Phase 4;
these endpoints serve the evaluation harness, scripts and MCP clients."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from workmind.api.deps import require_scope, resources_of
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.agents.research import research
from workmind.modules.assistant.answer import answer
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import Tenant
from workmind.modules.retrieval.service import Mode, Passage
from workmind.modules.usage.budgets import BudgetExceededError
from workmind.modules.usage.calls import attribute
from workmind.providers.llm import LLMError

router = APIRouter(tags=["assistant"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)
    mode: Mode = "hybrid"  # permissions apply in every mode


class PassageOut(BaseModel):
    marker: str
    document_id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    filename: str
    section: str
    page: int | None
    trust: str
    sensitivity: str
    score: float
    text: str


class SearchOut(BaseModel):
    passages: list[PassageOut]
    candidates: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class CitationOut(BaseModel):
    marker: str
    document_id: uuid.UUID
    title: str
    filename: str
    section: str
    page: int | None
    trust: str


class UsageOut(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int


class AskOut(BaseModel):
    answer: str
    citations: list[CitationOut]
    sources: list[PassageOut]
    abstained: bool
    unknown_markers: list[str]
    withheld: int = Field(description="Restricted sources left out: the model is external")
    usage: UsageOut


def _passage_out(p: Passage) -> PassageOut:
    return PassageOut(
        marker=p.marker,
        document_id=uuid.UUID(p.document_id),
        collection_id=uuid.UUID(p.collection_id),
        title=p.title,
        filename=p.filename,
        section=p.section,
        page=p.page,
        trust=p.trust,
        sensitivity=p.sensitivity,
        score=p.score,
        text=p.text,
    )


@router.post("/search")
async def search(
    ctx: Annotated[RequestContext, require_scope("knowledge:read")],
    request: Request,
    body: SearchRequest,
) -> SearchOut:
    result = await resources_of(request).retriever.search(
        ctx, body.query, limit=body.limit, mode=body.mode
    )
    return SearchOut(
        passages=[_passage_out(p) for p in result.passages], candidates=result.candidates
    )


@router.post("/ask")
async def ask(
    ctx: Annotated[RequestContext, require_scope("assistant:ask")],
    request: Request,
    body: AskRequest,
) -> AskOut:
    resources = resources_of(request)
    await resources.usage.check(ctx)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
    organization = tenant.name if tenant else "the company"
    llm = await resources.models.for_tenant(ctx.tenant_id)
    try:
        with attribute("chat", user_id=ctx.user_id):
            result = await answer(
                llm,
                resources.retriever,
                ctx,
                body.question,
                organization=organization,
                policy=llm.max_external_sensitivity,
                local_llm=llm.local,
            )
    except BudgetExceededError:
        raise  # 429 budget_exceeded (main.py)
    except LLMError as exc:
        raise ApiError(
            502, "model_unavailable", "The language model is unavailable", str(exc)[:200]
        ) from exc
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="assistant.answered",
            details={
                "documents": sorted({p.document_id for p in result.passages}),
                "cited": sorted({c.document_id for c in result.citations}),
                "abstained": result.abstained,
                "withheld_restricted": result.withheld,
                "local_model": result.local_model,
                "model": result.usage.model,
                "tokens": result.usage.input_tokens + result.usage.output_tokens,
            },
        )
    return AskOut(
        answer=result.text,
        citations=[
            CitationOut(
                marker=c.marker,
                document_id=uuid.UUID(c.document_id),
                title=c.title,
                filename=c.filename,
                section=c.section,
                page=c.page,
                trust=c.trust,
            )
            for c in result.citations
        ],
        sources=[_passage_out(p) for p in result.passages],
        abstained=result.abstained,
        unknown_markers=result.unknown_markers,
        withheld=result.withheld,
        usage=UsageOut(
            model=result.usage.model,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        ),
    )


class ResearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    tools: list[str] | None = Field(default=None, description="limit to these tools")


class ResearchStepOut(BaseModel):
    tool: str
    ok: bool
    error: str | None


class ResearchOut(BaseModel):
    answer: str
    steps: list[ResearchStepOut]
    stopped: str
    usage: UsageOut


@router.post("/research")
async def research_question(
    ctx: Annotated[RequestContext, require_scope("assistant:ask")],
    request: Request,
    body: ResearchRequest,
) -> ResearchOut:
    """The research agent: the model picks tools (company search, configured MCP servers, web
    search if enabled). Tools with side effects stop at the approval gate."""
    resources = resources_of(request)
    await resources.usage.check(ctx)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
    try:
        with attribute("research", user_id=ctx.user_id):
            result = await research(
                await resources.models.for_tenant(ctx.tenant_id),
                resources.tools,
                resources.db,
                ctx,
                body.question,
                organization=tenant.name if tenant else "the company",
                tool_names=body.tools,
            )
    except BudgetExceededError:
        raise  # 429 budget_exceeded (main.py)
    except LLMError as exc:
        raise ApiError(502, "model_unavailable", "The language model is unavailable") from exc
    usage = result.usage[-1] if result.usage else None
    return ResearchOut(
        answer=result.text,
        steps=[ResearchStepOut(tool=s.tool, ok=s.ok, error=s.error) for s in result.steps],
        stopped=result.stopped,
        usage=UsageOut(
            model=usage.model if usage else "",
            input_tokens=sum(u.input_tokens for u in result.usage),
            output_tokens=sum(u.output_tokens for u in result.usage),
        ),
    )

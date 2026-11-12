"""Conversations, messages, runs and their event streams (ARCHITECTURE section 18).

    POST /conversations                       start a conversation
    POST /conversations/{id}/messages         ask -> 202 {run_id}; the answer is produced in
                                              the background
    GET  /runs/{run_id}/events                Server-Sent Events, replayable (Last-Event-ID)
    POST /runs/{run_id}/cancel                stop generating
    POST /messages/{id}/feedback              thumbs up or down, with an optional comment

Conversations are private: anyone else, including admins, gets 404.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import enforce_limit, require_scope, resources_of
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.resources import Resources
from workmind.core.tenancy import tenant_transaction
from workmind.modules.conversations.events import RunMeta
from workmind.modules.conversations.models import Conversation, Message
from workmind.modules.conversations.runner import RunDeps, execute_research_run, execute_run
from workmind.modules.identity.models import Tenant
from workmind.modules.usage.calls import Feature, attribute

router = APIRouter(tags=["conversations"])
AskContext = Annotated[RequestContext, require_scope("assistant:ask")]
TITLE_LENGTH = 80


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    last_message_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    status: str
    created_at: datetime
    run_id: uuid.UUID | None
    search_query: str | None
    citations: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    # Research answers: each tool call, and the approval it proposed (if any), in order.
    tool_steps: list[dict[str, Any]] = []
    usage: dict[str, Any]
    # Why a failed answer failed (as its run.failed event said), else None.
    error: dict[str, Any] | None = None
    abstained: bool
    withheld: int
    feedback: str | None
    feedback_comment: str | None


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
    active_run_id: uuid.UUID | None = None


class ConversationPage(BaseModel):
    items: list[ConversationOut]
    next_cursor: str | None


class ConversationCreate(BaseModel):
    title: str = Field(default="", max_length=200)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    # "research": the agent may use tools and propose actions (emails, tasks) for approval.
    mode: Literal["answer", "research"] = "answer"


class RunStarted(BaseModel):
    run_id: uuid.UUID
    user_message: MessageOut


class FeedbackIn(BaseModel):
    rating: Literal["up", "down"] | None
    comment: str | None = Field(default=None, max_length=2000)


def _conversation_out(c: Conversation) -> ConversationOut:
    return ConversationOut(
        id=c.id, title=c.title, created_at=c.created_at, last_message_at=c.last_message_at
    )


def _message_out(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        status=m.status,
        created_at=m.created_at,
        run_id=m.run_id,
        search_query=m.search_query,
        citations=m.citations,
        sources=m.sources,
        tool_steps=m.tool_steps,
        usage=m.usage,
        error=m.error,
        abstained=m.abstained,
        withheld=m.withheld,
        feedback=m.feedback,
        feedback_comment=m.feedback_comment,
    )


async def _owned(
    session: AsyncSession, ctx: RequestContext, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != ctx.user_id:
        raise ApiError(404, "not_found", "Conversation not found")
    return conversation


@router.post("/conversations", status_code=201)
async def create_conversation(
    ctx: AskContext, request: Request, body: ConversationCreate
) -> ConversationOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        conversation = Conversation(tenant_id=ctx.tenant_id, user_id=ctx.user_id, title=body.title)
        session.add(conversation)
        await session.flush()
        return _conversation_out(conversation)


@router.get("/conversations")
async def list_conversations(
    ctx: AskContext,
    request: Request,
    cursor: Annotated[str | None, Query(description="next_cursor of the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ConversationPage:
    query = (
        select(Conversation)
        .where(Conversation.user_id == ctx.user_id)
        .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        try:
            stamp, last_id = cursor.split("|", 1)
            position = (datetime.fromisoformat(stamp), uuid.UUID(last_id))
        except ValueError as exc:
            raise ApiError(422, "invalid_cursor", "Invalid cursor") from exc
        query = query.where(tuple_(Conversation.last_message_at, Conversation.id) < position)
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        rows = list((await session.scalars(query)).all())
    page, more = rows[:limit], len(rows) > limit
    next_cursor = f"{page[-1].last_message_at.isoformat()}|{page[-1].id}" if more else None
    return ConversationPage(items=[_conversation_out(c) for c in page], next_cursor=next_cursor)


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    ctx: AskContext, request: Request, conversation_id: uuid.UUID
) -> ConversationDetail:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        conversation = await _owned(session, ctx, conversation_id)
        messages = (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at, Message.id)
            )
        ).all()
    active = await resources.runs.events.active_run(ctx.tenant_id, conversation_id)
    return ConversationDetail(
        **_conversation_out(conversation).model_dump(),
        messages=[_message_out(m) for m in messages],
        active_run_id=active,
    )


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    ctx: AskContext, request: Request, conversation_id: uuid.UUID, body: ConversationUpdate
) -> ConversationOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        conversation = await _owned(session, ctx, conversation_id)
        conversation.title = body.title.strip()
        return _conversation_out(conversation)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    ctx: AskContext, request: Request, conversation_id: uuid.UUID
) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await session.delete(await _owned(session, ctx, conversation_id))
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def send_message(
    ctx: AskContext, request: Request, conversation_id: uuid.UUID, body: MessageCreate
) -> RunStarted:
    resources = resources_of(request)
    await enforce_limit(request, ctx, "research" if body.mode == "research" else "chat")
    # A used-up budget refuses the question (429 budget_exceeded) before it is stored.
    await resources.usage.check(ctx)
    run_id = uuid.uuid4()
    meta = RunMeta(run_id, ctx.tenant_id, ctx.user_id, conversation_id)
    # One slot per run in progress, freed when the run ends (429 too_many_requests).
    inflight, slot = resources.runs.inflight, (ctx, str(run_id))
    if not await inflight.acquire(*slot):
        raise inflight.refusal()
    try:
        user_message, tenant = await _store_question(resources, ctx, meta, body)
        llm = await resources.models.for_tenant(ctx.tenant_id)
    except BaseException:
        await inflight.release(*slot)
        raise
    deps = RunDeps(
        sessions=resources.db,
        events=resources.runs.events,
        llm=llm,
        local_llm=llm.local,
        retriever=resources.retriever,
        router=resources.agent_router,
        mode=resources.assistant_mode,
        tools=resources.tools,
    )
    # Every model call of the run counts for this person and the answer it produces (the run's
    # task inherits the attribution). The agents modes plan and use department agents.
    answer_id = uuid.uuid4()
    feature: Feature = (
        "research"
        if body.mode == "research"
        else "agents"
        if resources.assistant_mode in ("agents", "auto")
        else "chat"
    )
    question, organization = user_message.content, tenant.name if tenant else "the company"
    with attribute(feature, user_id=ctx.user_id, ref=("message", answer_id)):
        if body.mode == "research":
            resources.runs.start(
                execute_research_run(
                    deps,
                    ctx,
                    meta,
                    question=question,
                    user_message_id=user_message.id,
                    organization=organization,
                    answer_id=answer_id,
                ),
                slot=slot,
            )
            return RunStarted(run_id=run_id, user_message=user_message)
        resources.runs.start(
            execute_run(
                deps,
                ctx,
                meta,
                question=question,
                user_message_id=user_message.id,
                organization=organization,
                policy=llm.max_external_sensitivity,
                answer_id=answer_id,
            ),
            slot=slot,
        )
    return RunStarted(run_id=run_id, user_message=user_message)


async def _store_question(
    resources: Resources, ctx: RequestContext, meta: RunMeta, body: MessageCreate
) -> tuple[MessageOut, Tenant | None]:
    """Registers the run (409 if the conversation is still answering) and stores the
    question."""
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        conversation = await _owned(session, ctx, meta.conversation_id)
        if not await resources.runs.events.create(meta):
            raise ApiError(409, "run_in_progress", "This conversation is still answering")
        question = body.content.strip()
        if not conversation.title:
            conversation.title = question[:TITLE_LENGTH] + (
                "…" if len(question) > TITLE_LENGTH else ""
            )
        message = Message(
            tenant_id=ctx.tenant_id,
            conversation_id=meta.conversation_id,
            role="user",
            content=question,
            run_id=meta.run_id,
        )
        session.add(message)
        await session.flush()
        return _message_out(message), await session.get(Tenant, ctx.tenant_id)


async def _run_for(request: Request, ctx: RequestContext, run_id: uuid.UUID) -> RunMeta:
    meta = await resources_of(request).runs.events.meta(ctx.tenant_id, run_id)
    if meta is None or meta.tenant_id != ctx.tenant_id or meta.user_id != ctx.user_id:
        raise ApiError(404, "not_found", "Run not found or expired")
    return meta


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
async def run_events(
    ctx: AskContext,
    request: Request,
    run_id: uuid.UUID,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    meta = await _run_for(request, ctx, run_id)
    events = resources_of(request).runs.events

    async def stream() -> AsyncIterator[str]:
        yield "retry: 2000\n\n"
        async for event in events.read(meta, after=last_event_id or "0"):
            if await request.is_disconnected():
                return
            yield ": keep-alive\n\n" if event is None else event.sse()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(ctx: AskContext, request: Request, run_id: uuid.UUID) -> Response:
    meta = await _run_for(request, ctx, run_id)
    await resources_of(request).runs.events.cancel(meta)
    return Response(status_code=202)


@router.post("/messages/{message_id}/feedback")
async def give_feedback(
    ctx: AskContext, request: Request, message_id: uuid.UUID, body: FeedbackIn
) -> MessageOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        message = await session.get(Message, message_id)
        if message is None or message.role != "assistant":
            raise ApiError(404, "not_found", "Message not found")
        await _owned(session, ctx, message.conversation_id)
        message.feedback = body.rating
        message.feedback_comment = (body.comment or "").strip() or None
        await session.flush()
        return _message_out(message)

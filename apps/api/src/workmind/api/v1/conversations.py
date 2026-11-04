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
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import require_scope, resources_of
from workmind.api.v1.assistant import restricted_policy
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.conversations.events import RunMeta
from workmind.modules.conversations.models import Conversation, Message
from workmind.modules.conversations.runner import RunDeps, execute_run
from workmind.modules.identity.models import Tenant

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
    usage: dict[str, Any]
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
        usage=m.usage,
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
    active = cast(
        bytes | None, await resources.valkey.get(f"wm:conversation:{conversation_id}:run")
    )
    return ConversationDetail(
        **_conversation_out(conversation).model_dump(),
        messages=[_message_out(m) for m in messages],
        active_run_id=uuid.UUID(active.decode()) if active else None,
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
    run_id = uuid.uuid4()
    meta = RunMeta(run_id, ctx.tenant_id, ctx.user_id, conversation_id)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        conversation = await _owned(session, ctx, conversation_id)
        if not await resources.runs.events.create(meta):
            raise ApiError(409, "run_in_progress", "This conversation is still answering")
        question = body.content.strip()
        if not conversation.title:
            conversation.title = question[:TITLE_LENGTH] + (
                "…" if len(question) > TITLE_LENGTH else ""
            )
        message = Message(
            tenant_id=ctx.tenant_id,
            conversation_id=conversation_id,
            role="user",
            content=question,
            run_id=run_id,
        )
        session.add(message)
        await session.flush()
        user_message = _message_out(message)
        tenant = await session.get(Tenant, ctx.tenant_id)
    deps = RunDeps(
        sessions=resources.db,
        events=resources.runs.events,
        llm=resources.llm,
        local_llm=resources.local_llm,
        retriever=resources.retriever,
        router=resources.agent_router,
        mode=resources.assistant_mode,
    )
    resources.runs.start(
        execute_run(
            deps,
            ctx,
            meta,
            question=question,
            user_message_id=message.id,
            organization=tenant.name if tenant else "the company",
            policy=restricted_policy(tenant.settings if tenant else {}),
        )
    )
    return RunStarted(run_id=run_id, user_message=user_message)


async def _run_for(request: Request, ctx: RequestContext, run_id: uuid.UUID) -> RunMeta:
    meta = await resources_of(request).runs.events.meta(run_id)
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
    await _run_for(request, ctx, run_id)
    events = resources_of(request).runs.events

    async def stream() -> AsyncIterator[str]:
        yield "retry: 2000\n\n"
        async for event in events.read(run_id, after=last_event_id or "0"):
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
    await _run_for(request, ctx, run_id)
    await resources_of(request).runs.events.cancel(run_id)
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

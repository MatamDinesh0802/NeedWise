"""Executes one chat run (ARCHITECTURE sections 11, 18):

    follow-up rewritten as a standalone question -> retrieval with both permission locks
    -> routing by sensitivity -> streamed answer -> citations checked -> message stored

Every step is published as an event, so the browser can render it live and replay it later.
"""

import asyncio
import re
import uuid
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.tenancy import tenant_transaction
from workmind.modules.agents.orchestrator import (
    AgentStepEvent,
    OrchestratorDeps,
    OrchestratorEvent,
    PlanEvent,
    orchestrate,
)
from workmind.modules.agents.planning import FastRouter
from workmind.modules.agents.research import ResearchStep, research
from workmind.modules.assistant.answer import (
    Answer,
    RestrictedPolicy,
    SourcesEvent,
    TokenEvent,
    answer_stream,
)
from workmind.modules.conversations.events import RunEvents, RunMeta
from workmind.modules.conversations.models import Conversation, Message
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.retrieval.service import Passage, Retriever
from workmind.modules.tools.registry import ToolRegistry
from workmind.providers.llm import LLMError, LLMMessage, LLMProvider, LLMRequest, LLMUsage
from workmind.providers.pricing import cost_usd

log = structlog.get_logger("workmind.runs")

HISTORY_MESSAGES = 6  # the recent window sent verbatim; older turns live in the summary
SUMMARIZE_AFTER = 12  # unsummarized messages before the summary is refreshed
CANCEL_CHECK_EVERY = 16  # tokens between cancellation checks

REWRITE_SYSTEM = """You turn the latest message of a workplace conversation into a standalone \
search query.

Resolve references using the conversation ("it", "that policy", "the second one", "and for \
contractors?"). Keep names, numbers, product names and identifiers exactly as written. If the \
message is already standalone, return it unchanged. Reply with the query only, on one line."""

SUMMARY_SYSTEM = """Summarize this conversation between an employee and the company assistant \
in at most 120 words. Keep facts, figures, names, decisions and open questions; drop small talk. \
Write in the language of the conversation. Reply with the summary only."""

_MARKERS = re.compile(r"\s?\[\s*S\d+(?:\s*,\s*S\d+)*\s*\]")


def strip_markers(text: str) -> str:
    """Old answers cite sources that are not in the new prompt: drop their markers."""
    return _MARKERS.sub("", text)


def source_ref(passage: Passage, *, with_text: bool = False) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "marker": passage.marker,
        "document_id": passage.document_id,
        "chunk_id": passage.chunk_id,
        "collection_id": passage.collection_id,
        "title": passage.title,
        "filename": passage.filename,
        "section": passage.section,
        "page": passage.page,
        "trust": passage.trust,
        "sensitivity": passage.sensitivity,
    }
    if with_text:  # sent live to the person allowed to read it, never stored with the message
        ref["text"] = passage.text
    return ref


@dataclass
class Usage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0

    def add(self, usage: LLMUsage, *, keep_model: bool = False) -> None:
        if not (usage.input_tokens or usage.output_tokens):
            return  # a step that called no model (e.g. no rewrite needed) costs nothing
        if not keep_model or not self.model:
            self.model = usage.model or self.model
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        cost = cost_usd(usage)
        self.cost_usd = None if cost is None or self.cost_usd is None else self.cost_usd + cost

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": None if self.cost_usd is None else round(self.cost_usd, 6),
        }


@dataclass
class RunDeps:
    sessions: async_sessionmaker[AsyncSession]
    events: RunEvents
    llm: LLMProvider
    local_llm: LLMProvider | None
    retriever: Retriever
    router: FastRouter | None = None
    mode: str = "single"  # "single" or "agents" (ARCHITECTURE 12)
    tools: ToolRegistry | None = None  # research runs (tools, proposed actions)


@dataclass
class History:
    messages: list[LLMMessage] = field(default_factory=list[LLMMessage])
    summary: str = ""


async def load_history(
    sessions: async_sessionmaker[AsyncSession],
    ctx: RequestContext,
    conversation_id: uuid.UUID,
    *,
    before: uuid.UUID,
) -> History:
    async with tenant_transaction(sessions, ctx.tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        rows = (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.id != before)
                .order_by(Message.created_at.desc())
                .limit(HISTORY_MESSAGES * 2)
            )
        ).all()
    # Only finished exchanges: a question whose answer was cancelled or failed would otherwise
    # sit unanswered in the prompt and steer the next answer.
    answered = {m.run_id for m in rows if m.role == "assistant" and m.status == "complete"}
    kept = [m for m in reversed(rows) if m.run_id in answered and m.status == "complete"]
    return History(
        messages=[
            LLMMessage("assistant" if m.role == "assistant" else "user", strip_markers(m.content))
            for m in kept[-HISTORY_MESSAGES:]
        ],
        summary=conversation.summary if conversation else "",
    )


async def rewrite_question(
    llm: LLMProvider, history: Sequence[LLMMessage], question: str
) -> tuple[str, LLMUsage]:
    """A follow-up such as "and for contractors?" cannot be searched on its own."""
    if not history:
        return question, LLMUsage()
    transcript = "\n".join(f"{m.role}: {m.content[:1500]}" for m in history)
    request = LLMRequest(
        system=REWRITE_SYSTEM,
        messages=[
            LLMMessage(
                "user",
                f"<conversation>\n{transcript}\n</conversation>\n\nLatest message: {question}",
            )
        ],
        max_tokens=4000,
    )
    text, usage = await llm.complete("router", request)
    lines = [line.strip().strip('"') for line in text.strip().splitlines() if line.strip()]
    return (lines[0][:1000] if lines else question), usage


def assistant_stream(
    deps: RunDeps,
    ctx: RequestContext,
    question: str,
    *,
    organization: str,
    history: Sequence[LLMMessage],
    summary: str,
    search_query: str,
    policy: RestrictedPolicy,
) -> AsyncGenerator[OrchestratorEvent]:
    if deps.mode in ("agents", "auto"):
        return orchestrate(
            OrchestratorDeps(
                deps.sessions,
                deps.llm,
                deps.local_llm,
                deps.retriever,
                deps.router,
                tools=deps.tools,  # agents configured with read-level tools call them
            ),
            ctx,
            question,
            organization=organization,
            history=history,
            summary=summary,
            search_query=search_query,
            policy=policy,
            agents_only_for_fan_out=deps.mode == "auto",
        )
    return answer_stream(
        deps.llm,
        deps.retriever,
        ctx,
        question,
        organization=organization,
        history=history,
        summary=summary,
        search_query=search_query,
        policy=policy,
        local_llm=deps.local_llm,
    )


async def execute_run(
    deps: RunDeps,
    ctx: RequestContext,
    meta: RunMeta,
    *,
    question: str,
    user_message_id: uuid.UUID,
    organization: str,
    policy: RestrictedPolicy,
) -> None:
    events, run_id = deps.events, meta.run_id
    await events.emit(
        run_id, "run.started", {"run_id": str(run_id), "conversation_id": str(meta.conversation_id)}
    )
    usage = Usage()
    parts: list[str] = []
    final: Answer | None = None
    search_query = question
    status, error = "complete", None
    try:
        history = await load_history(
            deps.sessions, ctx, meta.conversation_id, before=user_message_id
        )
        search_query, rewrite_usage = await rewrite_question(deps.llm, history.messages, question)
        usage.add(rewrite_usage)
        if search_query != question:
            await events.emit(run_id, "query.rewritten", {"query": search_query})
        stream = assistant_stream(
            deps,
            ctx,
            question,
            organization=organization,
            history=history.messages,
            summary=history.summary,
            search_query=search_query,
            policy=policy,
        )
        async for event in stream:
            if isinstance(event, PlanEvent):
                await events.emit(
                    run_id,
                    "plan",
                    {
                        "mode": event.plan.mode,
                        "routed_by": event.plan.routed_by,
                        "subtasks": [
                            {**s.model_dump(), "name": event.names.get(s.agent, s.agent)}
                            for s in event.plan.subtasks
                        ],
                    },
                )
            elif isinstance(event, AgentStepEvent):
                await events.emit(
                    run_id,
                    "agent.step",
                    {
                        "agent": event.agent,
                        "name": event.name or event.agent,
                        "status": event.status,
                        "label": event.label,
                        "findings": event.findings,
                        "sources": event.sources,
                        "seconds": round(event.seconds, 2),
                    },
                )
            elif isinstance(event, SourcesEvent):
                await events.emit(
                    run_id,
                    "retrieval",
                    {
                        "sources": [source_ref(p, with_text=True) for p in event.passages],
                        "withheld": event.withheld,
                    },
                )
            elif isinstance(event, TokenEvent):
                parts.append(event.text)
                await events.emit(run_id, "token", {"text": event.text})
                if len(parts) % CANCEL_CHECK_EVERY == 0 and await events.cancelled(run_id):
                    status = "cancelled"
                    break
            else:
                final = event.answer
        await stream.aclose()
    except asyncio.CancelledError:
        # Server shutdown: end the stream and free the conversation, then stop.
        await events.emit(
            run_id, "run.failed", {"code": "interrupted", "message": "The server restarted."}
        )
        raise
    except LLMError as exc:
        status, error = "failed", ("model_unavailable", "The language model is unavailable.")
        log.warning("run_model_error", run_id=str(run_id), error=str(exc)[:300])
    except Exception:
        status, error = "failed", ("internal", "Something went wrong while answering.")
        log.exception("run_failed", run_id=str(run_id))

    if final is not None:
        usage.add(final.usage)
        for extra in final.extra_usage:
            usage.add(extra, keep_model=True)
        for citation in final.citations:
            await events.emit(
                run_id,
                "citation",
                {
                    "marker": citation.marker,
                    "document_id": citation.document_id,
                    "title": citation.title,
                    "section": citation.section,
                    "page": citation.page,
                    "trust": citation.trust,
                },
            )
        await events.emit(run_id, "verification", {"unknown_markers": final.unknown_markers})

    message_id = await _store(
        deps, ctx, meta, final, "".join(parts), status, search_query, usage.as_dict()
    )
    if status == "complete":
        await events.emit(
            run_id,
            "run.completed",
            {
                "message_id": str(message_id),
                "usage": usage.as_dict(),
                "abstained": final.abstained if final else False,
                "withheld": final.withheld if final else 0,
            },
        )
        await _maybe_summarize(deps, ctx, meta.conversation_id)
    elif status == "cancelled":
        await events.emit(run_id, "run.cancelled", {"message_id": str(message_id)})
    else:
        code, message = error or ("internal", "Something went wrong.")
        await events.emit(
            run_id, "run.failed", {"code": code, "message": message, "message_id": str(message_id)}
        )


async def execute_research_run(
    deps: RunDeps,
    ctx: RequestContext,
    meta: RunMeta,
    *,
    question: str,
    user_message_id: uuid.UUID,
    organization: str,
) -> None:
    """A chat turn answered by the research agent: it may search, call tools, and propose actions
    (an email, a task) that wait for the person's approval as inline cards (`approval.required`)."""
    events, run_id = deps.events, meta.run_id
    await events.emit(
        run_id, "run.started", {"run_id": str(run_id), "conversation_id": str(meta.conversation_id)}
    )
    usage = Usage()
    status, error, text = "complete", None, ""
    approvals: list[str] = []
    steps: list[dict[str, Any]] = []  # stored with the answer: a reload shows them as live

    async def on_step(step: ResearchStep) -> None:
        steps.append(
            {
                "tool": step.tool,
                "ok": step.ok,
                "error": step.error,
                "approval_id": step.approval_id,
                "summary": step.summary,
            }
        )
        await events.emit(
            run_id, "tool.step", {"tool": step.tool, "ok": step.ok, "error": step.error}
        )
        if step.approval_id:
            approvals.append(step.approval_id)
            await events.emit(
                run_id,
                "approval.required",
                {"approval_id": step.approval_id, "tool": step.tool, "summary": step.summary},
            )

    try:
        assert deps.tools is not None  # noqa: S101
        history = await load_history(
            deps.sessions, ctx, meta.conversation_id, before=user_message_id
        )
        standalone, rewrite_usage = await rewrite_question(deps.llm, history.messages, question)
        usage.add(rewrite_usage)
        result = await research(
            deps.llm,
            deps.tools,
            deps.sessions,
            ctx,
            standalone,
            organization=organization,
            origin={"conversation_id": str(meta.conversation_id), "run_id": str(run_id)},
            on_step=on_step,
        )
        for step_usage in result.usage:
            usage.add(step_usage)
        text = result.text
        for start in range(0, len(text), 60):  # the loop isn't token-streamed; replay it in pieces
            await events.emit(run_id, "token", {"text": text[start : start + 60]})
    except LLMError as exc:
        status, error = "failed", ("model_unavailable", "The language model is unavailable.")
        log.warning("research_model_error", run_id=str(run_id), error=str(exc)[:300])
    except Exception:
        status, error = "failed", ("internal", "Something went wrong while working on this.")
        log.exception("research_failed", run_id=str(run_id))
    message_id = await _store(
        deps, ctx, meta, None, text, status, question, usage.as_dict(), tool_steps=steps
    )
    if status == "complete":
        await events.emit(
            run_id,
            "run.completed",
            {"message_id": str(message_id), "usage": usage.as_dict(), "approvals": approvals},
        )
    else:
        code, message = error or ("internal", "Something went wrong.")
        await events.emit(
            run_id, "run.failed", {"code": code, "message": message, "message_id": str(message_id)}
        )


async def _store(
    deps: RunDeps,
    ctx: RequestContext,
    meta: RunMeta,
    final: Answer | None,
    partial: str,
    status: str,
    search_query: str,
    usage: dict[str, Any],
    *,
    tool_steps: list[dict[str, Any]] | None = None,
) -> uuid.UUID:
    content = final.text if final and status == "complete" else partial  # research: the text
    async with tenant_transaction(deps.sessions, ctx.tenant_id) as session:
        message = Message(
            tenant_id=ctx.tenant_id,
            conversation_id=meta.conversation_id,
            role="assistant",
            content=content,
            status=status,
            run_id=meta.run_id,
            search_query=search_query,
            citations=[
                {
                    "marker": c.marker,
                    "document_id": c.document_id,
                    "title": c.title,
                    "filename": c.filename,
                    "section": c.section,
                    "page": c.page,
                    "trust": c.trust,
                }
                for c in (final.citations if final else [])
            ],
            sources=[source_ref(p) for p in (final.passages if final else [])],
            usage=usage,
            abstained=final.abstained if final else False,
            withheld=final.withheld if final else 0,
            tool_steps=tool_steps or [],
        )
        session.add(message)
        conversation = await session.get(Conversation, meta.conversation_id)
        if conversation:
            conversation.last_message_at = datetime.now(UTC)
        await session.flush()
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="assistant.answered",
            resource_type="conversation",
            resource_id=str(meta.conversation_id),
            details={
                "run_id": str(meta.run_id),
                "status": status,
                "documents": sorted({p.document_id for p in final.passages}) if final else [],
                "cited": sorted({c.document_id for c in final.citations}) if final else [],
                "withheld_restricted": final.withheld if final else 0,
                "local_model": final.local_model if final else False,
                "model": usage["model"],
                "tokens": usage["input_tokens"] + usage["output_tokens"],
            },
        )
        return message.id


async def _maybe_summarize(deps: RunDeps, ctx: RequestContext, conversation_id: uuid.UUID) -> None:
    """Folds everything before the recent window into a rolling summary, once enough has built
    up. Best effort: a failure here never affects the answer that was just given."""
    try:
        async with tenant_transaction(deps.sessions, ctx.tenant_id) as session:
            conversation = await session.get(Conversation, conversation_id)
            total = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id, Message.status == "complete")
            )
            if conversation is None or not total:
                return
            upto = total - HISTORY_MESSAGES
            if upto - conversation.summarized_messages < SUMMARIZE_AFTER - HISTORY_MESSAGES:
                return
            older = (
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id, Message.status == "complete")
                    .order_by(Message.created_at)
                    .offset(conversation.summarized_messages)
                    .limit(upto - conversation.summarized_messages)
                )
            ).all()
            previous = conversation.summary
        transcript = "\n".join(f"{m.role}: {strip_markers(m.content)[:2000]}" for m in older)
        text, _ = await deps.llm.complete(
            "router",
            LLMRequest(
                system=SUMMARY_SYSTEM,
                messages=[
                    LLMMessage(
                        "user",
                        f"<summary_so_far>\n{previous}\n</summary_so_far>\n\n"
                        f"<new_messages>\n{transcript}\n</new_messages>",
                    )
                ],
                max_tokens=4000,
            ),
        )
        async with tenant_transaction(deps.sessions, ctx.tenant_id) as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation:
                conversation.summary = text.strip()[:4000]
                conversation.summarized_messages = upto
    except Exception:
        log.warning("summary_failed", conversation_id=str(conversation_id), exc_info=True)

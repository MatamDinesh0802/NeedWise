"""Tools that change things: they never run without an approval (side effects: write/external)."""

from datetime import UTC, date, datetime, time
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.context import RequestContext
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import Department, User
from workmind.modules.tools.registry import Tool, ToolError, ToolResult
from workmind.modules.work.models import Task
from workmind.modules.work.notifications import notify
from workmind.providers.email import EmailError, SmtpMailer

# A plain shape check, deliberately not deliverability: internal and test domains (".test",
# ".internal") are normal inside companies, and the mail server is the final judge.
Email = Annotated[str, StringConstraints(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)]


class SendEmail(BaseModel):
    to: list[Email] = Field(min_length=1, max_length=20)
    cc: list[Email] = Field(default=[], max_length=20)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=50_000)


class CreateTask(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    assignee: Email | None = Field(default=None, description="who should do it")
    department: str | None = Field(default=None, description="department key, for a team queue")
    due: date | None = None


def send_email_tool(mailer: SmtpMailer | None, sessions: async_sessionmaker[AsyncSession]) -> Tool:
    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        assert isinstance(args, SendEmail)  # noqa: S101
        if mailer is None:
            raise ToolError("not_configured", "Email is not configured (SMTP_HOST)")
        try:
            sent = await mailer.send(
                [str(a) for a in args.to], args.subject, args.body, cc=[str(a) for a in args.cc]
            )
        except EmailError as exc:
            raise ToolError("upstream_unavailable", str(exc)) from exc
        async with tenant_transaction(sessions, ctx.tenant_id) as session:
            await record_event(
                session,
                tenant_id=ctx.tenant_id,
                actor=Actor(type="user", id=ctx.user_id),
                action="email.sent",
                resource_type="email",
                resource_id=sent.message_id,
                details={"to": sent.recipients, "subject": args.subject},
            )
        return ToolResult(f"Sent to {', '.join(sent.recipients)}.", {"message_id": sent.message_id})

    def summarize(arguments: dict[str, Any]) -> str:
        to = ", ".join(map(str, arguments.get("to", [])))
        return f"Send an email to {to}: {arguments.get('subject', '')}"

    return Tool(
        name="send_email",
        description="Send an email. Always needs the user's approval; they may edit it first.",
        input_model=SendEmail,
        side_effect="external",
        scope="assistant:ask",
        handler=run,
        summarize=summarize,
    )


def create_task_tool(sessions: async_sessionmaker[AsyncSession]) -> Tool:
    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        assert isinstance(args, CreateTask)  # noqa: S101
        async with tenant_transaction(sessions, ctx.tenant_id) as session:
            assignee = (
                await session.scalar(select(User).where(User.email == str(args.assignee).lower()))
                if args.assignee
                else None
            )
            if args.assignee and assignee is None:
                raise ToolError("invalid_input", f"No colleague with the address {args.assignee}")
            department = (
                await session.scalar(select(Department).where(Department.slug == args.department))
                if args.department
                else None
            )
            if args.department and department is None:
                raise ToolError("invalid_input", f"No department {args.department!r}")
            task = Task(
                tenant_id=ctx.tenant_id,
                title=args.title,
                description=args.description,
                assignee_id=assignee.id if assignee else None,
                department_id=department.id if department else None,
                created_by=ctx.user_id,
                due_at=None,
                origin={"via": "assistant"},
            )
            if args.due:
                task.due_at = datetime.combine(args.due, time(17, 0), UTC)
            session.add(task)
            await session.flush()
            if assignee:
                await notify(
                    session,
                    ctx.tenant_id,
                    assignee.id,
                    kind="task.assigned",
                    title=f"New task: {args.title}",
                    link=f"/tasks/{task.id}",
                )
            await record_event(
                session,
                tenant_id=ctx.tenant_id,
                actor=Actor(type="user", id=ctx.user_id),
                action="task.created",
                resource_type="task",
                resource_id=str(task.id),
                details={
                    "assignee": str(assignee.id) if assignee else None,
                    "department": args.department,
                },
            )
            task_id = task.id
        return ToolResult(f"Created task {args.title!r}.", {"task_id": str(task_id)})

    def summarize(arguments: dict[str, Any]) -> str:
        who = arguments.get("assignee") or arguments.get("department") or "nobody yet"
        return f"Create a task for {who}: {arguments.get('title', '')}"

    return Tool(
        name="create_task",
        description="Create a task for a colleague or a department queue (needs approval).",
        input_model=CreateTask,
        side_effect="write",
        scope="assistant:ask",
        handler=run,
        summarize=summarize,
    )

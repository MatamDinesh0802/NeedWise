"""Notifications by email and Slack (docs/automation.md).

In-app notifications always happen (notify() in notifications.py). Each person chooses, per
category, whether a copy also goes out by email (immediately, or in a daily digest) and as a
Slack direct message; an admin may also post chosen categories to a Slack channel.

    notify()           -> the notification, plus outbox rows (NotificationDelivery) in the
                          same transaction: nothing is sent while the action is in progress
    beat, every minute -> queue_digests(): one digest per person at their hour
                          deliver_due():   send what is due; failures are logged and retried
                                           with backoff, then marked failed

A failure never blocks or undoes the action that caused the notification. Messages are plain
text: the notification's title and body and a link to the page in NeedWise.
"""

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.tenancy import global_transaction, tenant_transaction
from needwise.modules.identity.models import Tenant, User
from needwise.modules.model_catalog.secrets import KeyRing, SecretsError
from needwise.modules.work.models import (
    AutomationSettings,
    Notification,
    NotificationDelivery,
    WorkPreference,
)
from needwise.modules.work.slack import SlackClient, SlackError
from needwise.providers.email import EmailError, SmtpMailer

log = structlog.get_logger("needwise.notifications")

Category = Literal[
    "approvals",
    "assignments",
    "escalations",
    "reviews",
    "sources",
    "legal_holds",
    "budgets",
    "mentions",
    "other",
]
CATEGORIES: Final[tuple[Category, ...]] = (
    "approvals",
    "assignments",
    "escalations",
    "reviews",
    "sources",
    "legal_holds",
    "budgets",
    "mentions",
    "other",
)
EmailMode = Literal["off", "immediate", "digest"]
# Written into a message body where the deployment's address goes: the sender fills it in.
APP_URL: Final = "{{app_url}}"
MAX_ATTEMPTS: Final = 5
LEASE: Final = timedelta(minutes=10)  # a claimed row a crashed sender left is retried after this
KEEP: Final = timedelta(days=30)  # sent and failed rows are pruned after this
BATCH: Final = 50
DIGEST_ITEMS: Final = 50

_KINDS: Final[dict[str, Category]] = {
    "approval.escalated": "escalations",
    "task.escalated": "escalations",
    "document.review_escalated": "escalations",
    "task.assigned": "assignments",
    "task.reminder": "assignments",
    "request.routed": "assignments",
    "request.queued": "assignments",
    "request.triage": "assignments",
    "document.review_due": "reviews",
    "document.owner": "reviews",
    "document.demoted": "reviews",
    "source.stale_documents": "reviews",
    "source.failing": "sources",
    "source.recovered": "sources",
    "legal_hold.placed": "legal_holds",
    "legal_hold.released": "legal_holds",
    "budget.warning": "budgets",
    "budget.exceeded": "budgets",
    "mention": "mentions",
}


def category_of(kind: str) -> Category:
    if kind in _KINDS:
        return _KINDS[kind]
    if kind.startswith("approval."):
        return "approvals"
    return "other"


class Channels(BaseModel):
    """One category's channels. Off by default: a person opts in."""

    email: EmailMode = "off"
    slack: bool = False


class Preferences(BaseModel):
    channels: dict[Category, Channels] = Field(default_factory=dict[Category, Channels])
    digest_hour: int = Field(default=8, ge=0, le=23)

    def for_kind(self, kind: str) -> Channels:
        return self.channels.get(category_of(kind), Channels())

    def complete(self) -> dict[Category, Channels]:
        return {c: self.channels.get(c, Channels()) for c in CATEGORIES}


def preferences_of(row: WorkPreference | None) -> Preferences:
    if row is None:
        return Preferences()
    try:
        return Preferences.model_validate(
            {"channels": row.channels, "digest_hour": row.digest_hour}
        )
    except ValueError:
        return Preferences(digest_hour=row.digest_hour)


# --- Slack secrets ---------------------------------------------------------------------------

SlackSecret = Literal["bot_token", "webhook_url"]


def _context(tenant_id: uuid.UUID, kind: SlackSecret) -> bytes:
    """Binds a sealed value to its tenant and purpose: copied elsewhere, it does not open."""
    return b"needwise/slack/" + tenant_id.bytes + b"/" + kind.encode()


def seal_slack(keyring: KeyRing, tenant_id: uuid.UUID, kind: SlackSecret, value: str) -> bytes:
    return keyring.seal(value, context=_context(tenant_id, kind))


def open_slack(keyring: KeyRing, tenant_id: uuid.UUID, kind: SlackSecret, sealed: bytes) -> str:
    return keyring.open(sealed, context=_context(tenant_id, kind))


@dataclass
class SlackRotation:
    rotated: int = 0  # re-encrypted with the active key
    current: int = 0  # already on it
    failed: int = 0  # cannot be decrypted with any configured key: re-enter them


async def rotate_slack_secrets(
    sessions: async_sessionmaker[AsyncSession], keyring: KeyRing
) -> SlackRotation:
    """`nw secrets rotate`: every tenant's Slack bot token and webhook URL, sealed again with
    the active key (as model provider keys are)."""
    report = SlackRotation()
    async with global_transaction(sessions) as session:
        tenant_ids = list((await session.scalars(select(Tenant.id))).all())
    for tenant_id in tenant_ids:
        async with tenant_transaction(sessions, tenant_id) as session:
            row = await session.scalar(select(AutomationSettings).with_for_update())
            if row is None:
                continue
            for kind in ("bot_token", "webhook_url"):
                sealed: bytes | None = getattr(row, f"slack_{kind}")
                if sealed is None:
                    continue
                if not keyring.needs_rotation(sealed):
                    report.current += 1
                    continue
                try:
                    setattr(
                        row,
                        f"slack_{kind}",
                        keyring.rotate(sealed, context=_context(tenant_id, kind)),
                    )
                except SecretsError as exc:
                    report.failed += 1
                    log.warning(
                        "slack_secret_not_rotated", tenant_id=str(tenant_id), reason=str(exc)
                    )
                    continue
                report.rotated += 1
    return report


# --- Writing the outbox (inside notify's transaction) ----------------------------------------


def message_body(title: str, body: str, link: str | None) -> str:
    """The plain-text email or Slack message for one notification."""
    lines = [title]
    if body.strip():
        lines += ["", body.strip()]
    if link:
        lines += ["", f"Open in NeedWise: {APP_URL}{link}"]
    return "\n".join(lines)


def slack_text(text: str) -> str:
    """Text for Slack, escaped as Slack requires for untrusted text: titles carry what people
    and sources wrote (a request's title, a synced page's <title>), and unescaped `<!channel>`
    or `<https://evil.example|Open in NeedWise>` would ping a channel or disguise a link."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


EMAIL_FOOTER: Final = (
    "\n\n--\nYou get this email because of your notification settings: "
    f"{APP_URL}/settings#notifications"
)


async def _slack_settings(session: AsyncSession) -> AutomationSettings | None:
    """The tenant's Slack settings, read once per transaction."""
    cache: dict[str, Any] = session.info.setdefault("needwise_slack_settings", {})
    if "row" not in cache:
        cache["row"] = await session.scalar(select(AutomationSettings))
    row = cache["row"]
    return row if isinstance(row, AutomationSettings) else None


def channel_key(notification: Notification, thing: str | None = None) -> str:
    """What one Slack channel post stands for: the notification's kind and the thing it is
    about (its own key, else its link, else its title). Kinds whose link is a shared page (legal
    holds, budgets, sources, escalations told without a title) pass the thing, or every later
    one would be taken for the first and never posted."""
    return f"{notification.kind}:{thing or notification.link or notification.title}"[:500]


async def enqueue(
    session: AsyncSession, notification: Notification, *, thing: str | None = None
) -> None:
    """Outbox rows for a new notification, from the person's preferences and the tenant's
    Slack settings. Flushed with the notification, committed with the action."""
    preference = preferences_of(await session.get(WorkPreference, notification.user_id))
    channels = preference.for_kind(notification.kind)
    text = message_body(notification.title, notification.body, notification.link)
    if channels.email == "immediate":
        session.add(
            NotificationDelivery(
                tenant_id=notification.tenant_id,
                user_id=notification.user_id,
                notification_id=notification.id,
                channel="email",
                kind=notification.kind,
                subject=notification.title[:200],
                body=text + EMAIL_FOOTER,
            )
        )
    slack = await _slack_settings(session)
    if channels.slack and slack is not None and slack.slack_bot_token is not None:
        session.add(
            NotificationDelivery(
                tenant_id=notification.tenant_id,
                user_id=notification.user_id,
                notification_id=notification.id,
                channel="slack_dm",
                kind=notification.kind,
                subject=notification.title[:200],
                body=slack_text(text),
            )
        )
    if (
        slack is not None
        and slack.slack_webhook_url is not None
        and category_of(notification.kind) in slack.slack_channel_categories
    ):
        # Once per thing, however many people were told about it.
        await session.execute(
            insert(NotificationDelivery)
            .values(
                tenant_id=notification.tenant_id,
                channel="slack_channel",
                kind=notification.kind,
                subject=notification.title[:200],
                body=slack_text(text),
                dedupe_key=channel_key(notification, thing),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "channel", "dedupe_key"])
        )


# --- Digests ---------------------------------------------------------------------------------


def digest_body(items: Iterable[Notification]) -> str:
    lines = ["What happened in NeedWise since your last digest:", ""]
    for n in items:
        lines.append(f"- {n.title}")
        if n.link:
            lines.append(f"  {APP_URL}{n.link}")
    return "\n".join(lines) + EMAIL_FOOTER


async def queue_digests(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, now: datetime
) -> int:
    """At each person's digest hour (UTC), one email with their unread notifications of the
    categories they chose "digest" for, since their last digest (at most a day back).
    Idempotent: `last_digest_at` is set in the same transaction, so a second run in the same
    hour, or a beat that runs every minute, sends nothing more."""
    queued = 0
    async with tenant_transaction(sessions, tenant_id) as session:
        rows = (
            await session.scalars(
                select(WorkPreference)
                .where(
                    WorkPreference.digest_hour == now.hour,
                    (WorkPreference.last_digest_at.is_(None))
                    | (WorkPreference.last_digest_at < now - timedelta(hours=20)),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            preference = preferences_of(row)
            kinds_wanted = {c for c, ch in preference.complete().items() if ch.email == "digest"}
            if not kinds_wanted:
                continue
            since = max(row.last_digest_at or now - timedelta(days=1), now - timedelta(days=1))
            candidates = (
                await session.scalars(
                    select(Notification)
                    .where(
                        Notification.user_id == row.user_id,
                        Notification.read_at.is_(None),
                        Notification.created_at > since,
                    )
                    .order_by(Notification.created_at.desc())
                    .limit(500)
                )
            ).all()
            items = [n for n in candidates if category_of(n.kind) in kinds_wanted][:DIGEST_ITEMS]
            row.last_digest_at = now
            if not items:
                continue
            session.add(
                NotificationDelivery(
                    tenant_id=tenant_id,
                    user_id=row.user_id,
                    channel="email",
                    kind="digest",
                    next_attempt_at=now,
                    subject=f"NeedWise: {len(items)} update{'s' if len(items) != 1 else ''}",
                    body=digest_body(items),
                )
            )
            queued += 1
    return queued


# --- Sending ---------------------------------------------------------------------------------


@dataclass
class DeliveryDeps:
    sessions: async_sessionmaker[AsyncSession]
    mailer: SmtpMailer | None
    slack: SlackClient
    keyring: KeyRing | None
    public_url: str


@dataclass
class DeliveryReport:
    sent: int = 0
    retried: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list[str])


class _Permanent(Exception):  # noqa: N818  (a reason not to retry, not an error type)
    pass


def backoff(attempts: int) -> timedelta:
    """1, 5, 25 minutes, then 2 hours."""
    return timedelta(minutes=min(5 ** (attempts - 1), 120))


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _filled(text: str, public_url: str) -> str:
    return _CONTROL.sub("", text.replace(APP_URL, public_url.rstrip("/")))


async def deliver_due(deps: DeliveryDeps, tenant_id: uuid.UUID, now: datetime) -> DeliveryReport:
    """Sends the tenant's due outbox rows. A row is claimed (attempt counted, retried after a
    lease if the sender dies) before anything is sent, so two senders never send it twice."""
    report = DeliveryReport()
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        claimed = list(
            (
                await session.scalars(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.status == "pending",
                        NotificationDelivery.next_attempt_at <= now,
                    )
                    .order_by(NotificationDelivery.next_attempt_at)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for row in claimed:
            row.attempts += 1
            row.next_attempt_at = now + LEASE
        settings = await session.scalar(select(AutomationSettings))
        emails = {
            u.id: (u.email, u.status)
            for u in await session.scalars(
                select(User).where(User.id.in_({r.user_id for r in claimed if r.user_id}))
            )
        }
    for row in claimed:
        try:
            await _send(deps, tenant_id, row, settings, emails)
        except _Permanent as exc:
            await _finish(deps, tenant_id, row.id, status="failed", error=str(exc), now=now)
            report.failed += 1
            report.errors.append(str(exc))
            log.warning("notification_not_delivered", channel=row.channel, reason=str(exc))
        except (EmailError, SlackError, SecretsError) as exc:
            retry = not isinstance(exc, SlackError) or exc.retry
            if isinstance(exc, SecretsError):
                retry = False
            final = not retry or row.attempts >= MAX_ATTEMPTS
            await _finish(
                deps,
                tenant_id,
                row.id,
                status="failed" if final else "pending",
                error=str(exc)[:500],
                now=now,
                retry_at=None if final else now + backoff(row.attempts),
            )
            if final:
                report.failed += 1
            else:
                report.retried += 1
            report.errors.append(str(exc)[:200])
            log.warning(
                "notification_delivery_failed",
                channel=row.channel,
                attempts=row.attempts,
                final=final,
                error=str(exc)[:200],
            )
        else:
            await _finish(deps, tenant_id, row.id, status="sent", error=None, now=now)
            report.sent += 1
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        await session.execute(
            delete(NotificationDelivery).where(
                NotificationDelivery.status.in_(("sent", "failed")),
                NotificationDelivery.created_at < now - KEEP,
            )
        )
    return report


async def _send(
    deps: DeliveryDeps,
    tenant_id: uuid.UUID,
    row: NotificationDelivery,
    settings: AutomationSettings | None,
    people: dict[uuid.UUID, tuple[str, str]],
) -> None:
    body = _filled(row.body, deps.public_url)
    if row.channel == "slack_channel":
        if settings is None or settings.slack_webhook_url is None or deps.keyring is None:
            raise _Permanent("the Slack channel is no longer configured")
        url = open_slack(deps.keyring, tenant_id, "webhook_url", settings.slack_webhook_url)
        await deps.slack.post_webhook(url, body)
        return
    person = people.get(row.user_id) if row.user_id else None
    if person is None or person[1] != "active":
        raise _Permanent("the person is no longer active")
    email = person[0]
    if row.channel == "email":
        if deps.mailer is None:
            raise _Permanent("email is not configured (SMTP_HOST)")
        await deps.mailer.send([email], row.subject, body)
        return
    if settings is None or settings.slack_bot_token is None or deps.keyring is None:
        raise _Permanent("Slack direct messages are no longer configured")
    token = open_slack(deps.keyring, tenant_id, "bot_token", settings.slack_bot_token)
    await deps.slack.direct_message(token, email, body)


async def _finish(
    deps: DeliveryDeps,
    tenant_id: uuid.UUID,
    row_id: uuid.UUID,
    *,
    status: str,
    error: str | None,
    now: datetime,
    retry_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "last_error": error}
    if status == "sent":
        values["sent_at"] = now
    if retry_at is not None:
        values["next_attempt_at"] = retry_at
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        await session.execute(
            update(NotificationDelivery).where(NotificationDelivery.id == row_id).values(**values)
        )

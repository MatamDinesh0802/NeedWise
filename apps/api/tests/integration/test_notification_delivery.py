"""Notifications by email and Slack (docs/automation.md): the outbox is written with the
notification, sent by the beat task with a stub mailer and a stub Slack, retried with backoff,
and never blocks the action. Needs the test database and TEST_VALKEY_URL."""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update

from needwise.core.tenancy import tenant_transaction
from needwise.modules.model_catalog.secrets import KeyRing
from needwise.modules.work.delivery import (
    MAX_ATTEMPTS,
    DeliveryDeps,
    deliver_due,
    queue_digests,
    rotate_slack_secrets,
    seal_slack,
)
from needwise.modules.work.models import (
    AutomationSettings,
    Notification,
    NotificationDelivery,
    WorkPreference,
)
from needwise.modules.work.notifications import notify
from needwise.modules.work.slack import SlackClient
from needwise.providers.email import EmailError, SentEmail
from tests.integration.conftest import Acme, SessionFactory

KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # 32 bytes, tests only
OTHER_KEY = "ZmVkY2JhOTg3NjU0MzIxMGZlZGNiYTk4NzY1NDMyMTA="
TOKEN = "xoxb-test-token"
HOOK = "https://hooks.slack.com/services/T0/B0/secret"


class Mailer:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[list[str], str, str]] = []
        self.fail = fail

    async def send(
        self, to: Sequence[str], subject: str, body: str, *, cc: Sequence[str] = ()
    ) -> SentEmail:
        if self.fail:
            raise EmailError("the mail server refused the message: 421 try later")
        self.sent.append((list(to), subject, body))
        return SentEmail("<id@test>", list(to))


class SlackStub:
    """Answers like Slack's Web API and an incoming webhook, and remembers what it got."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "hooks.slack.com":
            self.posts.append(json.loads(request.content))
            return httpx.Response(200, text="ok")
        method = request.url.path.rsplit("/", 1)[-1]
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        if method == "users.lookupByEmail":
            return httpx.Response(200, json={"ok": True, "user": {"id": "U-OLIVIA"}})
        self.messages.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})


def deps(sessions: SessionFactory, mailer: Any, stub: SlackStub) -> DeliveryDeps:
    http = httpx.AsyncClient(transport=httpx.MockTransport(stub))
    return DeliveryDeps(
        sessions=sessions,
        mailer=mailer,
        slack=SlackClient(http, "https://slack.stub/api"),
        keyring=KeyRing(KEY),
        public_url="https://needwise.acme.test",
    )


async def prefer(sessions: SessionFactory, acme: Acme, who: str, **fields: Any) -> None:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        session.add(WorkPreference(tenant_id=acme.tenant_id, user_id=acme.users[who], **fields))


async def connect_slack(
    sessions: SessionFactory, acme: Acme, *, channel: list[str] | None = None
) -> None:
    ring = KeyRing(KEY)
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        session.add(
            AutomationSettings(
                tenant_id=acme.tenant_id,
                slack_bot_token=seal_slack(ring, acme.tenant_id, "bot_token", TOKEN),
                slack_webhook_url=seal_slack(ring, acme.tenant_id, "webhook_url", HOOK),
                slack_channel_categories=channel or [],
            )
        )


async def tell(
    sessions: SessionFactory, acme: Acme, who: str, kind: str, title: str, link: str | None
) -> None:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        await notify(session, acme.tenant_id, acme.users[who], kind=kind, title=title, link=link)


async def outbox(sessions: SessionFactory, acme: Acme) -> list[NotificationDelivery]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(NotificationDelivery).order_by(NotificationDelivery.created_at)
                )
            ).all()
        )


async def test_in_app_only_by_default(app_sessions: SessionFactory, acme: Acme) -> None:
    await connect_slack(app_sessions, acme)
    await tell(app_sessions, acme, "olivia.grant", "approval.requested", "Approval needed", None)
    assert await outbox(app_sessions, acme) == []


async def test_email_and_slack_copies_follow_each_persons_choice(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    await connect_slack(app_sessions, acme)
    await prefer(
        app_sessions,
        acme,
        "olivia.grant",
        channels={"approvals": {"email": "immediate", "slack": True}, "budgets": {"slack": True}},
    )
    approval = uuid.uuid4()
    await tell(
        app_sessions,
        acme,
        "olivia.grant",
        "approval.requested",
        "Approval needed: Send the weekly report",
        f"/approvals/{approval}",
    )
    await tell(app_sessions, acme, "olivia.grant", "task.assigned", "New task: Audit", "/tasks/1")
    rows = await outbox(app_sessions, acme)
    assert sorted(r.channel for r in rows) == ["email", "slack_dm"]  # not for assignments

    mailer, slack = Mailer(), SlackStub()
    report = await deliver_due(deps(app_sessions, mailer, slack), acme.tenant_id, datetime.now(UTC))

    assert (report.sent, report.failed) == (2, 0)
    ((to, subject, body),) = mailer.sent
    assert to == ["olivia.grant@acme.test"]
    assert subject == "Approval needed: Send the weekly report"
    assert f"Open in NeedWise: https://needwise.acme.test/approvals/{approval}" in body
    assert "https://needwise.acme.test/settings#notifications" in body
    (message,) = slack.messages
    assert message["channel"] == "U-OLIVIA"
    assert message["text"].startswith("Approval needed: Send the weekly report")
    assert {r.status for r in await outbox(app_sessions, acme)} == {"sent"}
    # Sent rows are not sent again.
    again = await deliver_due(deps(app_sessions, mailer, slack), acme.tenant_id, datetime.now(UTC))
    assert again.sent == 0
    assert len(mailer.sent) == 1


async def test_failures_are_retried_with_backoff_then_given_up(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    await prefer(app_sessions, acme, "carol", channels={"mentions": {"email": "immediate"}})
    await tell(app_sessions, acme, "carol", "mention", "Erin mentioned you: Q4", "/tasks/1")
    failing = Mailer(fail=True)
    now = datetime.now(UTC)

    first = await deliver_due(deps(app_sessions, failing, SlackStub()), acme.tenant_id, now)
    (row,) = await outbox(app_sessions, acme)
    assert (first.retried, row.status, row.attempts) == (1, "pending", 1)
    assert row.next_attempt_at - now == timedelta(minutes=1)
    assert row.last_error is not None
    assert "421 try later" in row.last_error
    # Not due yet: nothing is tried.
    early = await deliver_due(deps(app_sessions, failing, SlackStub()), acme.tenant_id, now)
    assert (early.retried, early.failed) == (0, 0)

    later = now
    for _ in range(MAX_ATTEMPTS - 1):
        later += timedelta(hours=3)
        await deliver_due(deps(app_sessions, failing, SlackStub()), acme.tenant_id, later)
    (row,) = await outbox(app_sessions, acme)
    assert (row.status, row.attempts) == ("failed", MAX_ATTEMPTS)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        assert (await session.scalars(select(Notification))).all()  # the in-app copy is there


async def test_a_wrong_token_is_not_retried_and_an_unconfigured_mailer_fails_once(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    ring = KeyRing(KEY)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        session.add(
            AutomationSettings(
                tenant_id=acme.tenant_id,
                slack_bot_token=seal_slack(ring, acme.tenant_id, "bot_token", "xoxb-revoked"),
            )
        )
    await prefer(
        app_sessions,
        acme,
        "dan",
        channels={"assignments": {"email": "immediate", "slack": True}},
    )
    await tell(app_sessions, acme, "dan", "task.assigned", "New task: Call Bluefin", "/tasks/2")

    report = await deliver_due(
        deps(app_sessions, None, SlackStub()), acme.tenant_id, datetime.now(UTC)
    )

    assert (report.sent, report.failed) == (0, 2)
    rows = {r.channel: r for r in await outbox(app_sessions, acme)}
    assert rows["slack_dm"].last_error == "Slack refused users.lookupByEmail: invalid_auth"
    assert rows["email"].last_error == "email is not configured (SMTP_HOST)"
    assert "xoxb-revoked" not in str([r.last_error for r in rows.values()])


async def test_a_channel_gets_one_post_per_thing_for_the_chosen_categories(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    await connect_slack(app_sessions, acme, channel=["escalations"])
    for who in ("erin", "priya.raman"):
        await tell(app_sessions, acme, who, "approval.escalated", "Escalated: Q4", "/approvals/9")
    await tell(app_sessions, acme, "erin", "approval.requested", "Approval needed", "/approvals/9")

    slack = SlackStub()
    await deliver_due(deps(app_sessions, Mailer(), slack), acme.tenant_id, datetime.now(UTC))

    assert [p["text"] for p in slack.posts] == [
        "Escalated: Q4\n\nOpen in NeedWise: https://needwise.acme.test/approvals/9"
    ]


async def test_things_that_share_a_page_still_get_one_post_each(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    # Every legal hold links to Admin > Legal holds, every budget warning to /analytics: the
    # channel post was keyed on the link, so after the first hold (or warning) the others were
    # dropped for 30 days. And a source that failed again in a new episode was never posted.
    await connect_slack(app_sessions, acme, channel=["legal_holds", "sources"])
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        for matter in ("Smith v. Acme", "Jones v. Acme"):
            hold = uuid.uuid4()
            for who in ("erin", "priya.raman"):  # one fan-out: still one post per hold
                await notify(
                    session,
                    acme.tenant_id,
                    acme.users[who],
                    kind="legal_hold.placed",
                    title=f"Legal hold placed: {matter}",
                    link="/admin/legal-holds",
                    thing=f"legal_hold:{hold}",
                )
        for episode, link in (
            ("e1", "/admin/sources?source=x"),
            ("e1", "/knowledge/sources"),
            ("e2", "/admin/sources?source=x"),
        ):
            await notify(
                session,
                acme.tenant_id,
                acme.users["erin"],
                kind="source.failing",
                title="“Wiki” keeps failing to sync",
                link=link,
                thing=f"source:wiki:{episode}",
            )

    slack = SlackStub()
    await deliver_due(deps(app_sessions, Mailer(), slack), acme.tenant_id, datetime.now(UTC))

    titles = sorted(p["text"].split("\n", 1)[0] for p in slack.posts)
    assert titles == [
        "Legal hold placed: Jones v. Acme",
        "Legal hold placed: Smith v. Acme",
        "“Wiki” keeps failing to sync",
        "“Wiki” keeps failing to sync",
    ]


async def test_slack_gets_titles_escaped_so_they_cannot_ping_or_disguise_links(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    await connect_slack(app_sessions, acme, channel=["assignments"])
    await prefer(app_sessions, acme, "olivia.grant", channels={"assignments": {"slack": True}})
    crafted = "<!channel> Payroll & bonus <https://evil.example|Open in NeedWise>"
    await tell(app_sessions, acme, "olivia.grant", "task.assigned", crafted, "/tasks/1")

    slack = SlackStub()
    await deliver_due(deps(app_sessions, Mailer(), slack), acme.tenant_id, datetime.now(UTC))

    expected = "&lt;!channel&gt; Payroll &amp; bonus &lt;https://evil.example|Open in NeedWise&gt;"
    assert [p["text"].split("\n", 1)[0] for p in slack.posts] == [expected]
    assert [m["text"].split("\n", 1)[0] for m in slack.messages] == [expected]


async def test_a_digest_once_a_day_at_the_persons_hour_with_what_they_have_not_read(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    now = datetime.now(UTC).replace(minute=5)
    await prefer(
        app_sessions,
        acme,
        "hannah.schmidt",
        channels={"approvals": {"email": "digest"}, "reviews": {"email": "digest"}},
        digest_hour=now.hour,
    )
    await prefer(
        app_sessions,
        acme,
        "marcus.bell",
        channels={"approvals": {"email": "digest"}},
        digest_hour=(now.hour + 1) % 24,
    )
    await tell(app_sessions, acme, "hannah.schmidt", "approval.requested", "Approve A", "/a/1")
    await tell(app_sessions, acme, "hannah.schmidt", "document.review_due", "Review B", "/d/2")
    await tell(app_sessions, acme, "hannah.schmidt", "budget.warning", "Budget C", "/analytics")
    await tell(app_sessions, acme, "hannah.schmidt", "approval.requested", "Seen D", "/a/4")
    await tell(app_sessions, acme, "marcus.bell", "approval.requested", "Approve E", "/a/5")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Notification).where(Notification.title == "Seen D").values(read_at=now)
        )

    assert await queue_digests(app_sessions, acme.tenant_id, now) == 1
    assert await queue_digests(app_sessions, acme.tenant_id, now + timedelta(minutes=30)) == 0

    (digest,) = [r for r in await outbox(app_sessions, acme) if r.kind == "digest"]
    assert digest.subject == "NeedWise: 2 updates"
    assert "- Approve A" in digest.body
    assert "- Review B" in digest.body
    assert "Budget C" not in digest.body
    assert "Seen D" not in digest.body
    mailer = Mailer()
    await deliver_due(deps(app_sessions, mailer, SlackStub()), acme.tenant_id, now)
    assert [m[0] for m in mailer.sent] == [["hannah.schmidt@acme.test"]]
    assert "https://needwise.acme.test/a/1" in mailer.sent[0][2]


async def test_slack_secrets_rotate_to_the_new_key(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    await connect_slack(app_sessions, acme)
    # Every tenant in the database is rotated; other tests' secrets may use other keys.
    rotated = await rotate_slack_secrets(app_sessions, KeyRing(OTHER_KEY, [KEY]))
    assert rotated.rotated >= 2
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        row = (await session.scalars(select(AutomationSettings))).one()
    assert row.slack_bot_token is not None
    assert row.slack_webhook_url is not None
    assert not KeyRing(OTHER_KEY).needs_rotation(row.slack_bot_token)
    assert not KeyRing(OTHER_KEY).needs_rotation(row.slack_webhook_url)
    # A sender with only the new key still reaches Slack.
    await prefer(app_sessions, acme, "olivia.grant", channels={"approvals": {"slack": True}})
    await tell(app_sessions, acme, "olivia.grant", "approval.requested", "Approve", "/a/1")
    slack = SlackStub()
    sender = deps(app_sessions, Mailer(), slack)
    sender.keyring = KeyRing(OTHER_KEY)
    report = await deliver_due(sender, acme.tenant_id, datetime.now(UTC))
    assert report.sent == 1
    assert len(slack.messages) == 1

"""Usage reports and budgets over HTTP and against the meter, on a seeded Acme tenant
(PostgreSQL + Valkey): who sees what, budgets that block every model entry point, periods that
reset, warnings sent once, the append-only history, and the backfill.

Spending is made by inserting usage events (what the meter records); the meter used directly
here has its own connections, so it never shares the app's event loop. Needs TEST_VALKEY_URL.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory
from workmind.core.config import Settings
from workmind.core.edition import Edition
from workmind.core.extensions import ExtensionRegistry
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.analytics.models import AnalyticsRun
from workmind.modules.conversations.models import Conversation, Message
from workmind.modules.identity.models import Department
from workmind.modules.knowledge.models import Collection
from workmind.modules.meetings.models import Meeting
from workmind.modules.usage.backfill import backfill_tenant
from workmind.modules.usage.budgets import BudgetExceededError
from workmind.modules.usage.calls import Attribution, ModelCall
from workmind.modules.usage.departments import primary_department_query
from workmind.modules.usage.meter import UsageMeter
from workmind.modules.usage.models import UsageEvent
from workmind.modules.work.models import Notification
from workmind.providers.llm import LLMUsage

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL")


@pytest.fixture
def api(database: Database) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        authz_outbox_worker=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
async def meter(app_sessions: SessionFactory) -> AsyncIterator[UsageMeter]:
    valkey = Redis.from_url(VALKEY_URL or "")  # pyright: ignore[reportUnknownMemberType]
    yield UsageMeter(app_sessions, valkey, ExtensionRegistry(Edition.community()))
    await valkey.aclose()


async def spend(
    sessions: SessionFactory,
    acme: Acme,
    who: str | None,
    cost: float | None,
    *,
    when: datetime | None = None,
    feature: str = "chat",
    model: str = "claude-sonnet-5-5",
    tokens: tuple[int, int] = (1000, 100),
) -> None:
    """A recorded model call, as the meter would store it."""
    user_id = acme.users[who] if who else None
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        session.add(
            UsageEvent(
                tenant_id=acme.tenant_id,
                **({"occurred_at": when} if when else {}),
                user_id=user_id,
                department_id=primary_department_query(user_id) if user_id else None,
                feature=feature,
                model=model,
                provider="anthropic",
                local=False,
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cost_usd=None if cost is None else Decimal(str(cost)),
            )
        )


def put_budgets(
    api: TestClient,
    acme: Acme,
    budgets: list[dict[str, Any]],
    user_default: dict[str, Any] | None = None,
    *,
    who: str = "erin",
) -> Any:
    return api.put(
        "/api/v1/admin/budgets",
        json={"budgets": budgets, "user_default": user_default},
        headers=acme.as_(who, write=True),
    )


def budget(scope: str, limit: float, **fields: Any) -> dict[str, Any]:
    return {
        "scope": scope,
        "subject_id": None,
        "period": "month",
        "limit_usd": limit,
        "warn_at": 0.8,
        "action": "block",
        **fields,
    }


def summary(api: TestClient, acme: Acme, who: str, group_by: str = "user") -> dict[str, Any]:
    today = datetime.now(UTC).date().isoformat()
    response = api.get(
        "/api/v1/usage/summary",
        params={"from": today, "to": today, "group_by": group_by, "interval": "day"},
        headers=acme.as_(who),
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- Administering budgets --------------------------------------------------------------------


async def test_admins_save_budgets_and_read_them_back_with_their_status(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    await spend(app_sessions, acme, "bob", 1.25)
    await spend(app_sessions, acme, "carol", 0.75)
    bob = str(acme.users["bob"])

    saved = put_budgets(
        api,
        acme,
        [budget("tenant", 500), budget("user", 1.5, subject_id=bob, period="day", warn_at=0.5)],
        {"period": "month", "limit_usd": 20, "action": "warn"},
    )

    assert saved.status_code == 200, saved.text
    body = saved.json()
    tenant, cap = body["budgets"]
    assert tenant | {"id": "", "resets_at": ""} == {
        "id": "",
        "scope": "tenant",
        "subject": None,
        "period": "month",
        "limit_usd": 500.0,
        "spent_usd": 2.0,
        "warn_at": 0.8,
        "status": "ok",
        "resets_at": "",
        "action": "block",
    }
    assert tenant["resets_at"].endswith("T00:00:00Z")
    assert (cap["subject"], cap["spent_usd"], cap["status"]) == (
        {"id": bob, "name": "Bob Okafor"},
        1.25,
        "warning",
    )
    assert body["user_default"] == {"period": "month", "limit_usd": 20.0, "action": "warn"}
    assert api.get("/api/v1/admin/budgets", headers=acme.as_("erin")).json() == body

    # Saving again keeps each budget's id (and so its warnings); what is left out is removed.
    again = put_budgets(api, acme, [budget("tenant", 400)]).json()
    assert [(b["id"], b["limit_usd"]) for b in again["budgets"]] == [(tenant["id"], 400.0)]
    assert again["user_default"] is None
    assert api.get("/api/v1/admin/budgets", headers=acme.as_("bob")).status_code == 403
    assert put_budgets(api, acme, [], who="alice").status_code == 403


async def test_invalid_budgets_are_refused_field_by_field(api: TestClient, acme: Acme) -> None:
    bob = str(acme.users["bob"])
    response = put_budgets(
        api,
        acme,
        [
            budget("tenant", 0),
            budget("user", 5),
            budget("user", 5, subject_id=str(uuid.uuid4())),
            budget("user", 5, subject_id=bob),
            budget("user", 6, subject_id=bob),
        ],
    )

    assert response.status_code == 422
    problems = {(tuple(e["loc"][1:]), e["msg"]) for e in response.json()["errors"]}
    assert (("budgets", 0, "limit_usd"), "Input should be greater than 0") in problems
    response = put_budgets(
        api,
        acme,
        [
            budget("user", 5),
            budget("user", 5, subject_id=str(uuid.uuid4())),
            budget("user", 5, subject_id=bob),
            budget("user", 6, subject_id=bob),
            budget("tenant", 5, subject_id=bob),
        ],
    )
    assert response.status_code == 422
    problems = {(tuple(e["loc"][1:]), e["msg"]) for e in response.json()["errors"]}
    assert problems == {
        (("budgets", 0, "subject_id"), "Choose a person."),
        (("budgets", 1, "subject_id"), "No such person in this organization."),
        (("budgets", 3, "period"), "There is already a monthly budget for this person."),
        (("budgets", 4, "subject_id"), "The organization budget has no subject."),
    }
    assert api.get("/api/v1/admin/budgets", headers=acme.as_("erin")).json()["budgets"] == []


async def test_department_budgets_and_chargeback_are_enterprise_features(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        hr = (await session.scalars(select(Department.id).where(Department.slug == "hr"))).one()

    refused = put_budgets(
        api, acme, [budget("tenant", 100), budget("department", 5, subject_id=str(hr))]
    )
    export = api.get(
        "/api/v1/usage/export.csv",
        params={"from": "2026-11-01", "to": "2026-11-30", "group_by": "department"},
        headers=acme.as_("erin"),
    )

    assert (refused.status_code, refused.json()["code"]) == (402, "enterprise_feature")
    assert "Department budgets" in refused.json()["detail"]
    assert api.get("/api/v1/admin/budgets", headers=acme.as_("erin")).json()["budgets"] == []
    assert (export.status_code, export.json()["code"]) == (402, "enterprise_feature")


# --- Enforcement ------------------------------------------------------------------------------


async def test_a_used_up_budget_refuses_every_model_entry_point_with_429(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    await spend(app_sessions, acme, "carol", 1.5)  # before: saving reads the sums into Valkey
    assert put_budgets(api, acme, [budget("tenant", 1.0)]).status_code == 200
    conversation = api.post("/api/v1/conversations", json={}, headers=acme.as_("bob", write=True))
    messages = f"/api/v1/conversations/{conversation.json()['id']}/messages"
    entry_points = [
        ("bob", messages, {"content": "Hi"}),  # chat, and the agents modes
        ("bob", messages, {"content": "Hi", "mode": "research"}),
        ("bob", "/api/v1/ask", {"question": "Parental leave?"}),
        ("bob", "/api/v1/research", {"question": "Parental leave?"}),
        ("carol", "/api/v1/analytics/ask", {"question": "Revenue this year?"}),
        ("erin", "/api/v1/admin/agents/test", {"question": "Parental leave?", "key": "hr"}),
    ]

    for who, url, body in entry_points:
        response = api.post(url, json=body, headers=acme.as_(who, write=True))
        assert response.status_code == 429, (url, response.text)
        problem = response.json()
        assert problem["code"] == "budget_exceeded"
        assert problem["detail"].startswith("The monthly organization budget of $1.00 is used up")
        assert problem["budget"]["scope"] == "tenant"
        assert problem["budget"]["spent_usd"] == 1.5
        assert problem["budget"]["status"] == "exceeded"
        assert "action" not in problem["budget"]
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        assert (await session.scalars(select(Message))).all() == []  # the question was not kept


async def test_a_persons_cap_limits_only_them_and_the_default_everyone_else(
    meter: UsageMeter, api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    bob = str(acme.users["bob"])
    await spend(app_sessions, acme, "bob", 2)  # before: saving reads the sums into Valkey
    await spend(app_sessions, acme, "carol", 2)
    saved = put_budgets(
        api,
        acme,
        [budget("user", 10, subject_id=bob, period="day")],
        {"period": "day", "limit_usd": 1, "action": "block"},
    )
    assert saved.status_code == 200

    await meter.admit(acme.tenant_id, Attribution("chat", user_id=acme.users["bob"]))  # own cap
    with pytest.raises(BudgetExceededError) as carol:
        await meter.admit(acme.tenant_id, Attribution("chat", user_id=acme.users["carol"]))
    await meter.admit(acme.tenant_id, Attribution("chat", user_id=acme.users["dan"]))
    await meter.admit(acme.tenant_id, Attribution("eval", user_id=None))  # no person: no cap

    status = carol.value.status
    assert (status.scope, status.subject and status.subject.name, status.period) == (
        "user",
        "Carol Lindqvist",
        "day",
    )
    assert carol.value.detail.startswith("Your daily cap of $1.00 is used up ($2.00 spent).")


async def test_budgets_reset_at_the_utc_period_boundary(
    meter: UsageMeter, api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    put_budgets(api, acme, [budget("tenant", 1, period="day"), budget("tenant", 3, period="month")])
    await spend(app_sessions, acme, "bob", 2, when=datetime(2026, 11, 30, 23, 0, tzinfo=UTC))
    bob = Attribution("chat", user_id=acme.users["bob"])

    meter.clock = lambda: datetime(2026, 11, 30, 23, 59, tzinfo=UTC)
    with pytest.raises(BudgetExceededError) as late:
        await meter.admit(acme.tenant_id, bob)
    assert (late.value.status.period, late.value.status.resets_at) == (
        "day",
        datetime(2026, 12, 1, tzinfo=UTC),
    )

    meter.clock = lambda: datetime(2026, 12, 1, 0, 1, tzinfo=UTC)
    await meter.admit(acme.tenant_id, bob)  # a new day and a new month

    await spend(app_sessions, acme, "bob", 1.5, when=datetime(2026, 11, 15, tzinfo=UTC))
    # Written straight to the table, so it shows once the cached sums are refreshed.
    cached = [k async for k in meter.valkey.scan_iter(f"wm:usage:spent:{acme.tenant_id}:*")]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    await meter.valkey.delete(*cached)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    meter.clock = lambda: datetime(2026, 11, 29, 12, tzinfo=UTC)
    with pytest.raises(BudgetExceededError) as month:  # $3.50 in November, $0 that day
        await meter.admit(acme.tenant_id, bob)
    assert (month.value.status.period, month.value.status.spent_usd) == ("month", 3.5)


def _call(cost_tokens: int) -> ModelCall:
    """A call of Claude Sonnet 5.5 costing $2 per million input tokens."""
    return ModelCall(
        method="complete",
        role="answer",
        model_id="system:claude-sonnet-5-5",
        model="claude-sonnet-5-5",
        provider="anthropic",
        local=False,
        status="ok",
        latency_ms=12,
        usages=[LLMUsage(model="claude-sonnet-5-5", input_tokens=cost_tokens)],
    )


async def test_warnings_are_sent_once_per_period_to_the_right_people(
    meter: UsageMeter, api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    alice = str(acme.users["alice"])
    put_budgets(
        api,
        acme,
        [
            budget("tenant", 10, warn_at=0.5, action="warn"),
            budget("user", 1, subject_id=alice, period="day"),
        ],
    )
    as_alice = Attribution("chat", user_id=acme.users["alice"])

    await meter.record(acme.tenant_id, as_alice, _call(3_000_000))  # $6: both warn
    await meter.record(acme.tenant_id, as_alice, _call(1_000_000))  # $8: nothing new
    await meter.record(acme.tenant_id, as_alice, _call(2_500_000))  # $13: still nothing new
    await meter.admit(acme.tenant_id, Attribution("chat", user_id=acme.users["bob"]))  # warns only

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        sent = [
            (n.user_id, n.kind, n.title)
            for n in (
                await session.scalars(
                    select(Notification).where(Notification.kind.like("budget.%"))
                )
            ).all()
        ]
        recorded = (await session.scalars(select(UsageEvent))).all()
    assert sorted(sent) == sorted(
        [
            (acme.users["erin"], "budget.warning", "The monthly organization budget is 60% used"),
            (
                acme.users["priya.raman"],
                "budget.warning",
                "The monthly organization budget is 60% used",
            ),
            (acme.users["alice"], "budget.exceeded", "Your daily cap is used up"),
        ]
    )
    assert [float(e.cost_usd or 0) for e in recorded] == [6.0, 2.0, 5.0]
    assert {e.department_id for e in recorded} != {None}  # Alice's department
    assert meter.dropped == 0


async def test_recording_never_fails_the_call(
    meter: UsageMeter, acme: Acme, app_sessions: SessionFactory
) -> None:
    # A feature the database refuses: the event is lost, logged and counted, never raised.
    await meter.record(acme.tenant_id, Attribution("not-a-feature", user_id=None), _call(10))
    assert meter.dropped == 1
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        assert (await session.scalars(select(UsageEvent))).all() == []


# --- The history ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement", ["UPDATE usage_events SET cost_usd = 0", "DELETE FROM usage_events"]
)
async def test_the_runtime_role_cannot_rewrite_usage_history(
    acme: Acme, app_sessions: SessionFactory, statement: str
) -> None:
    await spend(app_sessions, acme, "bob", 1)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_transaction(app_sessions, acme.tenant_id) as session:
            await session.execute(text(statement))


async def test_usage_is_isolated_per_tenant(
    acme: Acme, app_sessions: SessionFactory, owner_sessions: SessionFactory
) -> None:
    from tests.integration.conftest import seed_tenant

    other = await seed_tenant(owner_sessions, "globex")
    await spend(app_sessions, acme, "bob", 1)
    async with tenant_transaction(app_sessions, other.id) as session:
        assert (await session.scalars(select(UsageEvent))).all() == []
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_transaction(app_sessions, other.id) as session:
            session.add(
                UsageEvent(
                    tenant_id=acme.tenant_id,
                    feature="chat",
                    model="m",
                    provider="p",
                    local=False,
                    input_tokens=1,
                    output_tokens=1,
                )
            )


async def test_the_backfill_imports_history_once_and_skips_what_was_recorded_live(
    acme: Acme, app_sessions: SessionFactory
) -> None:
    usage = {"model": "claude-sonnet-5-5", "input_tokens": 1200, "output_tokens": 80}
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        conversation = Conversation(tenant_id=acme.tenant_id, user_id=acme.users["alice"])
        session.add(conversation)
        await session.flush()
        old, live, empty = (
            Message(
                tenant_id=acme.tenant_id,
                conversation_id=conversation.id,
                role="assistant",
                content="An answer.",
                usage={**usage, "cost_usd": 0.0032} if n != 2 else {},
                tool_steps=[{"tool": "search_knowledge"}] if n == 0 else [],
            )
            for n in range(3)
        )
        session.add_all([old, live, empty])
        run = AnalyticsRun(
            tenant_id=acme.tenant_id,
            user_id=acme.users["carol"],
            question="Revenue?",
            status="done",
            answer={"usage": {**usage, "cost_usd": None}},
        )
        collection = (
            await session.scalars(select(Collection).where(Collection.key == "operations-sops"))
        ).one()
        meeting = Meeting(
            tenant_id=acme.tenant_id,
            title="Ops sync",
            collection_id=collection.id,
            owner_id=acme.users["olivia.grant"],
            status="ready",
            filename="ops.vtt",
            media_key="k",
            timings={"extract_s": 1.0, "model": "claude-sonnet-5-5"},
        )
        session.add_all([run, meeting])
        await session.flush()
        session.add(
            UsageEvent(
                tenant_id=acme.tenant_id,
                user_id=acme.users["alice"],
                feature="chat",
                model="claude-sonnet-5-5",
                provider="anthropic",
                local=False,
                input_tokens=600,
                output_tokens=40,
                ref_type="message",
                ref_id=str(live.id),
            )
        )
        ids = {"old": str(old.id), "run": str(run.id), "meeting": str(meeting.id)}

    first = await backfill_tenant(app_sessions, acme.tenant_id)
    second = await backfill_tenant(app_sessions, acme.tenant_id)

    assert (first.messages, first.analytics_runs, first.meetings) == (1, 1, 1)
    assert second.total == 0
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        imported = {
            e.ref_id: e
            for e in (
                await session.scalars(select(UsageEvent).where(UsageEvent.source == "backfill"))
            ).all()
        }
    assert set(imported) == set(ids.values())
    answer = imported[ids["old"]]
    assert (answer.feature, answer.user_id, answer.input_tokens, float(answer.cost_usd or 0)) == (
        "research",
        acme.users["alice"],
        1200,
        0.0032,
    )
    assert imported[ids["run"]].cost_usd is None
    assert (imported[ids["meeting"]].feature, imported[ids["meeting"]].input_tokens) == (
        "meetings",
        0,
    )
    assert imported[ids["meeting"]].cost_usd is None


# --- Who sees what ----------------------------------------------------------------------------


async def test_owners_and_admins_see_the_tenant_managers_their_departments_others_themselves(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    await spend(app_sessions, acme, "alice", 4)  # HR manager
    await spend(app_sessions, acme, "tom.becker", 2, feature="agents")  # HR
    await spend(app_sessions, acme, "bob", 1, feature="research")  # Engineering
    await spend(app_sessions, acme, "mallory", 0.5)  # a guest, in no department
    await spend(app_sessions, acme, None, 3, feature="eval")  # an evaluation: nobody
    await spend(app_sessions, acme, "bob", None, model="llama3", tokens=(10, 2))  # unpriced
    put_budgets(
        api,
        acme,
        [budget("tenant", 100), budget("user", 5, subject_id=str(acme.users["bob"]))],
        {"period": "month", "limit_usd": 2, "action": "block"},
    )

    admin = summary(api, acme, "erin", "department")
    assert admin["scope"] == {"kind": "tenant", "departments": []}
    assert admin["totals"] == {
        "cost_usd": 10.5,
        "input_tokens": 5010,
        "output_tokens": 502,
        "requests": 6,
        "unpriced_requests": 1,
    }
    assert [(g["label"], g["cost_usd"], g["requests"]) for g in admin["groups"]] == [
        ("People & HR", 6.0, 2),
        ("No department", 3.5, 2),
        ("Engineering", 1.0, 2),
    ]
    assert admin["groups"][0]["share"] == pytest.approx(6 / 10.5, abs=1e-6)
    assert [p["requests"] for p in admin["series"]] == [6]
    assert {(b["scope"], b["subject"] and b["subject"]["name"]) for b in admin["budgets"]} == {
        ("tenant", None),
        ("user", "Bob Okafor"),
        ("user", "Erin Takahashi"),  # the default cap, as it applies to Erin
    }
    assert summary(api, acme, "priya.raman")["scope"]["kind"] == "tenant"

    manager = summary(api, acme, "alice")
    assert manager["scope"]["kind"] == "departments"
    assert [d["name"] for d in manager["scope"]["departments"]] == ["People & HR"]
    assert [(g["label"], g["cost_usd"]) for g in manager["groups"]] == [
        ("Alice Moreau", 4.0),
        ("Tom Becker", 2.0),
    ]
    assert {(b["scope"], b["status"]) for b in manager["budgets"]} == {
        ("tenant", "ok"),
        ("user", "exceeded"),  # Alice's default cap of $2: she spent $4
    }

    member = summary(api, acme, "bob", "feature")
    assert member["scope"] == {"kind": "self", "departments": []}
    assert (member["totals"]["cost_usd"], member["totals"]["requests"]) == (1.0, 2)
    assert [g["key"] for g in member["groups"]] == ["research", "chat"]
    assert [(b["scope"], b["subject"] and b["subject"]["name"]) for b in member["budgets"]] == [
        ("tenant", None),
        ("user", "Bob Okafor"),
    ]
    guest = summary(api, acme, "mallory", "user")
    assert [(g["label"], g["cost_usd"]) for g in guest["groups"]] == [("Mallory Quinn", 0.5)]


async def test_admins_list_events_with_filters_and_pages(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    for cost in (1, 2, 3):
        await spend(app_sessions, acme, "bob", cost, feature="research")
    await spend(app_sessions, acme, "alice", 4)
    today = datetime.now(UTC).date()
    params: dict[str, Any] = {"from": today.isoformat(), "to": today.isoformat(), "limit": 2}

    first = api.get(
        "/api/v1/usage/events", params={**params, "feature": "research"}, headers=acme.as_("erin")
    ).json()
    rest = api.get(
        "/api/v1/usage/events",
        params={**params, "feature": "research", "cursor": first["next_cursor"]},
        headers=acme.as_("erin"),
    ).json()
    alice = api.get(
        "/api/v1/usage/events",
        params={**params, "user_id": str(acme.users["alice"])},
        headers=acme.as_("erin"),
    ).json()

    assert [e["cost_usd"] for e in first["items"] + rest["items"]] == [3.0, 2.0, 1.0]
    assert rest["next_cursor"] is None
    item = alice["items"][0]
    assert item["user"] == {"id": str(acme.users["alice"]), "name": "Alice Moreau"}
    assert item["department"]["name"] == "People & HR"
    assert (item["feature"], item["model"], item["local"], item["ref"]) == (
        "chat",
        "claude-sonnet-5-5",
        False,
        None,
    )
    assert api.get("/api/v1/usage/events", params=params, headers=acme.as_("bob")).status_code == (
        403
    )
    bad = api.get(
        "/api/v1/usage/events", params={**params, "cursor": "nope"}, headers=acme.as_("erin")
    )
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_cursor")
    reversed_range = {"from": "2026-11-12", "to": "2026-11-01"}
    reversed_summary = api.get(
        "/api/v1/usage/summary", params=reversed_range, headers=acme.as_("bob")
    )
    assert (reversed_summary.status_code, reversed_summary.json()["code"]) == (422, "invalid_range")

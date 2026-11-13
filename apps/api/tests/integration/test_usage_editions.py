"""The Enterprise half of usage accounting, with the real needwise-ee package loaded through its
entry point: department budgets (saved, shown, enforced, warned about) and the chargeback CSV,
and what happens when the license ends. The Community half (402 for both) is in
test_usage_budgets.py. Needs the ee extra (`uv sync --extra ee`) and TEST_VALKEY_URL.
"""

import csv
import io
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select

from needwise.core.config import Settings
from needwise.core.edition import FEATURES, Edition, License
from needwise.core.extensions import ExtensionRegistry, enable_extensions
from needwise.core.tenancy import tenant_transaction
from needwise.main import create_app
from needwise.modules.governance.models import AuditEvent
from needwise.modules.identity.models import Department, DepartmentMember
from needwise.modules.usage.calls import Attribution, ModelCall
from needwise.modules.usage.departments import primary_department_query
from needwise.modules.usage.extensions import CHARGEBACK_REPORT, DEPARTMENT_BUDGETS
from needwise.modules.usage.meter import UsageMeter
from needwise.modules.usage.models import Budget, UsageEvent
from needwise.modules.work.models import Notification
from needwise.providers.llm import LLMUsage
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory

pytest.importorskip("needwise_ee", reason="install the ee extra: uv sync --extra ee")
pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL")


@dataclass
class Clock:
    now: datetime


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime.now(UTC))


@pytest.fixture
def registry(clock: Clock) -> ExtensionRegistry:
    license_ = License(
        licensee="Acme Robotics",
        features=frozenset(FEATURES),
        issued_at=clock.now - timedelta(days=1),
        expires_at=clock.now + timedelta(days=30),
        key_id="test",
    )
    loaded = enable_extensions(Edition(license_, clock=lambda: clock.now))
    assert loaded.implemented(DEPARTMENT_BUDGETS)
    assert loaded.implemented(CHARGEBACK_REPORT)
    return loaded


@pytest.fixture
def api(database: Database, registry: ExtensionRegistry) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        authz_outbox_worker=False,
    )
    with TestClient(create_app(settings, extensions=registry)) as client:
        yield client


@pytest.fixture
async def meter(
    app_sessions: SessionFactory, registry: ExtensionRegistry
) -> AsyncIterator[UsageMeter]:
    valkey = Redis.from_url(VALKEY_URL or "")  # pyright: ignore[reportUnknownMemberType]
    yield UsageMeter(app_sessions, valkey, registry)
    await valkey.aclose()


async def department(sessions: SessionFactory, acme: Acme, slug: str) -> uuid.UUID:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return (await session.scalars(select(Department.id).where(Department.slug == slug))).one()


def sonnet(input_tokens: int) -> ModelCall:
    """Claude Sonnet 5.5: $2 per million input tokens."""
    return ModelCall(
        method="complete",
        role="answer",
        model_id="system:claude-sonnet-5-5",
        model="claude-sonnet-5-5",
        provider="anthropic",
        local=False,
        status="ok",
        latency_ms=9,
        usages=[LLMUsage(model="claude-sonnet-5-5", input_tokens=input_tokens)],
    )


def put(api: TestClient, acme: Acme, budgets: list[dict[str, Any]]) -> Any:
    return api.put(
        "/api/v1/admin/budgets",
        json={"budgets": budgets, "user_default": None},
        headers=acme.as_("erin", write=True),
    )


def hr_budget(hr: uuid.UUID, limit: float) -> dict[str, Any]:
    return {
        "scope": "department",
        "subject_id": str(hr),
        "period": "month",
        "limit_usd": limit,
        "warn_at": 0.8,
        "action": "block",
    }


def ask(api: TestClient, acme: Acme, who: str) -> Any:
    conversation = api.post("/api/v1/conversations", json={}, headers=acme.as_(who, write=True))
    return api.post(
        f"/api/v1/conversations/{conversation.json()['id']}/messages",
        json={"content": "How long is parental leave?"},
        headers=acme.as_(who, write=True),
    )


def summary_budgets(api: TestClient, acme: Acme, who: str) -> list[dict[str, Any]]:
    today = datetime.now(UTC).date().isoformat()
    response = api.get(
        "/api/v1/usage/summary", params={"from": today, "to": today}, headers=acme.as_(who)
    )
    assert response.status_code == 200, response.text
    return response.json()["budgets"]


async def test_a_department_budget_limits_and_warns_its_department_only(
    api: TestClient, acme: Acme, meter: UsageMeter, app_sessions: SessionFactory
) -> None:
    hr = await department(app_sessions, acme, "hr")
    saved = put(api, acme, [hr_budget(hr, 1.0)])
    assert saved.status_code == 200, saved.text
    (stored,) = saved.json()["budgets"]
    assert (stored["scope"], stored["subject"], stored["spent_usd"]) == (
        "department",
        {"id": str(hr), "name": "People & HR"},
        0.0,
    )

    # Tom is in HR: his $1.20 counts for HR and uses its budget up.
    await meter.record(
        acme.tenant_id, Attribution("chat", user_id=acme.users["tom.becker"]), sonnet(600_000)
    )

    refused = ask(api, acme, "tom.becker")
    assert (refused.status_code, refused.json()["code"]) == (429, "budget_exceeded")
    problem = refused.json()
    assert problem["budget"]["scope"] == "department"
    assert problem["budget"]["subject"]["name"] == "People & HR"
    assert problem["detail"].startswith("The monthly budget for People & HR of $1.00 is used up")
    assert ask(api, acme, "bob").status_code == 202  # Engineering is not limited by it

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        warned = {
            n.user_id: n.title
            for n in (
                await session.scalars(
                    select(Notification).where(Notification.kind == "budget.exceeded")
                )
            ).all()
        }
    assert warned == dict.fromkeys(
        [acme.users["alice"], acme.users["erin"], acme.users["priya.raman"]],
        "The monthly budget for People & HR is used up",
    )  # HR's manager, and the organization's admin and owner
    assert [b["scope"] for b in summary_budgets(api, acme, "tom.becker")] == ["department"]
    assert summary_budgets(api, acme, "bob") == []
    assert [b["scope"] for b in summary_budgets(api, acme, "erin")] == ["department"]


async def test_only_the_department_a_persons_usage_counts_for_limits_them(
    api: TestClient, acme: Acme, meter: UsageMeter, app_sessions: SessionFactory
) -> None:
    """Someone in two departments sees both budgets, and is told which one limits them."""
    hr = await department(app_sessions, acme, "hr")
    engineering = await department(app_sessions, acme, "engineering")
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        session.add(  # Tom (HR since the seed) also joins Engineering, later
            DepartmentMember(
                tenant_id=acme.tenant_id,
                department_id=engineering,
                user_id=acme.users["tom.becker"],
                role="member",
            )
        )
    engineering_budget = {**hr_budget(engineering, 0.5), "subject_id": str(engineering)}
    assert put(api, acme, [hr_budget(hr, 5.0), engineering_budget]).status_code == 200
    await meter.record(
        acme.tenant_id, Attribution("chat", user_id=acme.users["bob"]), sonnet(300_000)
    )  # Engineering: $0.60 of $0.50

    budgets = {b["subject"]["id"]: b for b in summary_budgets(api, acme, "tom.becker")}
    admitted = ask(api, acme, "tom.becker")

    assert set(budgets) == {str(hr), str(engineering)}
    assert budgets[str(engineering)]["status"] == "exceeded"
    assert budgets[str(engineering)]["applies_to_me"] is False  # shown, not limiting him
    assert budgets[str(hr)]["applies_to_me"] is True
    assert admitted.status_code == 202, admitted.text  # his usage counts for HR


async def test_the_chargeback_report_lists_cost_per_department(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    rows = [
        ("tom.becker", "1.2"),
        ("alice", "0.3"),
        ("carol", "0.5"),
        ("mallory", "0.25"),  # a guest in no department
        ("mallory", None),  # a local model: no price
    ]
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        for who, cost in rows:
            session.add(
                UsageEvent(
                    tenant_id=acme.tenant_id,
                    user_id=acme.users[who],
                    department_id=primary_department_query(acme.users[who]),
                    feature="chat",
                    model="claude-sonnet-5-5" if cost else "llama3",
                    provider="anthropic",
                    local=cost is None,
                    input_tokens=100,
                    output_tokens=10,
                    cost_usd=Decimal(cost) if cost else None,
                )
            )
    today = datetime.now(UTC).date().isoformat()
    params = {"from": today, "to": today, "group_by": "department"}

    response = api.get("/api/v1/usage/export.csv", params=params, headers=acme.as_("erin"))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="needwise-chargeback-{today}-to-{today}.csv"'
    )
    table = list(csv.DictReader(io.StringIO(response.text)))
    hr = str(await department(app_sessions, acme, "hr"))
    assert [(r["department"], r["department_id"], r["cost_usd"]) for r in table] == [
        ("People & HR", hr, "1.500000"),
        ("Finance", str(await department(app_sessions, acme, "finance")), "0.500000"),
        ("No department", "", "0.250000"),
    ]
    assert (table[0]["people"], table[0]["requests"], table[0]["input_tokens"]) == ("2", "2", "200")
    assert (table[2]["requests"], table[2]["unpriced_requests"]) == ("2", "1")
    member = api.get("/api/v1/usage/export.csv", params=params, headers=acme.as_("alice"))
    assert member.status_code == 403
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        exported = (
            await session.scalars(select(AuditEvent).where(AuditEvent.action == "usage.exported"))
        ).one()
    assert exported.details == {"report": "chargeback", "from": today, "to": today}


async def test_when_the_license_ends_department_budgets_are_neither_shown_stored_nor_enforced(
    api: TestClient, acme: Acme, meter: UsageMeter, app_sessions: SessionFactory, clock: Clock
) -> None:
    hr = await department(app_sessions, acme, "hr")
    assert put(api, acme, [hr_budget(hr, 1.0)]).status_code == 200
    await meter.record(
        acme.tenant_id, Attribution("chat", user_id=acme.users["tom.becker"]), sonnet(600_000)
    )
    assert ask(api, acme, "tom.becker").status_code == 429

    clock.now += timedelta(days=31)  # the license expired while the API was running
    meter.invalidate(acme.tenant_id)

    assert ask(api, acme, "tom.becker").status_code == 202  # no longer enforced
    assert api.get("/api/v1/admin/budgets", headers=acme.as_("erin")).json()["budgets"] == []
    refused = put(api, acme, [hr_budget(hr, 5.0)])
    assert (refused.status_code, refused.json()["code"]) == (402, "enterprise_feature")
    tenant = {
        "scope": "tenant",
        "subject_id": None,
        "period": "month",
        "limit_usd": 100,
        "warn_at": 0.8,
        "action": "block",
    }
    assert put(api, acme, [tenant]).status_code == 200
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        kept = (await session.scalars(select(Budget).where(Budget.scope == "department"))).all()
    assert [(b.subject_id, float(b.limit_usd)) for b in kept] == [(hr, 1.0)]  # back with a license
    today = datetime.now(UTC).date().isoformat()
    export = api.get(
        "/api/v1/usage/export.csv", params={"from": today, "to": today}, headers=acme.as_("erin")
    )
    assert (export.status_code, export.json()["code"]) == (402, "enterprise_feature")

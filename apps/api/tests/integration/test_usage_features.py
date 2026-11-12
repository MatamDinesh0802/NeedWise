# The fixtures come from test_conversations_api; tests take them as parameters by name.
# ruff: noqa: F811
"""Usage events from every feature over real HTTP, with real retrieval (Qdrant + OpenFGA, hash
embeddings) and a scripted model: one event per model call, attributed to the right person,
department, agent, feature and record. And a chat run stopped by a budget half-way.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY).
"""

import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fakes import FakeLLM
from tests.integration.conftest import Acme, SessionFactory
from tests.integration.test_conversations_api import (  # noqa: F401  (pytest fixtures)
    acme,
    api,
    ask,
    drop_test_collection,
    events,
    model_name,
    pytestmark,
    start,
    synced,
    use_model,
)
from tests.integration.test_meetings_api import OUTCOME, VTT
from workmind.core.tenancy import tenant_transaction
from workmind.modules.agents.department import EvidenceOut, Finding
from workmind.modules.agents.planning import PlanOut, Subtask
from workmind.modules.conversations.models import Message
from workmind.modules.identity.models import Department
from workmind.modules.usage.models import UsageEvent
from workmind.modules.work.models import Workflow, WorkflowRun


async def usage(sessions: SessionFactory, acme: Acme) -> list[UsageEvent]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(UsageEvent).order_by(UsageEvent.occurred_at, UsageEvent.id)
                )
            ).all()
        )


async def department(sessions: SessionFactory, acme: Acme, slug: str) -> uuid.UUID:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return (await session.scalars(select(Department.id).where(Department.slug == slug))).one()


def resources(api: TestClient) -> Any:
    app: Any = api.app
    return app.state.resources


def wait_until(done: Callable[[], bool], seconds: float = 30) -> None:
    """Polls the API (which runs its own event loop in another thread)."""
    deadline = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < deadline
        time.sleep(0.2)


async def test_a_chat_answer_records_its_call_for_the_person_and_the_answer(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    use_model(api, FakeLLM(["Parental leave ", "is 16 weeks ", "[S1]."], model="claude-sonnet-5-5"))
    conversation = start(api, acme, "alice")
    events(api, acme, "alice", ask(api, acme, "alice", conversation, "How long is parental leave?"))

    (event,) = await usage(app_sessions, acme)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        answer = (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == uuid.UUID(conversation), Message.role == "assistant"
                )
            )
        ).one()
    assert (event.feature, event.user_id, event.agent_key) == ("chat", acme.users["alice"], None)
    assert event.department_id == await department(app_sessions, acme, "hr")
    assert (event.ref_type, event.ref_id) == ("message", str(answer.id))
    assert (event.model, event.model_id, event.provider) == (
        "claude-sonnet-5-5",
        "system:claude-sonnet-5-5",
        "anthropic",
    )
    assert (event.input_tokens, event.output_tokens, event.status) == (100, 3, "ok")
    assert float(event.cost_usd or 0) == (100 * 2.0 + 3 * 10.0) / 1_000_000  # the static price
    assert event.latency_ms is not None
    assert not event.local


async def test_agents_mode_records_planning_each_agent_and_composing(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    resources(api).assistant_mode = "agents"
    resources(api).agent_router = None  # always ask the (scripted) planner
    use_model(
        api,
        FakeLLM(
            "Parental leave is 16 weeks [S1].",
            structured=[
                PlanOut(mode="single", subtasks=[Subtask(agent="hr", goal="Parental leave")]),
                EvidenceOut(
                    findings=[Finding(claim="16 weeks of parental leave", sources=["S1"])],
                    gaps=[],
                    follow_up_query="",
                ),
            ],
        ),
    )
    conversation = start(api, acme, "tom.becker")
    received = events(
        api, acme, "tom.becker", ask(api, acme, "tom.becker", conversation, "Parental leave?")
    )
    assert received[-1][0] == "run.completed", received[-1]

    recorded = await usage(app_sessions, acme)
    assert [(e.feature, e.agent_key) for e in recorded] == [
        ("agents", None),  # the planner
        ("agents", "hr"),  # the HR agent's evidence
        ("agents", None),  # the composed answer
    ]
    assert {e.user_id for e in recorded} == {acme.users["tom.becker"]}
    assert len({e.ref_id for e in recorded}) == 1


async def test_research_mode_records_the_tool_loop_once(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    use_model(api, FakeLLM(tool_turns=[[("search_knowledge", {"query": "parental leave"})]]))
    conversation = start(api, acme, "bob")
    response = api.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"content": "Find our leave policy", "mode": "research"},
        headers=acme.as_("bob", write=True),
    )
    assert response.status_code == 202, response.text
    events(api, acme, "bob", response.json()["run_id"])

    (event,) = await usage(app_sessions, acme)
    assert (event.feature, event.user_id) == ("research", acme.users["bob"])
    assert (event.input_tokens, event.output_tokens) == (50 + 60, 10 + 12)  # both turns
    assert event.cost_usd is None  # "fake" has no price: unknown, never a guess


async def test_the_test_console_counts_for_the_admin_and_the_tested_agent(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    resources(api).agent_router = None
    use_model(
        api,
        FakeLLM(
            "16 weeks [S1].",
            structured=[
                PlanOut(mode="single", subtasks=[Subtask(agent="hr", goal="Parental leave")]),
                EvidenceOut(
                    findings=[Finding(claim="16 weeks", sources=["S1"])],
                    gaps=[],
                    follow_up_query="",
                ),
            ],
        ),
    )
    response = api.post(
        "/api/v1/admin/agents/test",
        json={"question": "How long is parental leave?", "key": "hr"},
        headers=acme.as_("erin", write=True),
    )
    assert response.status_code == 200, response.text

    recorded = await usage(app_sessions, acme)
    assert len(recorded) == 3  # planning, the agent, the composed answer
    assert {(e.feature, e.user_id, e.agent_key) for e in recorded} == {
        ("agent_test", acme.users["erin"], "hr")
    }
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        assert (await session.scalars(select(Message))).all() == []  # never a conversation


async def test_a_workflow_run_counts_for_its_owner_and_the_run(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    use_model(api, FakeLLM(tool_turns=[[]], tool_text="All quiet this week."))
    saved = api.put(
        "/api/v1/admin/workflows/weekly-digest",
        json={
            "key": "weekly-digest",
            "name": "Weekly digest",
            "definition": {"steps": [{"id": "gather", "research": "What happened this week?"}]},
        },
        headers=acme.as_("erin", write=True),
    )
    assert saved.status_code == 200, saved.text
    started = api.post(
        "/api/v1/admin/workflows/weekly-digest/run", headers=acme.as_("erin", write=True)
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    wait_until(
        lambda: (
            api.get("/api/v1/admin/workflows/weekly-digest/runs", headers=acme.as_("erin")).json()[
                0
            ]["status"]
            not in ("queued", "running")
        )
    )

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        run = await session.get(WorkflowRun, uuid.UUID(run_id))
        workflow = (await session.scalars(select(Workflow))).one()
    assert run is not None
    assert run.status == "succeeded", run.error
    (event,) = await usage(app_sessions, acme)
    assert (event.feature, event.user_id, event.ref_type, event.ref_id) == (
        "workflow",
        workflow.owner_id,
        "workflow_run",
        run_id,
    )


async def test_meeting_extraction_counts_for_the_uploader_and_the_meeting(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    use_model(api, FakeLLM(structured=[OUTCOME]))
    uploaded = api.post(
        "/api/v1/meetings",
        params={"collection": "operations-sops", "filename": "ops.vtt", "title": "Ops sync"},
        content=VTT,
        headers={
            **acme.as_("olivia.grant", write=True),
            "Content-Type": "application/octet-stream",
        },
    )
    assert uploaded.status_code == 202, uploaded.text
    meeting_id = uploaded.json()["id"]
    wait_until(
        lambda: (
            api.get(f"/api/v1/meetings/{meeting_id}", headers=acme.as_("olivia.grant")).json()[
                "status"
            ]
            in ("ready", "failed")
        )
    )

    (event,) = await usage(app_sessions, acme)
    assert (event.feature, event.user_id, event.ref_type, event.ref_id) == (
        "meetings",
        acme.users["olivia.grant"],
        "meeting",
        meeting_id,
    )
    assert event.department_id == await department(app_sessions, acme, "operations")


async def test_a_budget_used_up_half_way_ends_the_chat_run_with_budget_exceeded(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    # Claude Opus prices with 10,000x the fake token counts: the answer (100 in, 3 out) costs
    # $4.60, rewriting a follow-up (20 in, 5 out) $1.80. With $6 a month, the first question
    # passes; the second is let in ($4.60 < $6), its rewrite takes the spend to $6.40, and its
    # answer is refused.
    saved = api.put(
        "/api/v1/admin/budgets",
        json={
            "budgets": [
                {
                    "scope": "tenant",
                    "subject_id": None,
                    "period": "month",
                    "limit_usd": 6,
                    "warn_at": 0.5,
                    "action": "block",
                }
            ],
            "user_default": None,
        },
        headers=acme.as_("erin", write=True),
    )
    assert saved.status_code == 200, saved.text
    use_model(api, FakeLLM(["a ", "b ", "c"], model="claude-opus-5-5", scale=10_000))
    conversation = start(api, acme, "alice")
    first = events(api, acme, "alice", ask(api, acme, "alice", conversation, "Parental leave?"))
    assert first[-1][0] == "run.completed"

    second = events(api, acme, "alice", ask(api, acme, "alice", conversation, "And adoption?"))

    name, failed = second[-1]
    assert (name, failed["code"]) == ("run.failed", "budget_exceeded")
    assert failed["message"] == failed["detail"]
    assert "monthly organization budget of $6.00 is used up" in failed["detail"]
    assert failed["budget"]["scope"] == "tenant"
    assert failed["budget"]["status"] == "exceeded"
    assert failed["budget"]["spent_usd"] == 6.4
    recorded = await usage(app_sessions, acme)
    assert [e.output_tokens for e in recorded] == [30_000, 50_000]  # the refused call: none
    third = api.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"content": "Anything else?"},
        headers=acme.as_("alice", write=True),
    )
    assert (third.status_code, third.json()["code"]) == (429, "budget_exceeded")
    # The warning (at 50%) went to the organization's owners and admins, once.
    notes = api.get("/api/v1/notifications", headers=acme.as_("erin")).json()
    assert [n["kind"] for n in notes if n["kind"].startswith("budget.")] == ["budget.warning"]

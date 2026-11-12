# The fixtures come from test_conversations_api; tests take them as parameters by name.
# ruff: noqa: F811
"""The agent test console over real HTTP with real retrieval (Qdrant + OpenFGA, hash
embeddings) and a scripted model: the draft is routed by the real planner and answers as the
administrator, with their permissions, and nothing is stored as a conversation.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY).
"""

import asyncio
import time
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fakes import FakeLLM
from tests.integration.conftest import Acme, SessionFactory
from tests.integration.test_conversations_api import (  # noqa: F401  (pytest fixtures)
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
from workmind.core.tenancy import tenant_transaction
from workmind.modules.agents.department import EvidenceOut, Finding
from workmind.modules.agents.planning import PlanOut, Subtask
from workmind.modules.governance.models import AuditEvent
from workmind.modules.knowledge.models import Collection

MATRIX = (
    b"# Contract Signature Authority Matrix\n\n## Supplier contracts\n"
    b"Supplier contracts from 25,000 to 150,000 euros are signed by the CFO.\n"
)
SALARIES = b"# Salary Bands 2026\n\n## Engineering\nThe L3 engineering band is 68,000 to 82,000.\n"
DRAFT: dict[str, Any] = {
    "key": "legal",
    "name": "Legal agent (draft)",
    "description": "Contracts: who signs them, NDAs, supplier contract reviews.",
    "collections": ["legal-contracts"],
    "tools": ["draft_email"],
    "max_tool_calls": 1,
    "model": "system:claude-test-model",
}


async def upload(
    api: TestClient,
    acme: Acme,
    sessions: SessionFactory,
    who: str,
    key: str,
    name: str,
    data: bytes,
) -> None:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        collection = (await session.scalars(select(Collection).where(Collection.key == key))).one()
    response = api.post(
        f"/api/v1/collections/{collection.id}/documents",
        params={"filename": name},
        content=data,
        headers={**acme.as_(who, write=True), "Content-Type": "text/markdown"},
    )
    assert response.status_code in (200, 201, 202), response.text
    document = response.json()["document"]["id"]
    deadline = time.monotonic() + 20
    while api.get(f"/api/v1/documents/{document}", headers=acme.as_(who)).json()["status"] != (
        "ready"
    ):
        assert time.monotonic() < deadline
        await asyncio.sleep(0.2)


def console(api: TestClient, acme: Acme, body: dict[str, Any]) -> Any:
    app: Any = api.app
    app.state.resources.agent_router = None  # always ask the (scripted) planner
    response = api.post(
        "/api/v1/admin/agents/test", json=body, headers=acme.as_("erin", write=True)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_an_unsaved_draft_is_routed_and_answers_with_its_tools_and_model(
    api: TestClient, synced: Acme, app_sessions: SessionFactory
) -> None:
    acme = synced
    await upload(api, acme, app_sessions, "priya.raman", "legal-contracts", "matrix.md", MATRIX)
    llm = use_model(
        api,
        FakeLLM(
            "The CFO signs it [S1]; a draft e-mail to Finance is ready.",
            structured=[
                PlanOut(
                    mode="single",
                    subtasks=[Subtask(agent="legal", goal="Who signs an 80,000 euro contract?")],
                ),
                EvidenceOut(
                    findings=[
                        Finding(claim="The CFO signs 25,000 to 150,000 euros", sources=["S1"]),
                        Finding(claim="A draft e-mail was prepared", sources=["T1"]),
                    ],
                    gaps=[],
                    follow_up_query="",
                ),
            ],
            tool_turns=[
                [("draft_email", {"to": ["finance@acme.test"], "subject": "S", "body": "B"})],
                [("draft_email", {"to": ["finance@acme.test"], "subject": "S", "body": "B"})],
            ],
        ),
    )

    out = console(
        api, acme, {"question": "Who signs a supplier contract of 80,000 euros?", "draft": DRAFT}
    )
    conversations = api.get("/api/v1/conversations", headers=acme.as_("erin")).json()
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        audit = [
            (e.action, e.details)
            for e in await session.scalars(
                select(AuditEvent).where(AuditEvent.action.in_(["agent.test", "tool.called"]))
            )
        ]

    routing, answer = out["routing"], out["answer"]
    assert routing["mode"] == "single"
    assert routing["routed_by"] == "planner"
    assert routing["agents"] == [
        {
            "key": "legal",
            "name": "Legal agent (draft)",
            "goal": "Who signs an 80,000 euro contract?",
        }
    ]
    assert routing["tested_agent_picked"] is True
    assert '"legal": Contracts: who signs them' in llm.requests[0][1].system  # the draft
    assert answer["text"].startswith("The CFO signs it [S1]")
    assert [c["filename"] for c in answer["citations"]] == ["matrix.md"]
    assert [(s["kind"], s["ok"], s["error"]) for s in answer["steps"]] == [
        ("search", True, None),
        ("tool", True, None),
        ("tool", False, "limit_reached"),  # max_tool_calls is 1
    ]
    assert answer["usage"]["calls"] == 6  # plan, three tool-loop turns, findings, answer
    # The agent's own calls use its model; planning and composing keep their roles' models.
    assert [(role, r.model) for role, r in llm.requests] == [
        ("router", None),
        ("agent", "claude-test-model"),
        ("agent", "claude-test-model"),
        ("answer", None),
    ]
    tools = [d for a, d in audit if a == "tool.called"]
    assert [(d["caller"], d["status"]) for d in tools] == [("agent:legal", "ok")]
    tested = next(d for a, d in audit if a == "agent.test")
    assert (tested["draft"], tested["agents"]) == (True, ["legal"])
    assert conversations["items"] == []  # nothing is stored as a conversation


async def test_the_draft_searches_as_the_administrator_and_cannot_see_more(
    api: TestClient, synced: Acme, app_sessions: SessionFactory
) -> None:
    """Erin administers the organization but is not in hr-records: a draft that names the
    employee records collection finds nothing there for her."""
    acme = synced
    await upload(
        api, acme, app_sessions, "alice", "hr-employee-records", "salary-bands.md", SALARIES
    )
    llm = use_model(
        api,
        FakeLLM(
            structured=[
                PlanOut(mode="single", subtasks=[Subtask(agent="pay", goal="L3 salary band?")])
            ]
        ),
    )
    draft = {
        **DRAFT,
        "key": "pay",
        "name": "Pay agent",
        "collections": ["hr-employee-records"],
        "tools": [],
        "model": None,
    }

    out = console(
        api, acme, {"question": "What is the L3 engineering salary band?", "draft": draft}
    )

    assert out["routing"]["agents"][0]["key"] == "pay"
    assert out["answer"]["citations"] == []
    assert out["answer"]["abstained"] is True
    assert "68,000" not in out["answer"]["text"]
    assert llm.roles() == ["router"]  # nothing found: no model saw any salary text


async def test_a_saved_agent_can_be_tested_by_key_and_routing_alone_without_one(
    api: TestClient, synced: Acme
) -> None:
    acme = synced
    use_model(
        api,
        FakeLLM(
            structured=[
                PlanOut(mode="direct", subtasks=[]),
                PlanOut(mode="direct", subtasks=[]),
            ]
        ),
    )

    by_key = console(api, acme, {"question": "Hi there!", "key": "hr"})
    routing_only = console(api, acme, {"question": "Hi there!"})
    missing = api.post(
        "/api/v1/admin/agents/test",
        json={"question": "q", "key": "nobody"},
        headers=acme.as_("erin", write=True),
    )

    assert by_key["routing"]["mode"] == "direct"
    assert by_key["routing"]["tested_agent_picked"] is False
    assert by_key["answer"]["agent"] == "hr"  # still answers, so the admin sees what it would say
    assert routing_only["answer"] is None
    assert routing_only["routing"]["tested_agent_picked"] is None
    assert missing.status_code == 404


async def test_in_a_conversation_a_saved_agent_calls_its_tools_as_the_person_asking(
    api: TestClient, synced: Acme, app_sessions: SessionFactory
) -> None:
    acme = synced
    hr = next(
        a
        for a in api.get("/api/v1/admin/agents", headers=acme.as_("erin")).json()["items"]
        if a["key"] == "hr"
    )
    body = {k: v for k, v in hr.items() if k not in ("id", "key", "version", "updated_at")}
    saved = api.put(
        "/api/v1/admin/agents/hr",
        json={**body, "tools": ["draft_email"], "max_tool_calls": 1, "note": "Drafts e-mails"},
        headers=acme.as_("erin", write=True),
    )
    use_model(
        api,
        FakeLLM(
            "A draft to HR is ready for you to review.",
            structured=[
                PlanOut(mode="single", subtasks=[Subtask(agent="hr", goal="Draft a leave mail")]),
                EvidenceOut(
                    findings=[Finding(claim="Draft prepared", sources=["T1"])],
                    gaps=[],
                    follow_up_query="",
                ),
            ],
            tool_turns=[[("draft_email", {"to": ["hr@acme.test"], "subject": "S", "body": "B"})]],
        ),
    )
    app: Any = api.app
    app.state.resources.assistant_mode = "agents"
    app.state.resources.agent_router = None
    conversation = start(api, acme, "bob")

    received = events(api, acme, "bob", ask(api, acme, "bob", conversation, "Draft a leave mail"))
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        calls = [
            (e.actor_id, e.details["caller"])
            for e in await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "tool.called")
            )
        ]

    assert saved.status_code == 200, saved.text
    assert received[-1][0] == "run.completed"
    assert calls == [(acme.users["bob"], "agent:hr")]  # as Bob, never as the administrator

"""A research answer after a reload: its steps are stored with the message, and the approvals it
proposed are found by conversation (or, for workflows, by run). Real HTTP, a scripted model.
Needs the test database and TEST_VALKEY_URL."""

import json
import uuid
from typing import Any

from fastapi.testclient import TestClient

from needwise.core.context import RequestContext
from needwise.core.tenancy import tenant_transaction
from needwise.modules.work.approvals import propose
from tests.fakes import FakeLLM
from tests.integration.conftest import Acme, SessionFactory

EMAIL = {
    "to": ["leadership@acme.test"],
    "subject": "Weekly operations report",
    "body": "No incidents this week.",
}


def use_model(api: TestClient, llm: FakeLLM) -> FakeLLM:
    app: Any = api.app
    app.state.resources.llm = llm
    return llm


def research(api: TestClient, acme: Acme, who: str, conversation: str, text: str) -> str:
    response = api.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"content": text, "mode": "research"},
        headers=acme.as_(who, write=True),
    )
    assert response.status_code == 202, response.text
    run: str = response.json()["run_id"]
    with api.stream("GET", f"/api/v1/runs/{run}/events", headers=acme.as_(who)) as stream:
        name = ""
        for line in stream.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: ") and name in ("run.completed", "run.failed"):
                assert name == "run.completed", line
                break
    return run


def start(api: TestClient, acme: Acme, who: str) -> str:
    response = api.post("/api/v1/conversations", json={}, headers=acme.as_(who, write=True))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def approvals(api: TestClient, acme: Acme, who: str, **filters: str) -> list[dict[str, Any]]:
    response = api.get(
        "/api/v1/approvals", params={"status": "all", **filters}, headers=acme.as_(who)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_reloaded_research_answer_shows_its_steps_and_its_approvals(
    api: TestClient, acme: Acme
) -> None:
    use_model(
        api,
        FakeLLM(
            tool_turns=[[("draft_email", EMAIL)], [("send_email", EMAIL)]],
            tool_text="The report is drafted and waits for your approval.",
        ),
    )
    conversation = start(api, acme, "olivia.grant")
    run = research(api, acme, "olivia.grant", conversation, "Email the ops report to leadership")
    other = start(api, acme, "olivia.grant")  # a second conversation, without approvals

    detail = api.get(
        f"/api/v1/conversations/{conversation}", headers=acme.as_("olivia.grant")
    ).json()
    question, answer = detail["messages"]
    approval_id = answer["tool_steps"][1]["approval_id"]

    assert question["tool_steps"] == []
    assert answer["run_id"] == run
    assert answer["tool_steps"] == [
        {"tool": "draft_email", "ok": True, "error": None, "approval_id": None, "summary": None},
        {
            "tool": "send_email",
            "ok": False,
            "error": "approval_required",
            "approval_id": approval_id,
            "summary": "Send an email to leadership@acme.test: Weekly operations report",
        },
    ]
    # Steps are names and outcomes: the draft's text is not stored with them.
    assert "No incidents" not in json.dumps(answer["tool_steps"])

    mine = approvals(api, acme, "olivia.grant", conversation_id=conversation)
    assert [(a["id"], a["status"], a["origin"]["run_id"]) for a in mine] == [
        (approval_id, "pending", run)
    ]
    assert approvals(api, acme, "olivia.grant", conversation_id=other) == []
    # The filter narrows what you may see; it never shows someone else's approvals.
    assert approvals(api, acme, "bob", conversation_id=conversation) == []
    bad = api.get(
        "/api/v1/approvals",
        params={"conversation_id": "not-a-uuid"},
        headers=acme.as_("olivia.grant"),
    )
    assert bad.status_code == 422


async def test_approvals_filter_by_workflow_run(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    olivia = RequestContext(
        request_id="t",
        tenant_id=acme.tenant_id,
        user_id=acme.users["olivia.grant"],
        org_role="member",
        principals=frozenset(),
        auth_method="service",
        scopes=frozenset({"*"}),
    )
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        for run, approver in ((run_a, "priya.raman"), (run_b, "priya.raman"), (run_a, None)):
            await propose(
                session,
                olivia,
                tool="send_email",
                arguments=EMAIL,
                summary=f"Report for {run}",
                approver_id=acme.users[approver] if approver else None,
                origin={"via": "workflow", "workflow": "weekly-ops", "workflow_run_id": str(run)},
            )

    as_priya = approvals(api, acme, "priya.raman", workflow_run_id=str(run_a))
    as_olivia = approvals(api, acme, "olivia.grant", workflow_run_id=str(run_a))

    assert [a["summary"] for a in as_priya] == [f"Report for {run_a}"]  # approver of one
    assert len(as_olivia) == 2  # requester of both
    assert {a["origin"]["workflow"] for a in as_olivia} == {"weekly-ops"}
    assert approvals(api, acme, "bob", workflow_run_id=str(run_a)) == []

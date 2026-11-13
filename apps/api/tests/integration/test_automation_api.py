"""Automation over real HTTP (docs/automation.md): the Request form and routing (rules, the
scripted router model, triage), the email-to-request stub, mentions, the assistant's
create_task, approver rules for the assistant's actions, personal and admin settings, Slack
secrets, and YAML workflow definitions. Needs the test database and TEST_VALKEY_URL."""

import json
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from needwise.core.context import RequestContext
from needwise.core.tenancy import tenant_transaction
from needwise.modules.agents.research import research
from needwise.modules.governance.models import AuditEvent
from needwise.modules.tools.execute import execute_tool
from needwise.modules.tools.registry import ToolRegistry
from needwise.modules.work.models import Approval, Notification, Task
from needwise.modules.work.routing import RouteChoice
from needwise.modules.work.tools import create_task_tool, send_email_tool
from needwise.providers.llm import LLMProvider
from tests.fakes import FakeLLM
from tests.integration.conftest import Acme, SessionFactory

RULES = {
    "rules": [
        {"department": "finance", "keywords": ["invoice", "refund"], "categories": ["Billing"]},
        {"department": "operations", "keywords": ["laptop", "vpn"], "categories": ["IT"]},
    ]
}


def resources(api: TestClient) -> Any:
    app: Any = api.app
    return app.state.resources


def use_model(api: TestClient, llm: FakeLLM) -> FakeLLM:
    resources(api).llm = llm
    return llm


def work_tools(sessions: SessionFactory, llm: FakeLLM) -> ToolRegistry:
    """The work tools on the test's own connections (the app's live on its own event loop)."""

    async def llm_for(_tenant: object) -> LLMProvider:
        return llm

    tools = ToolRegistry()
    tools.register(send_email_tool(None, sessions))
    tools.register(create_task_tool(sessions, llm_for))
    return tools


def set_rules(api: TestClient, acme: Acme) -> None:
    saved = api.put("/api/v1/admin/routing-rules", json=RULES, headers=acme.as_("erin", write=True))
    assert saved.status_code == 200, saved.text


def ask(api: TestClient, acme: Acme, who: str, **body: Any) -> Any:
    return api.post("/api/v1/requests", json=body, headers=acme.as_(who, write=True))


async def notifications(
    sessions: SessionFactory, acme: Acme, who: str, kind: str
) -> list[Notification]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(Notification).where(
                        Notification.user_id == acme.users[who], Notification.kind == kind
                    )
                )
            ).all()
        )


async def audit(sessions: SessionFactory, acme: Acme, action: str) -> list[AuditEvent]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return list(
            (
                await session.scalars(
                    select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.seq)
                )
            ).all()
        )


# --- Requests and routing --------------------------------------------------------------------


async def test_a_request_goes_to_the_queue_its_rules_name_and_says_why(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    llm = use_model(api, FakeLLM())
    set_rules(api, acme)
    options = api.get("/api/v1/requests/options", headers=acme.as_("bob")).json()
    assert options["categories"] == ["Billing", "IT"]
    assert "finance" in [d["key"] for d in options["departments"]]

    created = ask(api, acme, "bob", title="Refund for invoice 1182", description="Overcharged.")

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["routing"]["method"] == "rule"
    assert body["routing"]["department"] == "finance"
    assert body["routing"]["explanation"] == "Matched the Finance rule: “invoice”, “refund”."
    task = body["task"]
    assert (task["triage"], task["assignee_id"]) == (False, None)
    assert task["origin"]["via"] == "request"
    assert llm.requests == []  # the rules decided
    # Finance's members see it in their queue; its manager is told.
    queue = api.get("/api/v1/tasks", params={"scope": "queues"}, headers=acme.as_("carol")).json()
    assert task["id"] in [t["id"] for t in queue]
    (told,) = await notifications(app_sessions, acme, "hannah.schmidt", "request.queued")
    assert told.link == f"/tasks/{task['id']}"
    (routed,) = await audit(app_sessions, acme, "request.routed")
    assert routed.details["method"] == "rule"
    assert routed.details["department"] == "finance"
    assert "Refund" not in json.dumps(routed.details)  # the reason, not the request's text

    by_category = ask(api, acme, "bob", title="Card declined", category="Billing").json()
    assert by_category["routing"]["matched"] == ["category “Billing”"]


async def test_the_model_routes_what_rules_do_not_and_triage_gets_the_rest(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    llm = use_model(
        api,
        FakeLLM(
            structured=[
                RouteChoice(department="hr", confidence=0.92, reason="A leave question."),
                RouteChoice(department="sales", confidence=0.3, reason="Unclear."),
            ]
        ),
    )
    set_rules(api, acme)

    by_model = ask(api, acme, "bob", title="How do I register my new baby?").json()
    assert (by_model["routing"]["method"], by_model["routing"]["department"]) == ("model", "hr")
    assert by_model["routing"]["confidence"] == 0.92
    assert [role for role, _ in llm.requests] == ["router"]

    unsure = ask(api, acme, "bob", title="Question about the thing from Tuesday").json()
    task = unsure["task"]
    assert (unsure["routing"]["method"], task["triage"], task["department_id"]) == (
        "triage",
        True,
        None,
    )
    assert "A person will route it." in unsure["routing"]["explanation"]

    # Triage: the org admins see it; members don't; the requester can still open their own.
    for who, sees in (("erin", True), ("priya.raman", True), ("carol", False)):
        listed = api.get("/api/v1/tasks", params={"scope": "triage"}, headers=acme.as_(who))
        assert (task["id"] in [t["id"] for t in listed.json()]) is sees, who
    assert api.get(f"/api/v1/tasks/{task['id']}", headers=acme.as_("bob")).status_code == 200
    assert api.get(f"/api/v1/tasks/{task['id']}", headers=acme.as_("carol")).status_code == 404
    assert await notifications(app_sessions, acme, "erin", "request.triage")

    # Only a triager routes it; then it is in Finance's queue.
    url = f"/api/v1/tasks/{task['id']}/route"
    refused = api.post(url, json={"department": "finance"}, headers=acme.as_("bob", write=True))
    assert refused.status_code == 403
    routed = api.post(
        url,
        json={"department": "finance", "note": "Expense question"},
        headers=acme.as_("erin", write=True),
    )
    assert routed.status_code == 200, routed.text
    assert routed.json()["routing"]["method"] == "human"
    assert routed.json()["triage"] is False
    assert api.get(f"/api/v1/tasks/{task['id']}", headers=acme.as_("carol")).status_code == 200
    assert (await audit(app_sessions, acme, "request.routed"))[-1].details == {
        "method": "human",
        "department": "finance",
        "from": None,
    }
    # A queue member can send it on to the right queue.
    onward = api.post(url, json={"department": "operations"}, headers=acme.as_("carol", write=True))
    assert onward.status_code == 200, onward.text


async def test_guests_cannot_send_requests_and_the_threshold_is_the_admins(
    api: TestClient, acme: Acme
) -> None:
    use_model(api, FakeLLM(structured=[RouteChoice(department="hr", confidence=0.8)]))
    assert ask(api, acme, "mallory", title="Give me access").status_code == 403
    assert api.get("/api/v1/requests/options", headers=acme.as_("mallory")).status_code == 403
    saved = api.put(
        "/api/v1/admin/automation",
        json={"routing": {"threshold": 0.9}},
        headers=acme.as_("erin", write=True),
    )
    assert saved.status_code == 200, saved.text
    assert ask(api, acme, "bob", title="Pension").json()["routing"]["method"] == "triage"


async def test_requests_by_email_come_from_colleagues_only(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    use_model(api, FakeLLM())
    set_rules(api, acme)
    url = "/api/v1/admin/requests/inbound-email"
    admin = acme.as_("erin", write=True)
    sent = api.post(
        url,
        json={"from": "Carol@Acme.test", "subject": "VPN broken", "body": "Since Monday."},
        headers=admin,
    )
    assert sent.status_code == 201, sent.text
    task = sent.json()["task"]
    assert sent.json()["routing"]["department"] == "operations"
    assert (task["created_by"], task["origin"]["via"]) == (str(acme.users["carol"]), "email")
    (told,) = await notifications(app_sessions, acme, "carol", "request.routed")
    assert told.title == "Your request went to Operations: VPN broken"

    for sender in ("stranger@example.com", "mallory@acme.test"):
        refused = api.post(url, json={"from": sender, "subject": "Hi"}, headers=admin)
        assert (refused.status_code, refused.json()["code"]) == (422, "unknown_sender")
    assert len(await audit(app_sessions, acme, "request.email_refused")) == 2
    member = api.post(
        url, json={"from": "carol@acme.test", "subject": "x"}, headers=acme.as_("bob", write=True)
    )
    assert member.status_code == 403


async def test_mentions_reach_colleagues_who_can_open_the_task(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    use_model(api, FakeLLM())
    set_rules(api, acme)
    ask(
        api,
        acme,
        "bob",
        title="Refund for invoice 77",
        description="@noah.fischer@acme.test knows the vendor; @dan@acme.test asked about it.",
    )
    (mention,) = await notifications(app_sessions, acme, "noah.fischer", "mention")
    assert mention.title == "Bob Okafor mentioned you: Refund for invoice 77"
    assert await notifications(app_sessions, acme, "dan", "mention") == []  # not in Finance


async def test_the_assistants_create_task_is_routed_after_approval(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    use_model(api, FakeLLM())
    set_rules(api, acme)
    bob = RequestContext(
        request_id="t",
        tenant_id=acme.tenant_id,
        user_id=acme.users["bob"],
        org_role="member",
        principals=frozenset(),
        auth_method="session",
        scopes=frozenset({"*"}),
    )
    result = await execute_tool(
        work_tools(app_sessions, FakeLLM()),
        app_sessions,
        bob,
        "create_task",
        {"title": "My laptop will not boot", "description": "Since the update."},
        approved=True,
    )
    assert result.data is not None
    assert result.data["routing"]["department"] == "operations"
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        task = (await session.scalars(select(Task).where(Task.title.like("My laptop%")))).one()
    assert task.origin["via"] == "assistant"
    assert task.routing is not None
    assert task.routing["source"] == "assistant"
    assert await notifications(app_sessions, acme, "bob", "request.routed")


# --- Approver rules for the assistant's actions ------------------------------------------------


async def test_the_assistants_actions_follow_the_tenants_approver_rule(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    admin = acme.as_("erin", write=True)
    bad = api.put(
        "/api/v1/admin/automation",
        json={"approver_rules": {"send_email": {"department_manager": "legal"}}},
        headers=admin,
    )
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_approver")
    malformed = api.put(
        "/api/v1/admin/automation",
        json={"approver_rules": {"send_email": {"role": "boss"}}},
        headers=admin,
    )
    assert malformed.status_code == 422
    saved = api.put(
        "/api/v1/admin/automation",
        json={"approver_rules": {"send_email": {"department_manager": "requester"}}},
        headers=admin,
    )
    assert saved.status_code == 200, saved.text
    assert api.get("/api/v1/admin/automation", headers=acme.as_("erin")).json()[
        "approver_rules"
    ] == {"send_email": {"department_manager": "requester"}}
    assert api.get("/api/v1/admin/automation", headers=acme.as_("bob")).status_code == 403

    llm = FakeLLM(
        tool_turns=[[("send_email", {"to": ["team@acme.test"], "subject": "Hi", "body": "Notes."})]]
    )
    bob = RequestContext(
        request_id="t",
        tenant_id=acme.tenant_id,
        user_id=acme.users["bob"],
        org_role="member",
        principals=frozenset(),
        auth_method="session",
        scopes=frozenset({"*"}),
    )
    result = await research(
        llm, work_tools(app_sessions, llm), app_sessions, bob, "Email the team", organization="Acme"
    )
    (step,) = result.steps
    assert step.approval_id is not None
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        approval = (await session.scalars(select(Approval))).one()
    assert (approval.requested_by, approval.approver_id) == (
        acme.users["bob"],
        acme.users["marcus.bell"],
    )
    card = api.get(f"/api/v1/approvals/{approval.id}", headers=acme.as_("bob")).json()
    assert card["rule"] == "a manager of the requester's department"
    assert (card["stage"], card["stages"]) == (1, 1)
    # The model is told who decides: it used to say the action waited for Bob's own approval,
    # though Bob cannot decide it (decide() answers 404 to him).
    (reply,) = llm.tool_replies
    assert "waits for Marcus Bell to approve it" in reply.content
    assert "waiting for their approval" not in reply.content

    # Alice manages HR: nobody else can approve her email, so nothing is proposed.
    alice = RequestContext(
        request_id="t",
        tenant_id=acme.tenant_id,
        user_id=acme.users["alice"],
        org_role="member",
        principals=frozenset(),
        auth_method="session",
        scopes=frozenset({"*"}),
    )
    refused = await research(
        FakeLLM(
            tool_turns=[[("send_email", {"to": ["a@acme.test"], "subject": "x", "body": "y"})]]
        ),
        work_tools(app_sessions, FakeLLM()),
        app_sessions,
        alice,
        "Email",
        organization="Acme",
    )
    assert [(s.error, s.approval_id) for s in refused.steps] == [("no_approver", None)]


# --- Personal settings ---------------------------------------------------------------------------


async def test_people_choose_their_channels_and_backup_approver(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    url = "/api/v1/me/work-preferences"
    defaults = api.get(url, headers=acme.as_("olivia.grant")).json()
    assert defaults["channels"]["approvals"] == {"email": "off", "slack": False}
    assert len(defaults["categories"]) == 9
    assert (defaults["email_available"], defaults["slack_available"]) == (False, False)

    me = acme.as_("olivia.grant", write=True)
    self_backup = api.put(
        url, json={"backup_approver_id": str(acme.users["olivia.grant"])}, headers=me
    )
    assert (self_backup.status_code, self_backup.json()["code"]) == (422, "invalid_backup_approver")
    guest_backup = api.put(url, json={"backup_approver_id": str(acme.users["mallory"])}, headers=me)
    assert guest_backup.status_code == 422
    saved = api.put(
        url,
        json={
            "channels": {"approvals": {"email": "digest"}, "escalations": {"email": "immediate"}},
            "digest_hour": 6,
            "backup_approver_id": str(acme.users["ivan.petrov"]),
        },
        headers=me,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["channels"]["approvals"] == {"email": "digest", "slack": False}
    assert body["channels"]["mentions"] == {"email": "off", "slack": False}
    assert (body["digest_hour"], body["backup_approver_id"]) == (6, str(acme.users["ivan.petrov"]))
    (event,) = await audit(app_sessions, acme, "me.work_preferences.updated")
    assert event.details["backup_changed"] is True
    assert event.details["email"] == ["approvals", "escalations"]
    bad = api.put(url, json={"channels": {"approvals": {"email": "weekly"}}}, headers=me)
    assert bad.status_code == 422


# --- Slack (admin) -------------------------------------------------------------------------------


async def test_slack_secrets_are_write_only_and_checked(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    url = "/api/v1/admin/slack"
    admin = acme.as_("erin", write=True)
    token, hook = "xoxb-123-secret-token", "https://hooks.slack.com/services/T1/B1/secret"
    assert api.put(url, json={"bot_token": "xoxp-user"}, headers=admin).status_code == 422
    bad_hook = api.put(url, json={"webhook_url": "https://evil.example/x"}, headers=admin)
    assert (bad_hook.status_code, bad_hook.json()["code"]) == (422, "invalid_webhook")

    saved = api.put(
        url,
        json={"bot_token": token, "webhook_url": hook, "channel_categories": ["escalations"]},
        headers=admin,
    )
    assert saved.status_code == 200, saved.text
    shown = api.get(url, headers=acme.as_("erin"))
    for response in (saved, shown):
        assert token not in response.text
        assert "secret" not in response.text
    assert shown.json() == {
        "bot_configured": True,
        "webhook_configured": True,
        "channel_categories": ["escalations"],
        "webhook_prefix": "https://hooks.slack.com/",
    }
    # Leaving a secret out keeps it; null removes it.
    kept = api.put(url, json={"channel_categories": []}, headers=admin).json()
    assert kept["bot_configured"]
    assert kept["webhook_configured"]
    removed = api.put(url, json={"webhook_url": None}, headers=admin).json()
    assert (removed["bot_configured"], removed["webhook_configured"]) == (True, False)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "admin.slack.saved")
            )
        ).all()
    assert token not in str([e.details for e in events])
    prefs = api.get("/api/v1/me/work-preferences", headers=acme.as_("bob")).json()
    assert prefs["slack_available"] is True

    # The test message goes through Slack's API (a stub here) as the admin.
    seen: list[httpx.Request] = []

    def stub(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "auth.test":
            return httpx.Response(200, json={"ok": True, "team": "Acme"})
        if method == "users.lookupByEmail":
            return httpx.Response(200, json={"ok": True, "user": {"id": "U-ERIN"}})
        return httpx.Response(200, json={"ok": True})

    res = resources(api)
    original = res.http
    res.http = httpx.AsyncClient(transport=httpx.MockTransport(stub))
    try:
        tested = api.post(f"{url}/test", json={"target": "bot"}, headers=admin)
    finally:
        await res.http.aclose()
        res.http = original
    assert tested.json() == {"ok": True, "detail": "Sent you a direct message in Acme."}
    assert all(r.headers["Authorization"] == f"Bearer {token}" for r in seen)
    assert b"erin%40acme.test" in seen[1].content
    no_hook = api.post(f"{url}/test", json={"target": "webhook"}, headers=admin)
    assert no_hook.status_code == 409


# --- YAML workflow definitions ----------------------------------------------------------------

SOURCE = """\
# Sent on Monday mornings.
trigger:
  schedule: "0 8 * * MON"
steps:
  - id: gather
    research: Summarize this week's operations.
  - id: send
    tool: send_email
    arguments:
      to: [leadership@acme.test]
      subject: "Weekly report {{ today }}"
      body: "{{ steps.gather.text }}"
    approver:
      chain:
        - department_manager: requester
        - priya.raman@acme.test
"""


async def test_workflows_are_saved_as_yaml_and_errors_point_at_lines(
    api: TestClient, acme: Acme
) -> None:
    admin = acme.as_("erin", write=True)
    url = "/api/v1/admin/workflows/weekly-report"
    saved = api.put(
        url, json={"key": "weekly-report", "name": "Weekly", "source": SOURCE}, headers=admin
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["source"] == SOURCE  # comments kept
    assert saved.json()["definition"]["steps"][1]["approver"] == {
        "chain": [{"department_manager": "requester"}, "priya.raman@acme.test"]
    }
    listed = api.get("/api/v1/admin/workflows", headers=acme.as_("erin")).json()
    assert next(w for w in listed if w["key"] == "weekly-report")["source"] == SOURCE

    # Saved as an object: shown as YAML.
    as_object = api.put(
        "/api/v1/admin/workflows/plain",
        json={
            "key": "plain",
            "name": "Plain",
            "definition": {"steps": [{"id": "a", "research": "Hi"}]},
        },
        headers=admin,
    )
    assert as_object.json()["source"] == "steps:\n- id: a\n  research: Hi\n"

    broken = SOURCE.replace("tool: send_email", "tool: send_email\n    research: also this")
    refused = api.put(
        url, json={"key": "weekly-report", "name": "Weekly", "source": broken}, headers=admin
    )
    assert refused.status_code == 422
    (error,) = refused.json()["errors"]
    assert (error["line"], error["path"]) == (7, "steps[1]")  # the step that starts there
    assert refused.json()["detail"].startswith("line 7: steps[1]: step send: give either")

    checked = api.post(
        "/api/v1/admin/workflows/validate", json={"source": "steps: [\n"}, headers=admin
    )
    assert checked.json()["valid"] is False
    assert checked.json()["errors"][0]["message"].startswith("Not valid YAML")
    ok = api.post("/api/v1/admin/workflows/validate", json={"source": SOURCE}, headers=admin).json()
    assert (ok["valid"], ok["errors"]) == (True, [])

    both = api.put(
        url,
        json={"key": "weekly-report", "name": "W", "source": SOURCE, "definition": {"steps": []}},
        headers=admin,
    )
    assert both.status_code == 422
    # Erin is in Operations, which Olivia manages; a rule nobody can meet is refused at saving.
    impossible = SOURCE.replace("priya.raman@acme.test", "{department_manager: legal}")
    unmet = api.put(
        url, json={"key": "weekly-report", "name": "W", "source": impossible}, headers=admin
    )
    assert (unmet.status_code, unmet.json()["code"]) == (422, "invalid_approver")


async def test_the_editor_flags_unknown_tools_and_approvers_before_saving(
    api: TestClient, acme: Acme
) -> None:
    # "tool: send_emial" validated and saved, and failed only at run time (after the research
    # step's model calls); an approver nobody could be showed no problem until Save.
    admin = acme.as_("erin", write=True)
    typo = SOURCE.replace("tool: send_email", "tool: send_emial")
    nobody = SOURCE.replace("priya.raman@acme.test", "nobody@nowhere.test")

    def validate(source: str) -> Any:
        return api.post("/api/v1/admin/workflows/validate", json={"source": source}, headers=admin)

    unknown = validate(typo).json()
    unmet = validate(nobody).json()
    saved = api.put(
        "/api/v1/admin/workflows/typo",
        json={"key": "typo", "name": "Typo", "source": typo},
        headers=admin,
    )

    assert unknown["valid"] is False
    (problem,) = unknown["errors"]
    assert (problem["line"], problem["path"]) == (8, "steps[1].tool")
    assert problem["message"].startswith("No tool named “send_emial”")
    assert unmet["valid"] is False
    assert (unmet["errors"][0]["line"], unmet["errors"][0]["path"]) == (14, "steps[1].approver")
    assert "nobody@nowhere.test" in unmet["errors"][0]["message"]
    assert (saved.status_code, saved.json()["code"]) == (422, "unknown_tool")


async def test_a_definition_in_json_may_use_tabs_and_exponents(api: TestClient, acme: Acme) -> None:
    # PyYAML reads YAML 1.1: tab-indented JSON failed, and 1e5 became the string "1e5".
    source = '{\n\t"steps": [\n\t\t{"id": "gather", "research": "What happened?"}\n\t]\n}'
    checked = api.post(
        "/api/v1/admin/workflows/validate",
        json={"source": source},
        headers=acme.as_("erin", write=True),
    ).json()
    assert (checked["valid"], checked["errors"]) == (True, [])
    broken = api.post(
        "/api/v1/admin/workflows/validate",
        json={"source": '{\n\t"steps": [\n}'},
        headers=acme.as_("erin", write=True),
    ).json()
    assert broken["errors"][0]["message"].startswith("Not valid JSON")
    assert broken["errors"][0]["line"] == 3

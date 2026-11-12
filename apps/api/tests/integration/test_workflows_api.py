"""Admin > Workflows over real HTTP: deleting a workflow. Needs the test database and
TEST_VALKEY_URL."""

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tests.integration.conftest import Acme, SessionFactory
from workmind.core.tenancy import tenant_transaction
from workmind.modules.governance.models import AuditEvent
from workmind.modules.work.models import Workflow, WorkflowRun

DEFINITION = {"steps": [{"id": "gather", "research": "What happened this week?"}]}


async def test_an_admin_deletes_a_workflow_once_no_run_is_in_progress(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    saved = api.put(
        "/api/v1/admin/workflows/weekly-digest",
        json={"key": "weekly-digest", "name": "Weekly digest", "definition": DEFINITION},
        headers=acme.as_("erin", write=True),
    )
    assert saved.status_code == 200, saved.text
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        workflow = (
            await session.scalars(select(Workflow).where(Workflow.key == "weekly-digest"))
        ).one()
        run = WorkflowRun(
            tenant_id=acme.tenant_id,
            workflow_id=workflow.id,
            trigger="manual",
            status="waiting_approval",
        )
        session.add(run)
        await session.flush()
        run_id = run.id
    url = "/api/v1/admin/workflows/weekly-digest"

    assert api.delete(url, headers=acme.as_("bob", write=True)).status_code == 403  # a member
    waiting = api.delete(url, headers=acme.as_("erin", write=True))
    assert (waiting.status_code, waiting.json()["code"]) == (409, "runs_in_progress")

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(WorkflowRun).where(WorkflowRun.id == run_id).values(status="cancelled")
        )
    assert api.delete(url, headers=acme.as_("erin", write=True)).status_code == 204

    listed = api.get("/api/v1/admin/workflows", headers=acme.as_("erin")).json()
    assert "weekly-digest" not in {w["key"] for w in listed}
    assert api.get(f"{url}/runs", headers=acme.as_("erin")).status_code == 404
    assert api.delete(url, headers=acme.as_("erin", write=True)).status_code == 404
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        assert await session.get(WorkflowRun, run_id) is None  # the history went with it
        deleted = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "admin.workflow.deleted")
            )
        ).one()
    assert (deleted.resource_id, deleted.details) == (
        "weekly-digest",
        {"name": "Weekly digest", "runs": 1},
    )

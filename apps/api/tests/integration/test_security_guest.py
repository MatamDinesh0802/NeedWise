"""A guest (Mallory, the external contractor) and the task API: tasks for themselves only.

Before the fix a guest could push tasks into any department's queue (and learn which department
slugs exist from `unknown_department`) or onto any employee's list.
"""

from fastapi.testclient import TestClient

from tests.integration.conftest import Acme


def test_a_guest_can_only_create_tasks_for_themselves(api: TestClient, acme: Acme) -> None:
    as_mallory = acme.as_("mallory", write=True)

    own = api.post(
        "/api/v1/tasks",
        json={"title": "Read the contractor guide", "assignee_id": str(acme.users["mallory"])},
        headers=as_mallory,
    )
    unassigned = api.post("/api/v1/tasks", json={"title": "A note to self"}, headers=as_mallory)
    queue = api.post(
        "/api/v1/tasks",
        json={"title": "Reset every VPN password", "department": "engineering"},
        headers=as_mallory,
    )
    someone = api.post(
        "/api/v1/tasks",
        json={"title": "Wire 50,000 euros", "assignee_id": str(acme.users["hannah.schmidt"])},
        headers=as_mallory,
    )
    member = api.post(
        "/api/v1/tasks",
        json={"title": "Review the runbook", "department": "engineering"},
        headers=acme.as_("bob", write=True),
    )

    assert own.status_code == 201
    assert unassigned.status_code == 201
    assert queue.status_code == 403
    assert someone.status_code == 403
    assert member.status_code == 201
    mine = api.get("/api/v1/tasks", params={"scope": "mine"}, headers=acme.as_("hannah.schmidt"))
    assert all(t["title"] != "Wire 50,000 euros" for t in mine.json())

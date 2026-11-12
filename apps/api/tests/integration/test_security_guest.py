"""A guest (Mallory, the external contractor) and the task API: tasks for themselves only.

Before the fix a guest could push tasks into any department's queue (and learn which department
slugs exist from `unknown_department`) or onto any employee's list. After it, a guest could still
create a task for themselves and then hand it to any employee with PATCH (employee ids show as
document owners in a collection shared with them), with a "Handed over to you" notification.
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


def test_a_guest_cannot_hand_their_task_to_someone_else(api: TestClient, acme: Acme) -> None:
    as_mallory = acme.as_("mallory", write=True)
    created = api.post("/api/v1/tasks", json={"title": "Approve the wire"}, headers=as_mallory)
    assert created.status_code == 201
    task = created.json()["id"]

    handed = api.patch(
        f"/api/v1/tasks/{task}",
        json={"assignee_id": str(acme.users["hannah.schmidt"])},
        headers=as_mallory,
    )
    to_herself = api.patch(
        f"/api/v1/tasks/{task}",
        json={"assignee_id": str(acme.users["mallory"]), "status": "in_progress"},
        headers=as_mallory,
    )

    assert handed.status_code == 403
    assert to_herself.status_code == 200
    assert to_herself.json()["assignee_id"] == str(acme.users["mallory"])
    hannah = acme.as_("hannah.schmidt")
    assert all(t["id"] != task for t in api.get("/api/v1/tasks", headers=hannah).json())
    notes = api.get("/api/v1/notifications", headers=hannah).json()
    assert all("Approve the wire" not in n["title"] for n in notes)


def test_a_member_can_still_hand_a_task_over(api: TestClient, acme: Acme) -> None:
    as_bob = acme.as_("bob", write=True)
    task = api.post("/api/v1/tasks", json={"title": "Rotate the keys"}, headers=as_bob).json()

    handed = api.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"assignee_id": str(acme.users["hannah.schmidt"])},
        headers=as_bob,
    )

    assert handed.status_code == 200
    assert handed.json()["assignee_id"] == str(acme.users["hannah.schmidt"])

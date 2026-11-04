"""Administration API through real HTTP requests, against the test database.

Sessions are written straight into Valkey (as a successful login would), so these tests need
TEST_DATABASE_ADMIN_URL and TEST_VALKEY_URL but not Keycloak.
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.integration.conftest import VALKEY_URL, Acme, SessionFactory, seed_acme
from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.authz.models import AuthzOutbox
from workmind.modules.identity import admin
from workmind.modules.identity.models import Department

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL to run")


# --- Who may call what ---------------------------------------------------------------------

ENDPOINTS = {
    "list people": ("GET", "/api/v1/admin/users"),
    "list departments": ("GET", "/api/v1/admin/departments"),
    "create group": ("POST", "/api/v1/admin/groups"),
    "read audit log": ("GET", "/api/v1/admin/audit-events"),
}
ALLOWED = {
    "priya.raman": {"list people", "list departments", "create group", "read audit log"},  # owner
    "erin": {"list people", "list departments", "create group", "read audit log"},  # admin
    "amara.diallo": {"read audit log"},  # auditor
    "alice": set(),  # member
    "mallory": set(),  # guest
}


@pytest.mark.parametrize("person", list(ALLOWED))
async def test_role_by_endpoint_matrix(api: TestClient, acme: Acme, person: str) -> None:
    for name, (method, path) in ENDPOINTS.items():
        body = {"slug": f"g-{uuid.uuid4().hex[:8]}", "name": "Group"} if method == "POST" else None
        response = api.request(
            method, path, json=body, headers=acme.as_(person, write=method == "POST")
        )
        expected = 403 if name not in ALLOWED[person] else (201 if method == "POST" else 200)
        assert response.status_code == expected, f"{person} / {name}: {response.text}"


async def test_without_a_session_everything_is_401(api: TestClient) -> None:
    assert api.get("/api/v1/admin/users").status_code == 401


async def test_state_changes_need_csrf_protection(api: TestClient, acme: Acme) -> None:
    response = api.post(
        "/api/v1/admin/groups", json={"slug": "x", "name": "X"}, headers=acme.as_("erin")
    )
    assert (response.status_code, response.json()["code"]) == (403, "csrf_origin")


# --- Guard rails ---------------------------------------------------------------------------


async def test_admins_cannot_grant_or_touch_the_owner_role(api: TestClient, acme: Acme) -> None:
    promote = api.patch(
        f"/api/v1/admin/users/{acme.users['alice']}",
        json={"org_role": "owner"},
        headers=acme.as_("erin", write=True),
    )
    disable_owner = api.patch(
        f"/api/v1/admin/users/{acme.users['priya.raman']}",
        json={"status": "disabled"},
        headers=acme.as_("erin", write=True),
    )
    assert (promote.status_code, promote.json()["code"]) == (403, "owner_required")
    assert (disable_owner.status_code, disable_owner.json()["code"]) == (403, "owner_required")


async def test_nobody_can_change_their_own_role_or_status(api: TestClient, acme: Acme) -> None:
    response = api.patch(
        f"/api/v1/admin/users/{acme.users['erin']}",
        json={"org_role": "member"},
        headers=acme.as_("erin", write=True),
    )
    assert (response.status_code, response.json()["code"]) == (403, "self_change")


async def test_the_last_active_owner_cannot_be_removed(
    owner_sessions: SessionFactory, acme: Acme
) -> None:
    # Unreachable through the API (the only owner is always the one acting), so this checks the
    # service directly, as a future SCIM sync or a stale role might reach it.
    stale_owner = RequestContext(
        request_id="t",
        tenant_id=acme.tenant_id,
        user_id=acme.users["erin"],
        org_role="owner",
        principals=frozenset(),
        auth_method="session",
    )
    with pytest.raises(ApiError, match="at least one active owner"):
        async with tenant_transaction(owner_sessions, acme.tenant_id) as session:
            await admin.update_user(
                session, stale_owner, acme.users["priya.raman"], {"status": "disabled"}
            )


async def test_disabling_someone_ends_their_access_and_queues_tuple_removal(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    assert api.get("/api/v1/me", headers=acme.as_("bob")).status_code == 200

    response = api.patch(
        f"/api/v1/admin/users/{acme.users['bob']}",
        json={"status": "disabled"},
        headers=acme.as_("erin", write=True),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "disabled"
    assert api.get("/api/v1/me", headers=acme.as_("bob")).status_code == 401
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        rows = (
            await session.scalars(select(AuthzOutbox).where(AuthzOutbox.status == "pending"))
        ).all()
    removed = {(d["user"], d["relation"]) for row in rows for d in row.deletes}
    assert (f"user:{acme.users['bob']}", "member") in removed  # organization membership


async def test_nested_groups_cannot_form_a_cycle(api: TestClient, acme: Acme) -> None:
    def create(slug: str) -> str:
        response = api.post(
            "/api/v1/admin/groups",
            json={"slug": slug, "name": slug},
            headers=acme.as_("erin", write=True),
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    outer, inner = create("outer"), create("inner")
    nest = api.put(
        f"/api/v1/admin/groups/{outer}/members/groups/{inner}", headers=acme.as_("erin", write=True)
    )
    cycle = api.put(
        f"/api/v1/admin/groups/{inner}/members/groups/{outer}", headers=acme.as_("erin", write=True)
    )
    itself = api.put(
        f"/api/v1/admin/groups/{outer}/members/groups/{outer}", headers=acme.as_("erin", write=True)
    )

    assert nest.status_code == 204
    assert (cycle.status_code, cycle.json()["code"]) == (409, "group_cycle")
    assert (itself.status_code, itself.json()["code"]) == (409, "group_cycle")


async def test_other_tenants_records_are_invisible(
    api: TestClient, acme: Acme, owner_sessions: SessionFactory
) -> None:
    other = await seed_acme(owner_sessions)

    response = api.patch(
        f"/api/v1/admin/users/{other.users['bob']}",
        json={"title": "pwned"},
        headers=acme.as_("erin", write=True),
    )

    assert (response.status_code, response.json()["code"]) == (404, "not_found")


async def test_invalid_input_is_a_problem_document(api: TestClient, acme: Acme) -> None:
    response = api.post(
        "/api/v1/admin/departments",
        json={"slug": "Not A Slug", "name": "x"},
        headers=acme.as_("erin", write=True),
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "validation_error"


# --- Effects -------------------------------------------------------------------------------


async def test_department_membership_changes_are_audited_and_synced(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        engineering = (
            await session.scalars(select(Department).where(Department.slug == "engineering"))
        ).one()

    response = api.put(
        f"/api/v1/admin/departments/{engineering.id}/members/{acme.users['alice']}",
        json={"role": "manager"},
        headers=acme.as_("erin", write=True),
    )
    log = api.get(
        "/api/v1/admin/audit-events", params={"action": "admin."}, headers=acme.as_("amara.diallo")
    )
    verified = api.get("/api/v1/admin/audit-events/verify", headers=acme.as_("amara.diallo"))

    assert response.status_code == 200, response.text
    assert log.json()["items"][0]["action"] == "admin.department.member_set"
    assert log.json()["items"][0]["actor"]["display_name"] == "Erin Takahashi"
    assert verified.json()["ok"] is True
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        rows = (
            await session.scalars(select(AuthzOutbox).where(AuthzOutbox.status == "pending"))
        ).all()
    written = {(w["user"], w["relation"], w["object"]) for row in rows for w in row.writes}
    assert (f"user:{acme.users['alice']}", "manager", f"department:{engineering.id}") in written


async def test_people_list_includes_memberships_and_paginates(api: TestClient, acme: Acme) -> None:
    first = api.get("/api/v1/admin/users", params={"limit": 5}, headers=acme.as_("erin")).json()
    second = api.get(
        "/api/v1/admin/users",
        params={"limit": 5, "cursor": first["next_cursor"]},
        headers=acme.as_("erin"),
    ).json()
    alice = api.get("/api/v1/admin/users", params={"q": "alice"}, headers=acme.as_("erin")).json()[
        "items"
    ]

    assert len(first["items"]) == 5
    assert {u["id"] for u in first["items"]}.isdisjoint({u["id"] for u in second["items"]})
    assert [(d["slug"], d["role"]) for d in alice[0]["departments"]] == [("hr", "manager")]
    assert [g["slug"] for g in alice[0]["groups"]] == ["hr-records"]


def test_event_loop_is_not_shared() -> None:
    # TestClient runs the app on its own loop; this guards against accidental reuse.
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()

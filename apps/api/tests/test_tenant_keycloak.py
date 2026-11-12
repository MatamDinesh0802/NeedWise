"""Deleting a tenant's Keycloak organization (docs/multi-tenancy.md), against a scripted
Keycloak Admin API: the organization goes, and so do the users that belonged to no other
organization; a user shared with another organization (another tenant) stays."""

import json

import httpx

from workmind.modules.identity.lifecycle import delete_keycloak_organization
from workmind.seed.keycloak_admin import KeycloakAdmin


class FakeKeycloak:
    def __init__(self) -> None:
        self.orgs = {"org-a": "acme", "org-b": "globex"}
        self.members = {"org-a": ["u-only-a", "u-shared"], "org-b": ["u-shared", "u-only-b"]}
        self.users = {"u-only-a", "u-shared", "u-only-b", "u-linked"}
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append(f"{method} {path}")
        if path.startswith("/realms/master"):
            return httpx.Response(200, json={"access_token": "t"})
        rest = path.removeprefix("/admin/realms/workmind")
        if method == "GET" and rest == "/organizations":
            search = request.url.params.get("search", "")
            found = [{"id": i, "alias": a} for i, a in self.orgs.items() if search in a]
            return httpx.Response(200, json=found)
        if method == "GET" and rest.startswith("/organizations/members/"):
            user = rest.split("/")[3]
            mine = [{"id": o} for o, users in self.members.items() if user in users]
            return httpx.Response(200, json=mine)
        if method == "GET" and rest.endswith("/members"):
            org = rest.split("/")[2]
            first = int(request.url.params.get("first", "0"))
            page = [{"id": u} for u in self.members.get(org, [])][first : first + 100]
            return httpx.Response(200, json=page)
        if method == "DELETE" and rest.startswith("/users/"):
            user = rest.split("/")[2]
            if user not in self.users:
                return httpx.Response(404)
            self.users.discard(user)
            return httpx.Response(204)
        if method == "DELETE" and rest.startswith("/organizations/"):
            org = rest.split("/")[2]
            self.orgs.pop(org, None)
            self.members.pop(org, None)
            return httpx.Response(204)
        return httpx.Response(500, content=json.dumps({"unexpected": f"{method} {rest}"}))


async def _admin(fake: FakeKeycloak, http: httpx.AsyncClient) -> KeycloakAdmin:
    admin = KeycloakAdmin(http, "http://kc", "workmind")
    await admin.login("admin", "admin")
    return admin


async def test_the_organization_and_its_own_users_go_shared_users_stay() -> None:
    fake = FakeKeycloak()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        admin = await _admin(fake, http)
        # u-linked: a WorkMind user of the tenant whose Keycloak account is in no organization.
        deleted = await delete_keycloak_organization(admin, "acme", ["u-linked"])

    assert deleted == 2
    assert fake.users == {"u-shared", "u-only-b"}
    assert fake.orgs == {"org-b": "globex"}
    assert "DELETE /admin/realms/workmind/users/u-shared" not in fake.calls


async def test_a_tenant_without_an_organization_deletes_only_its_unshared_users() -> None:
    fake = FakeKeycloak()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        admin = await _admin(fake, http)
        assert await admin.find_organization("initech") is None
        deleted = await delete_keycloak_organization(admin, "initech", ["u-linked", "u-shared"])

    assert deleted == 1
    assert "u-shared" in fake.users
    assert set(fake.orgs) == {"org-a", "org-b"}

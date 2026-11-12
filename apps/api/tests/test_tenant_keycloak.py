"""Deleting a tenant's Keycloak organization (docs/multi-tenancy.md), against a scripted
Keycloak Admin API: the organization goes, and so do the users that belonged to no other
organization; a user shared with another organization (another tenant) stays.

The scripted `GET /organizations` behaves like Keycloak 26: `search` matches an organization's
name and domains, never its alias, and results come in pages of `first`/`max`."""

import json
from typing import Any, cast

import httpx
import pytest

from workmind.modules.identity.lifecycle import (
    Stores,
    TenantError,
    create_tenant,
    delete_keycloak_organization,
)
from workmind.seed.keycloak_admin import KeycloakAdmin


class FakeKeycloak:
    def __init__(self) -> None:
        self.orgs = {"org-a": "acme", "org-b": "globex"}
        # Names (and domains) that do not contain the alias: Keycloak's `search` misses them.
        self.names = {"org-a": "Acme Corp", "org-b": "Sec Review Corp"}
        self.domains = {"org-a": "acme.test", "org-b": "secreview.test"}
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
            search = request.url.params.get("search", "").lower()
            first = int(request.url.params.get("first", "0"))
            size = int(request.url.params.get("max", "10"))  # Keycloak's default page
            found = [
                {"id": i, "alias": a, "name": self.names.get(i, a)}
                for i, a in sorted(self.orgs.items())
                if search in self.names.get(i, a).lower() or search in self.domains.get(i, "")
            ]
            return httpx.Response(200, json=found[first : first + size])
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


async def test_the_organization_is_found_by_alias_even_when_its_name_does_not_contain_it() -> None:
    # Regression: `?search=<slug>` matches names and domains only, so "globex" named
    # "Sec Review Corp" was never found: deletion left the organization and its people in place,
    # verification reported nothing left, and a new tenant with the slug reused them.
    fake = FakeKeycloak()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        admin = await _admin(fake, http)
        assert await admin.find_organization("globex") == "org-b"
        deleted = await delete_keycloak_organization(admin, "globex", [])
        assert await admin.find_organization("globex") is None

    assert deleted == 1  # u-only-b; u-shared is still in acme
    assert set(fake.orgs) == {"org-a"}
    assert fake.users == {"u-only-a", "u-shared", "u-linked"}


async def test_the_lookup_pages_through_every_organization() -> None:
    fake = FakeKeycloak()
    for n in range(250):
        fake.orgs[f"org-{n:03}"] = f"filler-{n:03}"
    fake.orgs["org-zz"] = "zzsec"  # sorts after every filler: on the third page
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        admin = await _admin(fake, http)
        assert await admin.find_organization("zzsec") == "org-zz"
        assert await admin.find_organization("nobody") is None
        assert await admin.ensure_organization(alias="zzsec", name="New", domain="n.test") == (
            "org-zz"
        )


async def test_creating_a_tenant_refuses_a_slug_whose_organization_is_still_in_keycloak() -> None:
    # A leftover organization keeps its members: adopting it would let them into the new tenant.
    fake = FakeKeycloak()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        admin = await _admin(fake, http)
        unused = cast(Any, None)  # refused before any store is touched
        stores = Stores(unused, unused, unused, unused, unused, unused, keycloak=admin)
        with pytest.raises(TenantError, match="already has an organization"):
            await create_tenant(stores, slug="globex", name="New Customer Inc")

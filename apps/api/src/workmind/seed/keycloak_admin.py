"""The small slice of the Keycloak Admin REST API that seeding and the tenant lifecycle need
(`wm seed`, `wm tenant create|delete`). Every call is idempotent."""

from typing import Any

import httpx

_PAGE = 100


class KeycloakAdminError(RuntimeError):
    pass


class KeycloakAdmin:
    def __init__(self, http: httpx.AsyncClient, base_url: str, realm: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._realm = realm
        self._token: str | None = None

    async def login(self, username: str, password: str) -> None:
        response = await self._http.post(
            f"{self._base}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": username,
                "password": password,
            },
        )
        self._raise_for(response, "admin login")
        self._token = response.json()["access_token"]

    async def realm_exists(self) -> bool:
        response = await self._request("GET", "")
        return response.status_code == 200

    async def ensure_organization(self, *, alias: str, name: str, domain: str) -> str:
        if (found := await self.find_organization(alias)) is not None:
            return found
        response = await self._request(
            "POST",
            "/organizations",
            json={
                "name": name,
                "alias": alias,
                "enabled": True,
                "domains": [{"name": domain, "verified": False}],
            },
        )
        self._raise_for(response, f"create organization {alias}")
        return _id_from_location(response)

    async def ensure_user(
        self, *, email: str, first_name: str, last_name: str, password: str
    ) -> str:
        profile: dict[str, Any] = {
            "username": email,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": True,
            "emailVerified": True,
        }
        lookup = await self._request("GET", "/users", params={"email": email, "exact": "true"})
        found = lookup.json()
        if found:
            user_id = str(found[0]["id"])
            self._raise_for(await self._request("PUT", f"/users/{user_id}", json=profile), email)
        else:
            response = await self._request("POST", "/users", json=profile)
            self._raise_for(response, f"create user {email}")
            user_id = _id_from_location(response)
        response = await self._request(
            "PUT",
            f"/users/{user_id}/reset-password",
            json={"type": "password", "value": password, "temporary": False},
        )
        self._raise_for(response, f"set password for {email}")
        return user_id

    async def ensure_org_member(self, org_id: str, user_id: str) -> None:
        response = await self._request("POST", f"/organizations/{org_id}/members", json=user_id)
        if response.status_code != 409:  # 409: already a member
            self._raise_for(response, f"add {user_id} to organization")

    async def find_organization(self, alias: str) -> str | None:
        """The organization whose alias is exactly `alias`. Keycloak's `search` parameter
        matches names and domains, not aliases, so this pages through every organization:
        a missed lookup would leave a deleted tenant's organization, and its people, for the
        next tenant with the same slug."""
        first = 0
        while True:
            response = await self._request(
                "GET",
                "/organizations",
                params={"first": first, "max": _PAGE},
            )
            self._raise_for(response, f"find organization {alias}")
            page: list[dict[str, Any]] = response.json()
            for org in page:
                if org.get("alias") == alias:
                    return str(org["id"])
            if len(page) < _PAGE:
                return None
            first += len(page)

    async def organization_members(self, org_id: str) -> list[str]:
        members: list[str] = []
        while True:
            response = await self._request(
                "GET",
                f"/organizations/{org_id}/members",
                params={"first": len(members), "max": 100},
            )
            self._raise_for(response, "list organization members")
            page = [str(m["id"]) for m in response.json()]
            members += page
            if len(page) < 100:
                return members

    async def user_organizations(self, user_id: str) -> list[str]:
        response = await self._request("GET", f"/organizations/members/{user_id}/organizations")
        self._raise_for(response, f"organizations of {user_id}")
        return [str(o["id"]) for o in response.json()]

    async def delete_user(self, user_id: str) -> None:
        response = await self._request("DELETE", f"/users/{user_id}")
        if response.status_code != 404:
            self._raise_for(response, f"delete user {user_id}")

    async def delete_organization(self, org_id: str) -> None:
        response = await self._request("DELETE", f"/organizations/{org_id}")
        if response.status_code != 404:
            self._raise_for(response, f"delete organization {org_id}")

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self._token is None:
            raise KeycloakAdminError("call login() first")
        return await self._http.request(
            method,
            f"{self._base}/admin/realms/{self._realm}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            **kwargs,
        )

    @staticmethod
    def _raise_for(response: httpx.Response, action: str) -> None:
        if response.is_error:
            raise KeycloakAdminError(
                f"Keycloak {action} failed: HTTP {response.status_code} {response.text[:300]}"
            )


def _id_from_location(response: httpx.Response) -> str:
    return response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

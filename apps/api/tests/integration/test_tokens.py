"""Personal access tokens: shown once, stored hashed, scoped, revocable, expiring."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tests.integration.conftest import VALKEY_URL, Acme, SessionFactory, seed_acme
from workmind.core.tenancy import tenant_transaction
from workmind.modules.identity.models import ApiToken, User

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL to run")


def _create(api: TestClient, acme: Acme, who: str = "bob", **body: object) -> dict[str, object]:
    payload = {"name": "laptop MCP", "scopes": ["profile:read"], **body}
    response = api.post("/api/v1/me/tokens", json=payload, headers=acme.as_(who, write=True))
    assert response.status_code == 201, response.text
    return response.json()


def bearer(token: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_token_is_shown_once_and_stored_only_as_a_hash(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    created = _create(api, acme)
    token = str(created["token"])

    listed = api.get("/api/v1/me/tokens", headers=acme.as_("bob")).json()["items"]
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        row = (await session.scalars(select(ApiToken).where(ApiToken.name == "laptop MCP"))).one()

    assert token.startswith(f"wm_pat_{acme.slug}_")
    assert "token" not in listed[0]
    assert listed[0]["prefix"] == token[: len(f"wm_pat_{acme.slug}_") + 4]
    assert token.encode() not in row.token_hash
    assert token not in repr(vars(row))


async def test_a_token_acts_as_its_user_within_its_scopes(api: TestClient, acme: Acme) -> None:
    profile = _create(api, acme, scopes=["profile:read"])
    knowledge_only = _create(api, acme, name="search only", scopes=["knowledge:read"])

    me = api.get("/api/v1/me", headers=bearer(profile["token"]))
    denied = api.get("/api/v1/me", headers=bearer(knowledge_only["token"]))

    assert (me.status_code, me.json()["email"]) == (200, "bob@acme.test")
    assert (denied.status_code, denied.json()["code"]) == (403, "scope_required")


async def test_tokens_can_never_administer_or_mint_tokens(api: TestClient, acme: Acme) -> None:
    admin_token = _create(
        api, acme, who="erin", scopes=["profile:read", "knowledge:read", "assistant:ask"]
    )

    administer = api.get("/api/v1/admin/users", headers=bearer(admin_token["token"]))
    mint = api.post(
        "/api/v1/me/tokens",
        json={"name": "more", "scopes": ["profile:read"]},
        headers=bearer(admin_token["token"]),
    )

    assert (administer.status_code, administer.json()["code"]) == (403, "scope_required")
    assert (mint.status_code, mint.json()["code"]) == (403, "session_required")


async def test_revoked_expired_and_forged_tokens_are_rejected(
    api: TestClient, acme: Acme, owner_sessions: SessionFactory
) -> None:
    revoked, expired, good = (_create(api, acme, name=n) for n in ("revoked", "expired", "good"))
    assert (
        api.delete(
            f"/api/v1/me/tokens/{revoked['id']}", headers=acme.as_("bob", write=True)
        ).status_code
        == 204
    )
    async with tenant_transaction(owner_sessions, acme.tenant_id) as session:
        await session.execute(
            update(ApiToken)
            .where(ApiToken.name == "expired")
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    secret = str(good["token"]).removeprefix(f"wm_pat_{acme.slug}_")

    for token in (
        revoked["token"],
        expired["token"],
        f"wm_pat_{acme.slug}_{secret[:-1]}x",  # one character changed
        f"wm_pat_nonexistent_{secret}",  # wrong tenant
        "not-a-token",
    ):
        response = api.get("/api/v1/me", headers=bearer(token))
        assert response.status_code == 401, token
    assert api.get("/api/v1/me", headers=bearer(good["token"])).status_code == 200


async def test_another_tenants_slug_cannot_be_used_to_replay_a_token(
    api: TestClient, acme: Acme, owner_sessions: SessionFactory
) -> None:
    other = await seed_acme(owner_sessions)
    token = str(_create(api, acme)["token"]).replace(acme.slug, other.slug, 1)

    assert api.get("/api/v1/me", headers=bearer(token)).status_code == 401


async def test_disabling_the_user_disables_their_tokens(
    api: TestClient, acme: Acme, owner_sessions: SessionFactory
) -> None:
    token = _create(api, acme)["token"]
    async with tenant_transaction(owner_sessions, acme.tenant_id) as session:
        await session.execute(
            update(User).where(User.id == acme.users["bob"]).values(status="disabled")
        )

    assert api.get("/api/v1/me", headers=bearer(token)).status_code == 401


async def test_people_cannot_revoke_each_others_tokens(api: TestClient, acme: Acme) -> None:
    bobs = _create(api, acme)

    response = api.delete(f"/api/v1/me/tokens/{bobs['id']}", headers=acme.as_("alice", write=True))

    assert response.status_code == 404
    assert api.get("/api/v1/me", headers=bearer(bobs["token"])).status_code == 200


@pytest.mark.parametrize(
    "body",
    [{"scopes": []}, {"scopes": ["admin"]}, {"expires_in_days": 0}, {"expires_in_days": 366}],
    ids=["no-scopes", "unknown-scope", "zero-days", "over-a-year"],
)
async def test_invalid_token_requests_are_rejected(
    api: TestClient, acme: Acme, body: dict[str, object]
) -> None:
    payload = {"name": "x", "scopes": ["profile:read"], **body}
    response = api.post("/api/v1/me/tokens", json=payload, headers=acme.as_("bob", write=True))
    assert response.status_code == 422, response.text


async def test_token_lifecycle_is_audited(api: TestClient, acme: Acme) -> None:
    created = _create(api, acme)
    api.delete(f"/api/v1/me/tokens/{created['id']}", headers=acme.as_("bob", write=True))

    actions = [
        e["action"]
        for e in api.get(
            "/api/v1/admin/audit-events", params={"action": "token."}, headers=acme.as_("erin")
        ).json()["items"]
    ]
    assert actions == ["token.revoked", "token.created"]

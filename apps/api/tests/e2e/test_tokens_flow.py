import httpx

from tests.e2e.conftest import same_origin_headers, sign_in


def test_a_token_made_in_the_browser_works_for_an_mcp_style_client(browser: httpx.Client) -> None:
    sign_in(browser, "dan@acme.test")
    created = browser.post(
        "/api/v1/me/tokens",
        json={"name": "e2e client", "scopes": ["profile:read"], "expires_in_days": 1},
        headers=same_origin_headers(),
    )
    assert created.status_code == 201, created.text
    token = created.json()["token"]

    # A separate client with no cookies at all, as Claude Desktop or a script would be.
    with httpx.Client(base_url=str(browser.base_url)) as client:
        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        browser.delete(f"/api/v1/me/tokens/{created.json()['id']}", headers=same_origin_headers())
        after_revoke = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert (me.status_code, me.json()["email"]) == (200, "dan@acme.test")
    assert after_revoke.status_code == 401

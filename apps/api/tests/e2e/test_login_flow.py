import httpx

from tests.e2e.conftest import same_origin_headers, sign_in


def test_alice_signs_in_and_sees_her_hr_identity(browser: httpx.Client) -> None:
    callback = sign_in(browser, "alice@acme.test", return_to="/admin")

    assert callback.status_code == 303
    assert callback.headers["location"] == "/admin"
    assert "wm_session" in browser.cookies

    me = browser.get("/api/v1/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["email"] == "alice@acme.test"
    assert body["tenant"]["slug"] == "acme"
    assert [(d["slug"], d["role"]) for d in body["departments"]] == [("hr", "manager")]
    assert {g["slug"] for g in body["groups"]} == {"hr-records"}


def test_the_session_cookie_is_httponly_and_lax(browser: httpx.Client) -> None:
    callback = sign_in(browser, "bob@acme.test")

    cookie_header = next(
        v for k, v in callback.headers.multi_items() if k == "set-cookie" and "wm_session" in v
    )
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Path=/" in cookie_header


def test_the_contractor_is_a_guest_without_departments(browser: httpx.Client) -> None:
    sign_in(browser, "mallory@acme.test")

    body = browser.get("/api/v1/me").json()
    assert (body["org_role"], body["departments"], body["groups"]) == ("guest", [], [])


def test_wrong_password_never_creates_a_session(browser: httpx.Client) -> None:
    response = sign_in(browser, "alice@acme.test", password="not-the-password")

    assert response.status_code == 200  # Keycloak shows the form again
    assert "wm_session" not in browser.cookies
    assert browser.get("/api/v1/me").status_code == 401


def test_logout_requires_same_origin_and_then_ends_the_session(browser: httpx.Client) -> None:
    sign_in(browser, "carol@acme.test")

    cross_site = browser.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert cross_site.status_code == 403
    assert cross_site.json()["code"] == "csrf_origin"
    assert browser.get("/api/v1/me").status_code == 200  # still signed in

    logout = browser.post("/api/auth/logout", headers=same_origin_headers())
    assert logout.status_code == 200
    assert "/protocol/openid-connect/logout" in logout.json()["redirect_url"]
    assert browser.get("/api/v1/me").status_code == 401


def test_a_stolen_callback_url_is_useless_in_another_browser(browser: httpx.Client) -> None:
    # The flow cookie binds the callback to the browser that started the login.
    start = browser.get("/api/auth/login")
    state = httpx.URL(start.headers["location"]).params["state"]

    with httpx.Client(base_url=str(browser.base_url), follow_redirects=False) as attacker:
        response = attacker.get("/api/auth/callback", params={"code": "x", "state": state})

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login_expired"

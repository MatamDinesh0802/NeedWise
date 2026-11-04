"""End-to-end tests against the running stack, through Caddy, with real Keycloak logins.

Run with:  make up && make seed && make e2e
They are skipped unless E2E_BASE_URL is set (e.g. http://localhost:8480).
"""

import html
import os
import re
from collections.abc import Iterator
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "workmind-demo")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(pytest.mark.e2e)
            if not BASE_URL:
                item.add_marker(pytest.mark.skip(reason="set E2E_BASE_URL to run"))


class BrowserCookiePolicy(DefaultCookiePolicy):
    """Two differences between browsers and Python's cookie jar matter for Keycloak:
    browsers treat http://localhost as a secure context (Keycloak's cookies are `Secure`),
    and they ignore the obsolete `Version=1` attribute Keycloak still sends."""

    def return_ok_version(self, cookie: Cookie, request: Request) -> bool:
        return True

    def return_ok_secure(self, cookie: Cookie, request: Request) -> bool:
        host = urlsplit(request.get_full_url()).hostname
        return host in ("localhost", "127.0.0.1") or super().return_ok_secure(cookie, request)


@pytest.fixture
def browser() -> Iterator[httpx.Client]:
    """A client with browser-like cookie handling. httpx rebuilds a default-policy jar for
    every request, so a custom jar is attached through request/response hooks instead."""
    assert BASE_URL is not None
    jar = CookieJar(policy=BrowserCookiePolicy())

    def send_cookies(request: httpx.Request) -> None:
        request.headers.pop("cookie", None)
        jar.add_cookie_header(httpx.Cookies._CookieCompatRequest(request))  # pyright: ignore[reportPrivateUsage]

    def store_cookies(response: httpx.Response) -> None:
        jar.extract_cookies(
            httpx.Cookies._CookieCompatResponse(response),  # pyright: ignore[reportPrivateUsage, reportArgumentType]
            httpx.Cookies._CookieCompatRequest(response.request),  # pyright: ignore[reportPrivateUsage]
        )

    with httpx.Client(
        base_url=BASE_URL,
        follow_redirects=False,
        timeout=20,
        event_hooks={"request": [send_cookies], "response": [store_cookies]},
    ) as client:
        yield client


def same_origin_headers() -> dict[str, str]:
    assert BASE_URL is not None
    return {"Origin": BASE_URL, "X-CSRF-Protection": "1"}


def _login_form_action(page: httpx.Response) -> str | None:
    form = re.search(r'<form[^>]*\bid="kc-form-login"[^>]*>', page.text)
    action = re.search(r'\baction="([^"]+)"', form.group(0)) if form else None
    return html.unescape(action.group(1)) if action else None


def sign_in(
    client: httpx.Client, email: str, password: str = DEMO_PASSWORD, return_to: str = "/"
) -> httpx.Response:
    """Walk the real flow: WorkMind -> Keycloak -> callback. With Organizations enabled,
    Keycloak asks for the email first (to route to a company identity provider), then for
    the password. Returns the callback response, or Keycloak's page if sign-in failed."""
    start = client.get("/api/auth/login", params={"return_to": return_to})
    assert start.status_code == 302, start.text
    page = client.get(start.headers["location"])
    assert page.status_code == 200, page.text[:500]

    for _ in range(3):  # email page, then password page
        action = _login_form_action(page)
        assert action, "Keycloak login form not found"
        page = client.post(action, data={"username": email, "password": password})
        if page.status_code == 302:
            return client.get(page.headers["location"])
        if "kc-form-login" not in page.text or 'name="password"' not in page.text:
            break
        if "Invalid" in page.text:
            break
    return page  # still on a Keycloak page: wrong password, locked account, ...

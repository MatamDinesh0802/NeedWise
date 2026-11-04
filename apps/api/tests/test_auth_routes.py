import httpx
import pytest

from tests.conftest import ALL_HEALTHY, ClientFactory
from workmind.api.auth import safe_return_to
from workmind.modules.identity.oidc import OidcClient


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/admin", "/admin"),
        ("/assistant?c=1", "/assistant?c=1"),
        (None, "/"),
        ("", "/"),
        ("https://evil.example", "/"),
        ("//evil.example/path", "/"),
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
    ],
)
def test_return_to_only_allows_local_paths(value: str | None, expected: str) -> None:
    assert safe_return_to(value) == expected


def test_me_without_a_session_is_a_401_problem(make_client: ClientFactory) -> None:
    response = make_client(ALL_HEALTHY).get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "csrf_origin"),
        ({"Origin": "https://evil.example"}, "csrf_origin"),
        ({"Origin": "http://localhost:8480"}, "csrf_header"),
        ({"Referer": "https://evil.example/page", "X-CSRF-Protection": "1"}, "csrf_origin"),
    ],
    ids=["no-origin", "foreign-origin", "no-header", "foreign-referer"],
)
def test_logout_rejects_cross_site_requests(
    make_client: ClientFactory, headers: dict[str, str], code: str
) -> None:
    response = make_client(ALL_HEALTHY).post("/api/auth/logout", headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == code


def test_logout_without_a_session_just_returns_home(make_client: ClientFactory) -> None:
    response = make_client(ALL_HEALTHY).post(
        "/api/auth/logout",
        headers={"Origin": "http://localhost:8480", "X-CSRF-Protection": "1"},
    )

    assert response.status_code == 200
    assert response.json() == {"redirect_url": "http://localhost:8480/"}


def test_callback_without_the_browser_flow_cookie_is_rejected(make_client: ClientFactory) -> None:
    client = make_client(ALL_HEALTHY)

    response = client.get(
        "/api/auth/callback", params={"code": "c", "state": "s"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login_expired"


def test_login_redirects_with_a_friendly_error_when_keycloak_is_down(
    make_client: ClientFactory,
) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client = make_client(ALL_HEALTHY)
    client.app.state.resources.oidc = OidcClient(  # pyright: ignore[reportAttributeAccessIssue, reportFunctionMemberAccess]
        httpx.AsyncClient(transport=httpx.MockTransport(unreachable)),
        discovery_url="http://keycloak/.well-known/openid-configuration",
        client_id="workmind-api",
        client_secret="secret",
        redirect_uri="http://localhost:8480/api/auth/callback",
    )

    response = client.get("/api/auth/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login_unavailable"


def test_cancelled_login_is_reported_as_such(make_client: ClientFactory) -> None:
    response = make_client(ALL_HEALTHY).get(
        "/api/auth/callback", params={"error": "access_denied"}, follow_redirects=False
    )

    assert response.headers["location"] == "/?auth_error=login_cancelled"

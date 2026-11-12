"""The browser-like sign-in pieces and the token file, without a server."""

import stat
from http.cookiejar import Cookie
from pathlib import Path

import requests

from workmind_load.browser import browser_like, login_form_action, same_origin
from workmind_load.tokens import IssuedToken, TokenFile

KEYCLOAK_PAGE = """
<html><body>
<form id="kc-form-login" onsubmit="login.disabled = true; return true;"
      action="http://localhost:8480/auth/realms/workmind/login-actions/authenticate?session_code=a&amp;execution=b&amp;client_id=workmind-api"
      method="post">
  <input id="username" name="username" type="text">
</form>
</body></html>
"""


def test_the_login_form_action_is_found_and_unescaped() -> None:
    assert login_form_action(KEYCLOAK_PAGE) == (
        "http://localhost:8480/auth/realms/workmind/login-actions/authenticate"
        "?session_code=a&execution=b&client_id=workmind-api"
    )
    assert login_form_action("<html>no form</html>") is None


def secure_cookie(name: str) -> Cookie:
    return Cookie(
        version=1, name=name, value="v", port=None, port_specified=False,
        domain="localhost.local", domain_specified=False, domain_initial_dot=False,
        path="/auth", path_specified=True, secure=True, expires=None, discard=True,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    )  # fmt: skip


def test_secure_keycloak_cookies_go_back_to_http_localhost_like_a_browser() -> None:
    plain, browser = requests.Session(), browser_like(requests.Session())
    for session in (plain, browser):
        session.cookies.set_cookie(secure_cookie("AUTH_SESSION_ID"))
    request = requests.Request("POST", "http://localhost:8480/auth/realms/workmind/login")

    assert "Cookie" not in plain.prepare_request(request).headers  # what requests does alone
    assert browser.prepare_request(request).headers["Cookie"] == "AUTH_SESSION_ID=v"


def test_secure_cookies_still_need_https_elsewhere() -> None:
    session = browser_like(requests.Session())
    cookie = secure_cookie("AUTH_SESSION_ID")
    cookie.domain = "workmind.example"
    session.cookies.set_cookie(cookie)

    prepared = session.prepare_request(requests.Request("GET", "http://workmind.example/auth/x"))

    assert "Cookie" not in prepared.headers


def test_state_changing_requests_carry_the_origin_and_the_csrf_header() -> None:
    assert same_origin("http://localhost:8480/api") == {
        "Origin": "http://localhost:8480",
        "X-CSRF-Protection": "1",
    }


def test_the_token_file_is_private_and_never_shows_a_token(tmp_path: Path) -> None:
    path = tmp_path / ".secrets" / "tokens.json"
    token = IssuedToken("dan@acme.test", "id-1", "fake-token-for-the-test")

    TokenFile("http://localhost:8480", "loadtest-1", "2026-11-12", [token]).save(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert TokenFile.load(path).tokens == [token]
    assert "fake-token" not in repr(token)

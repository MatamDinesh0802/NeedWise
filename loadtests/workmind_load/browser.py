"""The sign-in a browser does: WorkMind -> Keycloak -> callback, ending with the session cookie
(ARCHITECTURE 6.2). Creating access tokens, uploading documents and the admin pages accept only
a browser session, so the load tests sign in like this for them.

With Keycloak Organizations, the login page asks for the email first, then the password.
Works with a plain `requests.Session` and with Locust's HttpSession (pass `names=True` so each
step is its own line in Locust's statistics).
"""

import html
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

SESSION_COOKIES = ("wm_session", "__Host-wm_session")
_FORM = re.compile(r'<form[^>]*\bid="kc-form-login"[^>]*>')
_ACTION = re.compile(r'\baction="([^"]+)"')


class SignInError(RuntimeError):
    pass


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})


def browser_like(session: requests.Session) -> requests.Session:
    """Two browser behaviours Keycloak relies on: http://localhost is a secure context (its
    login cookies are `Secure`), and an obsolete `Version=1` cookie attribute is ignored.

    requests copies the session's cookies into a fresh jar with the default policy for every
    request, so a custom policy would be lost: the cookies themselves are adjusted, only for
    plain-http requests to this machine, just before each request is prepared."""
    prepare = session.prepare_request

    def prepare_like_a_browser(request: requests.Request) -> requests.PreparedRequest:
        parts = urlsplit(request.url or "")
        if parts.scheme == "http" and parts.hostname in LOCAL_HOSTS:
            for cookie in session.cookies:
                cookie.secure = False
                cookie.version = 0
        return prepare(request)

    session.prepare_request = prepare_like_a_browser
    return session


def same_origin(base_url: str) -> dict[str, str]:
    """What a state-changing request from the web app carries (CSRF checks, ARCHITECTURE 6.3)."""
    parts = urlsplit(base_url)
    return {"Origin": f"{parts.scheme}://{parts.netloc}", "X-CSRF-Protection": "1"}


def login_form_action(page: str) -> str | None:
    form = _FORM.search(page)
    action = _ACTION.search(form.group(0)) if form else None
    return html.unescape(action.group(1)) if action else None


def has_session(session: requests.Session) -> bool:
    return any(c.name in SESSION_COOKIES for c in session.cookies)


def sign_in(
    session: requests.Session,
    base_url: str,
    email: str,
    password: str,
    *,
    names: bool = False,
    timeout: float = 30,
) -> None:
    """Walks the sign-in flow; raises SignInError unless it ends with a session cookie."""

    def call(method: str, url: str, step: str, **kwargs: Any) -> requests.Response:
        if names:
            kwargs["name"] = f"login: {step}"
        return session.request(
            method, urljoin(base_url, url), allow_redirects=False, timeout=timeout, **kwargs
        )

    start = call("GET", "/api/auth/login", "start", params={"return_to": "/"})
    if start.status_code != 302:
        raise SignInError(f"/api/auth/login answered {start.status_code}, not a redirect")
    page = call("GET", start.headers["location"], "keycloak page")
    for step in ("email", "password", "password again"):
        action = login_form_action(page.text)
        if action is None:
            raise SignInError(f"no Keycloak login form (HTTP {page.status_code})")
        page = call(
            "POST", action, f"submit {step}", data={"username": email, "password": password}
        )
        if page.status_code in (302, 303):
            callback = call("GET", page.headers["location"], "callback")
            if callback.status_code not in (302, 303) or not has_session(session):
                raise SignInError(f"the callback answered {callback.status_code}")
            return
        if "kc-form-login" not in page.text or 'name="password"' not in page.text:
            break
    raise SignInError(f"Keycloak did not accept the sign-in of {email}")


def sign_out(session: requests.Session, base_url: str, *, names: bool = False) -> None:
    kwargs: dict[str, Any] = {"name": "logout"} if names else {}
    session.post(
        urljoin(base_url, "/api/auth/logout"),
        headers=same_origin(base_url),
        allow_redirects=False,
        timeout=30,
        **kwargs,
    )
    session.cookies.clear()

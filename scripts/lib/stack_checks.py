#!/usr/bin/env python3
"""Checks through a running WorkMind's public URL, with real Keycloak sign-ins of the demo users
(password DEMO_PASSWORD, default workmind-demo). Standard library only (Python 3.9+), so the
same file drives an old release and the current one.

    stack_checks.py login  BASE EMAIL              exit 0 when the sign-in works
    stack_checks.py search BASE OUT                demo users' searches as JSON, to compare two
                                                   stacks (restore drills)
    stack_checks.py upgrade-before BASE STATE      on the old version: data to carry over
    stack_checks.py upgrade-after  BASE STATE      after the upgrade: it survived, everything works

Used by scripts/upgrade-test.sh (docs/operations/upgrade.md).
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import Any

PASSWORD = os.environ.get("DEMO_PASSWORD", "workmind-demo")
SEARCHES = [
    ("alice@acme.test", "How many weeks of parental leave do employees get?"),
    ("bob@acme.test", "How many days per week can I work from home?"),
    ("bob@acme.test", "What is the Fleet API rate limit?"),
    ("carol@acme.test", "For how many days in a row can I self-certify when I am sick?"),
    ("erin@acme.test", "What are the core collaboration hours?"),
    ("mallory@acme.test", "How many weeks of parental leave do employees get?"),
]
# A sentence no seeded document contains: only the upgrade test's own upload can match it.
MARKER = "The quokka maintenance window for upgrade drills is every second Tuesday at 07:40."


class Failed(Exception):
    pass


class BrowserCookiePolicy(DefaultCookiePolicy):
    """Browsers treat http://localhost as secure (Keycloak's cookies are Secure) and ignore the
    obsolete Version attribute Keycloak still sends."""

    def return_ok_version(self, cookie: Cookie, request: Any) -> bool:
        return True

    def return_ok_secure(self, cookie: Cookie, request: Any) -> bool:
        host = urllib.parse.urlsplit(request.get_full_url()).hostname
        return host in ("localhost", "127.0.0.1") or super().return_ok_secure(cookie, request)


class Client:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.jar = CookieJar(policy=BrowserCookiePolicy())
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _open(self, request: urllib.request.Request, timeout: float = 60) -> tuple[int, bytes]:
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    @staticmethod
    def request(url: str, **kwargs: Any) -> urllib.request.Request:
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise Failed(f"not an http(s) URL: {url}")
        return urllib.request.Request(url, **kwargs)  # noqa: S310 (checked just above)

    def sign_in(self, email: str) -> None:
        status, page = self._open(self.request(f"{self.base}/api/auth/login?return_to=/"))
        for _ in range(3):  # Organizations: the email first, then the password
            text = page.decode(errors="replace")
            form = re.search(r'<form[^>]*\bid="kc-form-login"[^>]*>', text)
            action = re.search(r'\baction="([^"]+)"', form.group(0)) if form else None
            if action is None:
                if self.signed_in:
                    return
                words = " ".join(re.sub(r"<[^>]+>", " ", text).split())[:200]
                raise Failed(f"sign-in of {email}: HTTP {status}: {words}")
            data = urllib.parse.urlencode({"username": email, "password": PASSWORD}).encode()
            status, page = self._open(self.request(html.unescape(action.group(1)), data=data))
            if self.signed_in:
                return
        raise Failed(f"sign-in of {email}: still on Keycloak (HTTP {status})")

    @property
    def signed_in(self) -> bool:
        return any(c.name in ("wm_session", "__Host-wm_session") for c in self.jar)

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        raw: bytes | None = None,
        content_type: str = "application/json",
        expect: tuple[int, ...] = (200, 201, 202, 204),
    ) -> Any:
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        headers = {"Accept": "application/json", "Origin": self.base, "X-CSRF-Protection": "1"}
        if data is not None:
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = self.request(f"{self.base}{path}", data=data, method=method, headers=headers)
        status, payload = self._open(request)
        if status not in expect:
            raise Failed(f"{method} {path}: HTTP {status} {payload[:300].decode(errors='replace')}")
        return json.loads(payload) if payload else None


def signed_in(base: str, email: str) -> Client:
    client = Client(base)
    client.sign_in(email)
    return client


def find_user(admin: Client, email: str) -> dict[str, Any]:
    page = admin.call("GET", f"/api/v1/admin/users?q={urllib.parse.quote(email)}")
    for user in page["items"]:
        if user["email"] == email:
            return user
    raise Failed(f"user {email} not found")


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.failures += 0 if ok else 1
        print(f"  [{'ok' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}", flush=True)


# --- Commands ------------------------------------------------------------------------------------


def login(base: str, email: str) -> int:
    signed_in(base, email)
    print(f"{email}: signed in")
    return 0


def search(base: str, out: str) -> int:
    results, clients = [], {}
    for email, question in SEARCHES:
        if email not in clients:
            clients[email] = signed_in(base, email)
        body = clients[email].call("POST", "/api/v1/search", {"query": question, "limit": 5})
        results.append(
            {
                "user": email,
                "question": question,
                "candidates": body["candidates"],
                "passages": [
                    [p["filename"], p["section"], p["document_id"], round(p["score"], 6)]
                    for p in body["passages"]
                ],
            }
        )
    Path(out).write_text(json.dumps(results, indent=2) + "\n")
    return 0


def upgrade_before(base: str, state_file: str) -> int:
    """Data a v0.1 user could create: a department and a group with a member, and a personal
    access token. Every later version has these endpoints too."""
    erin = signed_in(base, "erin@acme.test")
    bob_id = find_user(erin, "bob@acme.test")["id"]
    department = erin.call(
        "POST", "/api/v1/admin/departments", {"slug": "upgrade-drill", "name": "Upgrade drill"}
    )
    erin.call(
        "PUT", f"/api/v1/admin/departments/{department['id']}/members/{bob_id}", {"role": "member"}
    )
    group = erin.call(
        "POST",
        "/api/v1/admin/groups",
        {
            "slug": "upgrade-testers",
            "name": "Upgrade testers",
            "description": "kept across versions",
        },
    )
    erin.call("PUT", f"/api/v1/admin/groups/{group['id']}/members/users/{bob_id}")
    bob = signed_in(base, "bob@acme.test")
    token = bob.call(
        "POST",
        "/api/v1/me/tokens",
        {"name": "upgrade drill", "scopes": ["profile:read", "knowledge:read"]},
    )
    chain = erin.call("GET", "/api/v1/admin/audit-events/verify")
    state = {
        "version": erin.call("GET", "/api/v1/system/status")["version"],
        "bob_id": bob_id,
        "department_id": department["id"],
        "group_id": group["id"],
        "token": token["token"],  # a throwaway stack's credential, kept in the work directory
        "token_id": token["id"],
        "audit_checked": chain["checked"],
        "users": len(erin.call("GET", "/api/v1/admin/users?limit=100")["items"]),
    }
    Path(state_file).write_text(json.dumps(state, indent=2) + "\n")
    print(
        f"  created on {state['version']}: department upgrade-drill and group upgrade-testers "
        f"(member bob), bob's access token; audit chain ok over {chain['checked']} events"
    )
    return 0


def upgrade_after(base: str, state_file: str) -> int:
    state = json.loads(Path(state_file).read_text())
    report = Report()
    # Each dependency gets 2 s to answer; right after a start, on a busy machine, Keycloak can
    # take longer for a while.
    deadline = time.monotonic() + 90
    while True:
        status = Client(base).call("GET", "/api/v1/system/status")
        if status["status"] == "ok" or time.monotonic() > deadline:
            break
        time.sleep(5)
    report.check(
        "system status",
        status["status"] == "ok",
        ", ".join(f"{c['name']} {c['status']}" for c in status["components"]),
    )

    clients: dict[str, Client] = {}
    for email in ("erin@acme.test", "bob@acme.test", "alice@acme.test", "mallory@acme.test"):
        try:
            clients[email] = signed_in(base, email)
            report.check(f"sign-in {email}", True)
        except Failed as exc:
            report.check(f"sign-in {email}", False, str(exc))
    erin, bob = clients["erin@acme.test"], clients["bob@acme.test"]

    me = Client(base, token=state["token"]).call("GET", "/api/v1/me")
    report.check("bob's access token from the old version", me["email"] == "bob@acme.test")
    departments = {d["id"]: d for d in erin.call("GET", "/api/v1/admin/departments")["items"]}
    members = erin.call("GET", f"/api/v1/admin/departments/{state['department_id']}/members")
    report.check(
        "department and membership kept",
        state["department_id"] in departments
        and any(m["user_id"] == state["bob_id"] for m in members["items"]),
    )
    groups = {g["id"] for g in erin.call("GET", "/api/v1/admin/groups")["items"]}
    group_members = erin.call("GET", f"/api/v1/admin/groups/{state['group_id']}/members")
    report.check(
        "group and membership kept",
        state["group_id"] in groups and "bob@acme.test" in json.dumps(group_members),
    )
    chain = erin.call("GET", "/api/v1/admin/audit-events/verify")
    report.check(
        "audit hash chain",
        chain["ok"] and chain["checked"] >= state["audit_checked"],
        f"{chain['checked']} events (were {state['audit_checked']})",
    )
    edition = erin.call("GET", "/api/v1/edition")
    report.check("edition endpoint (new)", edition["edition"] == "community", edition["edition"])
    today = datetime.now(timezone.utc).date()
    usage = erin.call("GET", f"/api/v1/usage/summary?from={today - timedelta(days=30)}&to={today}")
    report.check("usage summary (new)", "budgets" in usage, ", ".join(sorted(usage)))

    # Knowledge, new since v0.1: a collection for the department made in the old version.
    collection = erin.call(
        "POST",
        "/api/v1/admin/collections",
        {
            "key": "upgrade-drill",
            "name": "Upgrade drill",
            "scope": "department",
            "department_id": state["department_id"],
            "editors": [f"user:{state['bob_id']}"],
        },
    )
    uploaded = bob.call(
        "POST",
        f"/api/v1/collections/{collection['id']}/documents?filename=upgrade-drill.md",
        raw=f"# Upgrade drill\n\n{MARKER}\n".encode(),
        content_type="text/markdown",
    )
    document_id = uploaded["document"]["id"]
    deadline, document = time.monotonic() + 240, {"status": "pending"}
    while document["status"] in ("pending", "processing") and time.monotonic() < deadline:
        time.sleep(2)
        document = bob.call("GET", f"/api/v1/documents/{document_id}")
    report.check("upload and ingestion (new)", document["status"] == "ready", document["status"])

    question = {"query": "When is the quokka maintenance window?", "limit": 5}
    with_token = Client(base, token=state["token"]).call("POST", "/api/v1/search", question)
    found = [p["document_id"] for p in with_token["passages"]]
    report.check(
        "search with the old token finds it",
        bool(found) and found[0] == document_id,
        f"{len(found)} passages",
    )
    for email in ("alice@acme.test", "mallory@acme.test"):
        if email in clients:
            other = clients[email].call("POST", "/api/v1/search", question)
            leaked = [p for p in other["passages"] if p["document_id"] == document_id]
            report.check(f"permission gate: {email} does not see it", not leaked)
    clients_collections = clients["alice@acme.test"].call("GET", "/api/v1/collections")
    report.check(
        "permission gate: alice is not shown the collection",
        all(c["id"] != collection["id"] for c in clients_collections["items"]),
    )
    conversation = bob.call("POST", "/api/v1/conversations", {"title": "After the upgrade"})
    listed = bob.call("GET", "/api/v1/conversations")
    report.check(
        "conversations (new)",
        any(c["id"] == conversation["id"] for c in listed["items"]),
    )
    print(f"  {report.failures} failed")
    return 1 if report.failures else 0


def main() -> int:
    command, *args = sys.argv[1:] or ["-h"]
    handlers = {
        "login": login,
        "search": search,
        "upgrade-before": upgrade_before,
        "upgrade-after": upgrade_after,
    }
    if command not in handlers or len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 0 if command in ("-h", "--help") else 2
    try:
        return handlers[command](*args)
    except Failed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

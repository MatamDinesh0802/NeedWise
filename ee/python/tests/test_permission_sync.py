"""Permission sync reads each source's ACLs as the source shows them (fakes of the Drive, Slack
and GitHub APIs behind httpx.MockTransport). What happens to those ACLs (mapping, enforcement)
is tested in apps/api/tests/integration/test_permission_sync.py."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

pytest.importorskip("needwise_ee", reason="install the ee extra: uv sync --extra ee")

from needwise.modules.connectors.base import ConnectorError, RemoteItem
from needwise.modules.connectors.extensions import ItemAcl
from needwise.modules.connectors.gdrive import GoogleDriveConnector
from needwise.modules.connectors.github import GitHubConnector
from needwise.modules.connectors.models import Source
from needwise.modules.connectors.slack import SlackConnector
from needwise.modules.connectors.web import WebConnector

from needwise_ee.permission_sync import (
    SourcePermissions,
    drive_acls,
    drive_principal,
    github_acls,
    slack_acls,
)

Routes = Callable[[httpx.Request], httpx.Response]


def client(routes: Routes, seen: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return routes(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


# --- Google Drive -------------------------------------------------------------------------------

PERMISSIONS: dict[str, list[dict[str, Any]]] = {
    "shared": [
        {"type": "user", "role": "owner", "emailAddress": "Wei.Zhang@Acme.test"},
        {"type": "user", "role": "commenter", "emailAddress": "marcus.bell@acme.test"},
        {"type": "group", "role": "reader", "emailAddress": "leadership@acme.test"},
        {"type": "domain", "role": "reader", "domain": "Partner.example"},
        {"type": "user", "role": "writer", "emailAddress": "gone@acme.test", "deleted": True},
    ],
    "public": [
        {"type": "user", "role": "owner", "emailAddress": "alice@acme.test"},
        {"type": "anyone", "role": "reader"},
    ],
}


def drive_routes(request: httpx.Request) -> httpx.Response:
    parts = request.url.path.split("/")
    if request.url.path.endswith("/permissions") and parts[-2] in PERMISSIONS:
        permissions = PERMISSIONS[parts[-2]]
        if parts[-2] == "shared" and not request.url.params.get("pageToken"):
            # Two pages, to show they are all read.
            return httpx.Response(200, json={"permissions": permissions[:2], "nextPageToken": "p2"})
        if parts[-2] == "shared":
            return httpx.Response(200, json={"permissions": permissions[2:]})
        return httpx.Response(200, json={"permissions": permissions})
    if request.url.path.endswith("/permissions") and parts[-2] == "hidden":
        return httpx.Response(403, json={"error": {"message": "insufficientFilePermissions"}})
    if request.url.path.endswith("/permissions") and parts[-2] == "slow":
        return httpx.Response(429)
    return httpx.Response(404)


def drive(routes: Routes = drive_routes) -> GoogleDriveConnector:
    return GoogleDriveConnector(
        client(routes),
        folder_id="root",
        credentials={"access_token": "t", "expires_at": "2999-01-01T00:00:00+00:00"},
        client_id="id",
        client_secret="secret",
    )


def test_drive_permissions_become_source_principals() -> None:
    assert drive_principal({"type": "user", "emailAddress": "A@B.test"}) == "email:a@b.test"
    assert drive_principal({"type": "group", "emailAddress": "g@b.test"}) == "group:g@b.test"
    assert drive_principal({"type": "domain", "domain": "B.test"}) == "domain:b.test"
    assert drive_principal({"type": "anyone"}) == "anyone"
    assert drive_principal({"type": "user", "emailAddress": "x@b.test", "deleted": True}) is None
    assert drive_principal({"type": "user"}) is None


async def test_every_page_of_a_files_permissions_is_read() -> None:
    acls = await drive_acls(drive(), ["shared", "public"])

    assert acls["shared"] == ItemAcl(
        principals=(
            "domain:partner.example",
            "email:marcus.bell@acme.test",
            "email:wei.zhang@acme.test",
            "group:leadership@acme.test",
        )
    )
    assert acls["public"] == ItemAcl(principals=("anyone", "email:alice@acme.test"))


async def test_a_file_whose_sharing_is_refused_is_for_nobody() -> None:
    acls = await drive_acls(drive(), ["hidden"])

    assert acls["hidden"].principals == ()
    assert acls["hidden"].restricted
    assert acls["hidden"].error
    assert "refused" in acls["hidden"].error


async def test_a_drive_outage_fails_the_sync_instead_of_guessing() -> None:
    with pytest.raises(ConnectorError):
        await drive_acls(drive(), ["slow"])


# --- Slack --------------------------------------------------------------------------------------

CHANNELS: dict[str, dict[str, Any]] = {
    "CPUB": {"id": "CPUB", "name": "general", "is_private": False},
    "CPRIV": {"id": "CPRIV", "name": "incident-42", "is_private": True},
}
MEMBERS = {"CPRIV": ["U1", "U2", "U3", "UBOT", "UOLD"]}
USERS = [
    {"id": "U1", "profile": {"email": "Wei.Zhang@acme.test"}},
    {"id": "U2", "profile": {"email": "marcus.bell@acme.test"}},
    {"id": "U3", "profile": {}},  # the address is hidden: needs a mapping
    {"id": "UBOT", "is_bot": True, "profile": {}},
    {"id": "UOLD", "deleted": True, "profile": {"email": "old@acme.test"}},
]


def slack_routes(request: httpx.Request) -> httpx.Response:
    method = request.url.path.rsplit("/", 1)[-1]
    channel = request.url.params.get("channel", "")
    if method == "conversations.info":
        if channel not in CHANNELS:
            return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
        return httpx.Response(200, json={"ok": True, "channel": CHANNELS[channel]})
    if method == "conversations.members":
        if not request.url.params.get("cursor"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "members": MEMBERS[channel][:2],
                    "response_metadata": {"next_cursor": "c2"},
                },
            )
        return httpx.Response(200, json={"ok": True, "members": MEMBERS[channel][2:]})
    if method == "users.list":
        return httpx.Response(200, json={"ok": True, "members": USERS})
    return httpx.Response(404)


async def test_a_private_channel_is_for_its_members_in_every_month() -> None:
    connector = SlackConnector(client(slack_routes), token="xoxb-t", channels=["CPRIV", "CPUB"])

    acls = await slack_acls(
        connector,
        ["CPRIV:2026-11", "CPUB:2026-11", "CPRIV:2026-01"],  # January: synced before
    )

    members = ItemAcl(
        principals=("email:marcus.bell@acme.test", "email:wei.zhang@acme.test", "slack-user:u3")
    )
    assert acls["CPRIV:2026-11"] == members
    assert acls["CPRIV:2026-01"] == members  # an older month the scan no longer lists
    assert acls["CPUB:2026-11"] == ItemAcl(restricted=False)  # the workspace: the collection


async def test_a_channel_the_bot_cannot_see_any_more_is_for_nobody() -> None:
    connector = SlackConnector(client(slack_routes), token="xoxb-t", channels=["CGONE"])

    acls = await slack_acls(connector, ["CGONE:2026-10"])

    assert acls["CGONE:2026-10"].principals == ()
    assert acls["CGONE:2026-10"].error
    assert "channel_not_found" in acls["CGONE:2026-10"].error


# --- GitHub -------------------------------------------------------------------------------------

REPOS: dict[str, dict[str, Any]] = {
    "acme/handbook": {"visibility": "public", "private": False},
    "acme/platform": {"visibility": "private", "private": True},
    "acme/inner": {"visibility": "internal", "private": True},
    "acme/locked": {"visibility": "private", "private": True},
}


def github_routes(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    for repo, info in REPOS.items():
        if path == f"/repos/{repo}":
            return httpx.Response(200, json=info)
        if path == f"/repos/{repo}/collaborators":
            if repo == "acme/locked":
                return httpx.Response(403, json={"message": "Must have push access"})
            assert request.url.params.get("affiliation") == "all"
            page = int(request.url.params.get("page", "1"))
            logins = [f"dev{n}" for n in range(150)] if repo == "acme/platform" else ["Wei-Z"]
            chunk = logins[(page - 1) * 100 : page * 100]
            return httpx.Response(200, json=[{"login": login} for login in chunk])
        if path == f"/repos/{repo}/teams":
            return httpx.Response(200, json=[{"slug": "Platform"}])
    return httpx.Response(404)


def github(repo: str) -> GitHubConnector:
    return GitHubConnector(client(github_routes), repo=repo, token="ghp_t")


async def test_a_public_repository_keeps_the_collections_access() -> None:
    acls = await github_acls(github("acme/handbook"), ["README.md", "docs/a.md"])
    assert set(acls.values()) == {ItemAcl(restricted=False)}


async def test_a_private_repository_is_for_its_collaborators_and_teams() -> None:
    acls = await github_acls(github("acme/platform"), ["README.md", "docs/a.md"])

    acl = acls["README.md"]
    assert acls["docs/a.md"] == acl
    assert len([p for p in acl.principals if p.startswith("github-user:")]) == 150  # two pages
    assert "github-team:acme/platform" in acl.principals
    assert not any(p.startswith("github-org:") for p in acl.principals)


async def test_an_internal_repository_adds_its_organization() -> None:
    acls = await github_acls(github("acme/inner"), ["README.md"])
    assert acls["README.md"].principals == (
        "github-org:acme",
        "github-team:acme/platform",
        "github-user:wei-z",
    )


async def test_collaborators_the_token_may_not_list_mean_nobody() -> None:
    acls = await github_acls(github("acme/locked"), ["README.md"])
    assert acls["README.md"].principals == ()
    assert acls["README.md"].error


# --- The hook -----------------------------------------------------------------------------------


def _source(kind: str) -> Source:
    return Source(kind=kind, key=kind, name=kind, config={})


async def test_the_hook_covers_drive_slack_and_github_but_not_the_web() -> None:
    permissions = SourcePermissions()
    assert [
        k for k in ("gdrive", "slack", "github", "web") if permissions.supports(_source(k))
    ] == [
        "gdrive",
        "slack",
        "github",
    ]
    web = WebConnector(client(lambda _r: httpx.Response(404)), start_urls=["https://x.test/"])
    assert await permissions.acls(_source("web"), web, [], []) == {}

    item = RemoteItem("shared", "Shared", "shared.docx", "1")
    acls = await permissions.acls(_source("gdrive"), drive(), [item], ["older"])
    assert set(acls) == {"shared"}  # Drive lists every file: known ones are deleted or rescanned

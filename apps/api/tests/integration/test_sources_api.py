"""Sources over real HTTP, with the real web crawler against a real local web server: create,
write-only credentials, sync into a collection, follow changes and deletions on the site. Also
the connector catalogue (`/kinds`) and the Atlassian and Microsoft OAuth flows, with their
token endpoints answered as their documentation describes.

Needs TEST_QDRANT_URL and TEST_VALKEY_URL (and the test database)."""

import asyncio
import base64
import functools
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import VALKEY_URL, Acme, Database
from workmind.core.config import Settings
from workmind.main import create_app
from workmind.modules.retrieval.qdrant import QdrantClient

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL), reason="set TEST_QDRANT_URL and TEST_VALKEY_URL"
)


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def site(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    root = tmp_path / "www"
    (root / "intranet").mkdir(parents=True)
    pages = root / "intranet"
    (pages / "index.html").write_text(
        "<html><title>Intranet</title>"
        "<a href='leave.html'>Leave</a> <a href='travel.html'>Travel</a></html>"
    )
    (pages / "leave.html").write_text(
        "<html><title>Leave policy</title><h1>Leave</h1><p>Parental leave is 16 weeks.</p></html>"
    )
    (pages / "travel.html").write_text(
        "<html><title>Travel policy</title><h1>Travel</h1>"
        "<p>Hotels are capped at 250 euros.</p></html>"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/intranet/", pages
    server.shutdown()


@pytest.fixture
def api(database: Database, tmp_path: Path) -> Iterator[TestClient]:
    model = f"test_{uuid.uuid4().hex[:10]}"
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        qdrant_url=QDRANT_URL or "",
        authz_outbox_worker=False,
        ingestion_mode="inline",
        embedding_provider="hash",
        embedding_model=model,
        embedding_dimensions=64,
        object_storage_path=str(tmp_path / "objects"),
        atlassian_client_id="atlassian-client",
        atlassian_client_secret="atlassian-secret",
        microsoft_client_id="microsoft-client",
        microsoft_client_secret="microsoft-secret",
        microsoft_tenant_id="contoso.onmicrosoft.com",
    )
    with TestClient(create_app(settings)) as client:
        yield client

    async def drop() -> None:
        async with httpx.AsyncClient() as http:
            qdrant = QdrantClient(http, QDRANT_URL or "")
            if await qdrant.collection_exists(f"chunks_{model}_64"):
                await qdrant.delete_collection(f"chunks_{model}_64")

    asyncio.run(drop())


def wait_for_sync(api: TestClient, acme: Acme, key: str, after: str | None) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        source = next(
            s
            for s in api.get("/api/v1/admin/sources", headers=acme.as_("erin")).json()
            if s["key"] == key
        )
        if source["status"] in ("ok", "error") and source["last_sync_at"] != after:
            return source
        time.sleep(0.2)
    raise AssertionError("the sync did not finish")


def sync(api: TestClient, acme: Acme, key: str) -> dict[str, Any]:
    before = next(
        s
        for s in api.get("/api/v1/admin/sources", headers=acme.as_("erin")).json()
        if s["key"] == key
    )
    assert (
        api.post(
            f"/api/v1/admin/sources/{key}/sync", headers=acme.as_("erin", write=True)
        ).status_code
        == 202
    )
    return wait_for_sync(api, acme, key, before["last_sync_at"])


def titles(api: TestClient, acme: Acme, collection_id: str) -> dict[str, str]:
    """The synced documents by title, once their ingestion has finished."""
    deadline = time.monotonic() + 30
    while True:
        docs = api.get(
            f"/api/v1/collections/{collection_id}/documents", headers=acme.as_("bob")
        ).json()["items"]
        mine = {d["title"]: d["status"] for d in docs if d["filename"].startswith("intranet")}
        if all(s in ("ready", "failed") for s in mine.values()) or time.monotonic() > deadline:
            return mine
        time.sleep(0.2)


async def test_a_web_source_syncs_follows_changes_and_deletions(
    api: TestClient, acme: Acme, site: tuple[str, Path]
) -> None:
    url, pages = site
    handbook = next(
        c
        for c in api.get("/api/v1/collections", headers=acme.as_("erin")).json()["items"]
        if c["key"] == "company-handbook"
    )
    created = api.post(
        "/api/v1/admin/sources",
        json={
            "key": "intranet",
            "name": "Intranet",
            "kind": "web",
            "collection": "company-handbook",
            "config": {"start_urls": [url]},
            "credentials": {"cookie": "session=abc"},
        },
        headers=acme.as_("erin", write=True),
    )
    assert created.status_code == 201, created.text
    assert created.json()["has_credentials"] is True
    assert "cookie" not in created.text  # credentials are write-only
    assert "abc" not in created.text

    first = sync(api, acme, "intranet")
    after_first = titles(api, acme, handbook["id"])
    (pages / "leave.html").write_text(
        "<html><title>Leave policy</title><h1>Leave</h1><p>Parental leave is 18 weeks.</p></html>"
    )
    (pages / "travel.html").unlink()
    (pages / "index.html").write_text(
        "<html><title>Intranet</title><a href='leave.html'>Leave</a></html>"
    )
    second = sync(api, acme, "intranet")

    assert first["status"] == "ok", first["last_error"]
    assert first["stats"]["added"] == 3
    assert after_first == {"Intranet": "ready", "Leave policy": "ready", "Travel policy": "ready"}
    assert (second["stats"]["updated"], second["stats"]["deleted"]) == (2, 1)
    assert set(titles(api, acme, handbook["id"])) == {"Intranet", "Leave policy"}
    assert second["documents"] == 2


async def test_sources_are_for_admins_and_configs_are_validated(
    api: TestClient, acme: Acme
) -> None:
    body = {
        "key": "bad",
        "name": "Bad",
        "kind": "github",
        "collection": "company-handbook",
        "config": {"repo": "no-slash"},
    }

    invalid = api.post("/api/v1/admin/sources", json=body, headers=acme.as_("erin", write=True))
    as_member = api.get("/api/v1/admin/sources", headers=acme.as_("bob"))
    unknown = api.post(
        "/api/v1/admin/sources",
        json={**body, "config": {"repo": "acme/docs"}, "collection": "nope"},
        headers=acme.as_("erin", write=True),
    )

    assert (invalid.status_code, invalid.json()["code"]) == (422, "invalid_config")
    assert as_member.status_code == 403
    assert (unknown.status_code, unknown.json()["code"]) == (422, "unknown_collection")


def create(api: TestClient, acme: Acme, body: dict[str, Any]) -> Any:
    return api.post(
        "/api/v1/admin/sources",
        json={"collection": "company-handbook", **body},
        headers=acme.as_("erin", write=True),
    )


def provider_answers(
    api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    handle: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    """The identity provider, as the API's outgoing HTTP client sees it."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handle(request)

    fake = httpx.AsyncClient(transport=httpx.MockTransport(record))
    app: Any = api.app
    http = app.state.resources.http
    monkeypatch.setattr(http, "post", fake.post)
    monkeypatch.setattr(http, "get", fake.get)
    return seen


def start(api: TestClient, acme: Acme, key: str, provider: str) -> dict[str, list[str]]:
    response = api.get(
        f"/api/v1/admin/sources/{key}/oauth/{provider}/start",
        headers=acme.as_("erin"),
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    return {"_url": [location.split("?")[0]], **parse_qs(urlsplit(location).query)}


def source_named(api: TestClient, acme: Acme, key: str) -> dict[str, Any]:
    return next(
        s
        for s in api.get("/api/v1/admin/sources", headers=acme.as_("erin")).json()
        if s["key"] == key
    )


async def test_the_connector_catalogue_describes_every_kind(api: TestClient, acme: Acme) -> None:
    kinds = {
        k["kind"]: k
        for k in api.get("/api/v1/admin/sources/kinds", headers=acme.as_("erin")).json()
    }
    as_member = api.get("/api/v1/admin/sources/kinds", headers=acme.as_("bob"))

    assert set(kinds) == {"web", "github", "slack", "gdrive", "confluence", "sharepoint"}
    confluence, sharepoint = kinds["confluence"], kinds["sharepoint"]
    assert (confluence["label"], confluence["auth"]) == ("Confluence", "oauth_or_token")
    assert [c["name"] for c in confluence["credentials"]] == ["email", "api_token"]
    assert confluence["oauth"] == {
        "provider": "atlassian",
        "start_path": "/api/v1/admin/sources/{key}/oauth/atlassian/start",
        "redirect_uri": "http://localhost:8480/api/v1/oauth/atlassian/callback",
        "configured": True,
    }
    assert set(confluence["config_schema"]["properties"]) == {
        "site_url",
        "spaces",
        "include_attachments",
    }
    assert (sharepoint["auth"], sharepoint["oauth"]["provider"]) == ("oauth", "microsoft")
    assert set(sharepoint["config_schema"]["properties"]) == {"site_url", "library", "folder"}
    assert kinds["gdrive"]["oauth"]["configured"] is False  # no GOOGLE_CLIENT_ID here
    assert kinds["web"]["oauth"] is None
    assert as_member.status_code == 403


async def test_a_confluence_source_with_an_api_token_keeps_it_write_only(
    api: TestClient, acme: Acme
) -> None:
    created = create(
        api,
        acme,
        {
            "key": "wiki-hr",
            "name": "HR wiki",
            "kind": "confluence",
            "config": {"site_url": "https://acme.atlassian.net/wiki", "spaces": ["HR"]},
            "credentials": {"email": "sync@acme.test", "api_token": "ATATT-secret-value"},
            "enabled": False,
        },
    )
    elsewhere = create(
        api,
        acme,
        {
            "key": "wiki-bad",
            "name": "Bad",
            "kind": "confluence",
            "config": {"site_url": "https://wiki.internal.example", "spaces": ["HR"]},
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["config"] == {
        "site_url": "https://acme.atlassian.net",
        "spaces": ["HR"],
        "include_attachments": True,
    }
    assert (body["has_credentials"], body["needs"]) == (True, ["email", "api_token"])
    assert "ATATT" not in created.text
    assert (elsewhere.status_code, elsewhere.json()["code"]) == (422, "invalid_config")


async def test_microsoft_oauth_connects_a_sharepoint_source_with_pkce(
    api: TestClient, acme: Acme, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        create(
            api,
            acme,
            {
                "key": "finance-docs",
                "name": "Finance documents",
                "kind": "sharepoint",
                "config": {"site_url": "https://contoso.sharepoint.com/sites/finance"},
                "enabled": False,
            },
        ).status_code
        == 201
    )
    seen = provider_answers(
        api,
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "scope": "Sites.Read.All",
                "expires_in": 3599,
                "access_token": "ms-access",
                "refresh_token": "ms-refresh",
            },
        ),
    )

    query = start(api, acme, "finance-docs", "microsoft")
    done = api.get(
        "/api/v1/oauth/microsoft/callback",
        params={"code": "auth-code", "state": query["state"][0]},
        headers=acme.as_("erin"),
        follow_redirects=False,
    )
    replayed = api.get(
        "/api/v1/oauth/microsoft/callback",
        params={"code": "auth-code", "state": query["state"][0]},
        headers=acme.as_("erin"),
        follow_redirects=False,
    )

    assert query["_url"] == [
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/authorize"
    ]
    assert query["scope"] == ["offline_access Sites.Read.All"]  # the least the site needs
    assert query["redirect_uri"] == ["http://localhost:8480/api/v1/oauth/microsoft/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert (done.status_code, done.headers["location"]) == (
        303,
        "/admin/sources?oauth=connected&source=finance-docs",
    )
    form = parse_qs(seen[0].content.decode())
    assert str(seen[0].url) == (
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/token"
    )
    verifier = form["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert query["code_challenge"] == [challenge.decode()]
    assert (form["grant_type"], form["client_secret"]) == (
        ["authorization_code"],
        ["microsoft-secret"],
    )
    listed = source_named(api, acme, "finance-docs")
    assert listed["has_credentials"] is True
    assert "ms-refresh" not in json.dumps(listed)
    assert replayed.headers["location"] == "/admin/sources?oauth=failed"  # one use per state


async def test_atlassian_oauth_finds_the_configured_site_among_the_granted_ones(
    api: TestClient, acme: Acme, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, site in (
        ("wiki-eng", "https://acme.atlassian.net"),
        ("wiki-x", "https://x.atlassian.net"),
    ):
        assert (
            create(
                api,
                acme,
                {
                    "key": key,
                    "name": key,
                    "kind": "confluence",
                    "config": {"site_url": site, "spaces": ["ENG"]},
                    "enabled": False,
                },
            ).status_code
            == 201
        )

    def atlassian(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "at-access",
                    "refresh_token": "at-refresh",
                    "expires_in": 3600,
                    "scope": "read:page:confluence offline_access",
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(  # GET /oauth/token/accessible-resources
            200,
            json=[
                {
                    "id": "1324a887-45db-1bf4-1e99-ef0ff456d421",
                    "name": "acme",
                    "url": "https://acme.atlassian.net",
                    "scopes": ["read:page:confluence"],
                    "avatarUrl": "https://site-admin-avatar-cdn.prod.public.atl-paas.net/x.png",
                }
            ],
        )

    seen = provider_answers(api, monkeypatch, atlassian)
    query = start(api, acme, "wiki-eng", "atlassian")
    done = api.get(
        "/api/v1/oauth/atlassian/callback",
        params={"code": "c", "state": query["state"][0]},
        headers=acme.as_("erin"),
        follow_redirects=False,
    )
    other = start(api, acme, "wiki-x", "atlassian")
    not_granted = api.get(
        "/api/v1/oauth/atlassian/callback",
        params={"code": "c", "state": other["state"][0]},
        headers=acme.as_("erin"),
        follow_redirects=False,
    )

    assert query["_url"] == ["https://auth.atlassian.com/authorize"]
    assert (query["audience"], query["prompt"]) == (["api.atlassian.com"], ["consent"])
    assert query["scope"] == [
        "read:space:confluence read:page:confluence read:attachment:confluence offline_access"
    ]
    assert done.headers["location"] == "/admin/sources?oauth=connected&source=wiki-eng"
    assert json.loads(seen[0].content)["grant_type"] == "authorization_code"
    assert seen[1].headers["authorization"] == "Bearer at-access"
    assert source_named(api, acme, "wiki-eng")["has_credentials"] is True
    assert not_granted.headers["location"] == "/admin/sources?oauth=failed"
    assert source_named(api, acme, "wiki-x")["has_credentials"] is False


async def test_oauth_start_checks_the_kind(api: TestClient, acme: Acme) -> None:
    create(
        api,
        acme,
        {
            "key": "wiki-hr",
            "name": "HR wiki",
            "kind": "confluence",
            "config": {"site_url": "https://acme.atlassian.net", "spaces": ["HR"]},
            "enabled": False,
        },
    )

    wrong = api.get(
        "/api/v1/admin/sources/wiki-hr/oauth/microsoft/start",
        headers=acme.as_("erin"),
        follow_redirects=False,
    )
    missing = api.get(
        "/api/v1/admin/sources/nope/oauth/atlassian/start",
        headers=acme.as_("erin"),
        follow_redirects=False,
    )

    assert (wrong.status_code, wrong.json()["code"]) == (409, "wrong_kind")
    assert missing.status_code == 404

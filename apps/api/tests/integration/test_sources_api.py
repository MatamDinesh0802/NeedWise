"""Sources over real HTTP, with the real web crawler against a real local web server: create,
write-only credentials, sync into a collection, follow changes and deletions on the site. Health,
the run history, pause and resume, collection owners managing their collections' sources only,
stale synced documents (titles withheld from admins who cannot read them), and Slack OAuth with
a scripted Slack.

Needs TEST_QDRANT_URL and TEST_VALKEY_URL (and the test database)."""

import asyncio
import functools
import os
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.integration.conftest import VALKEY_URL, Acme, Database
from workmind.core.config import Settings
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.knowledge.models import Document
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


# --- Health, run history, collection owners, stale documents, Slack OAuth ----------------------


def source_named(api: TestClient, acme: Acme, key: str, who: str = "erin") -> dict[str, Any]:
    return next(
        s for s in api.get("/api/v1/admin/sources", headers=acme.as_(who)).json() if s["key"] == key
    )


def create(api: TestClient, acme: Acme, who: str, key: str, collection: str, **body: Any) -> Any:
    payload = {
        "key": key,
        "name": key.replace("-", " ").title(),
        "kind": "github",
        "collection": collection,
        "config": {"repo": f"acme/{key}"},
        "enabled": False,
        **body,
    }
    return api.post("/api/v1/admin/sources", json=payload, headers=acme.as_(who, write=True))


async def test_health_runs_and_the_permission_mapping_after_a_sync(
    api: TestClient, acme: Acme, site: tuple[str, Path]
) -> None:
    url, _ = site
    created = api.post(
        "/api/v1/admin/sources",
        json={
            "key": "intranet",
            "name": "Intranet",
            "kind": "web",
            "collection": "company-handbook",
            "config": {"start_urls": [url]},
        },
        headers=acme.as_("erin", write=True),
    ).json()
    synced = sync(api, acme, "intranet")
    history = api.get("/api/v1/admin/sources/intranet/runs", headers=acme.as_("erin")).json()

    assert (created["health"], created["syncing"], created["stale_after_days"]) == (
        "never_synced",
        False,
        180,
    )
    assert created["next_sync_at"] is not None  # never synced: due now
    assert created["owner"]["id"] == str(acme.users["erin"])
    assert created["oauth"] is None
    assert created["collection_name"] == "Company Handbook"
    access = {a["relation"]: a for a in created["access"]}
    assert access["owner"]["label"] == "Alice Moreau"  # the collection's owner
    assert any(a["label"] == "Everyone in the organization" for a in created["access"])
    assert synced["health"] == "healthy"
    assert synced["consecutive_failures"] == 0
    assert synced["last_run"]["status"] == "ok"
    assert synced["last_run"]["trigger"] == "manual"
    assert synced["last_run"]["requested_by"]["id"] == str(acme.users["erin"])
    assert synced["last_run"]["stats"]["added"] == 3
    assert synced["stale"] == {"not_updated": 0, "missing_upstream": 0}
    assert [r["status"] for r in history] == ["ok"]
    assert history[0]["complete"] is True


async def test_a_failing_source_explains_its_error_in_plain_words(
    api: TestClient, acme: Acme
) -> None:
    closed = "http://127.0.0.1:9/handbook/"  # the discard port: nothing listens
    api.post(
        "/api/v1/admin/sources",
        json={
            "key": "offline",
            "name": "Offline wiki",
            "kind": "web",
            "collection": "company-handbook",
            "config": {"start_urls": [closed]},
        },
        headers=acme.as_("erin", write=True),
    )

    failed = sync(api, acme, "offline")
    history = api.get("/api/v1/admin/sources/offline/runs", headers=acme.as_("erin")).json()

    assert (failed["status"], failed["health"], failed["consecutive_failures"]) == (
        "error",
        "failing",
        1,
    )
    assert "couldn't reach the source" in failed["last_error_plain"]
    assert history[0]["status"] == "error"
    assert closed in history[0]["errors"][0]["message"]
    assert "couldn't reach" in history[0]["errors"][0]["plain"]


async def test_pause_and_resume(api: TestClient, acme: Acme) -> None:
    assert create(api, acme, "erin", "docs-repo", "company-handbook", enabled=True).is_success

    paused = api.post("/api/v1/admin/sources/docs-repo/pause", headers=acme.as_("erin", write=True))
    refused = api.post("/api/v1/admin/sources/docs-repo/sync", headers=acme.as_("erin", write=True))
    resumed = api.post(
        "/api/v1/admin/sources/docs-repo/resume", headers=acme.as_("erin", write=True)
    )

    assert paused.status_code == 200
    assert (paused.json()["enabled"], paused.json()["health"]) == (False, "paused")
    assert paused.json()["next_sync_at"] is None
    assert (refused.status_code, refused.json()["code"]) == (409, "source_paused")
    assert (resumed.json()["enabled"], resumed.json()["health"]) == (True, "never_synced")


async def test_collection_owners_manage_the_sources_of_their_collections_only(
    api: TestClient, acme: Acme, app_sessions: Any
) -> None:
    # Alice owns Company Handbook and HR Guidance; Hannah owns Finance Reports; Bob owns none.
    assert create(api, acme, "erin", "handbook-repo", "company-handbook").status_code == 201
    assert create(api, acme, "erin", "finance-repo", "finance-reports").status_code == 201

    alice_list = api.get("/api/v1/admin/sources", headers=acme.as_("alice")).json()
    alice_options = api.get("/api/v1/admin/sources/options", headers=acme.as_("alice")).json()
    erin_options = api.get("/api/v1/admin/sources/options", headers=acme.as_("erin")).json()
    elsewhere = create(api, acme, "alice", "alice-finance", "finance-reports")
    own = create(api, acme, "alice", "alice-handbook", "company-handbook")
    write = acme.as_("alice", write=True)
    finance_calls = [
        api.put(
            "/api/v1/admin/sources/finance-repo",
            json={"name": "x", "config": {"repo": "x/y"}, "interval_minutes": 60, "enabled": True},
            headers=write,
        ),
        api.post("/api/v1/admin/sources/finance-repo/sync", headers=write),
        api.post("/api/v1/admin/sources/finance-repo/pause", headers=write),
        api.put("/api/v1/admin/sources/finance-repo/credentials", json={}, headers=write),
        api.get("/api/v1/admin/sources/finance-repo/runs", headers=acme.as_("alice")),
        api.delete("/api/v1/admin/sources/finance-repo", headers=write),
        api.get(
            "/api/v1/admin/sources/stale-documents",
            params={"source": "finance-repo"},
            headers=acme.as_("alice"),
        ),
    ]
    edited = api.put(
        "/api/v1/admin/sources/handbook-repo",
        json={
            "name": "Handbook repo",
            "config": {"repo": "acme/handbook"},
            "interval_minutes": 1440,
            "enabled": False,
            "stale_after_days": 90,
        },
        headers=write,
    )
    bob = api.get("/api/v1/admin/sources", headers=acme.as_("bob"))
    bob_options = api.get("/api/v1/admin/sources/options", headers=acme.as_("bob"))
    token = api.post(
        "/api/v1/me/tokens",
        json={"name": "probe", "scopes": ["knowledge:read"]},
        headers=write,
    ).json()["token"]
    with_token = api.get("/api/v1/admin/sources", headers={"Authorization": f"Bearer {token}"})
    old_stale = api.get("/api/v1/admin/sources/stale", headers=acme.as_("alice"))

    assert [s["key"] for s in alice_list] == ["handbook-repo"]
    assert alice_options["scope"] == "owned"
    assert {c["key"] for c in alice_options["collections"]} == {"company-handbook", "hr-guidance"}
    assert erin_options["scope"] == "all"
    assert len(erin_options["collections"]) > 2
    assert (elsewhere.status_code, elsewhere.json()["code"]) == (422, "unknown_collection")
    assert own.status_code == 201
    assert own.json()["owner"]["id"] == str(acme.users["alice"])
    assert [r.status_code for r in finance_calls] == [404] * len(finance_calls)
    assert edited.status_code == 200, edited.text
    assert (edited.json()["interval_minutes"], edited.json()["stale_after_days"]) == (1440, 90)
    assert (bob.status_code, bob.json()["code"]) == (403, "forbidden")
    assert bob_options.status_code == 403
    assert (with_token.status_code, with_token.json()["code"]) == (403, "scope_required")
    assert old_stale.status_code == 403  # the all-documents list stays for org admins
    assert source_named(api, acme, "finance-repo")["key"] == "finance-repo"  # untouched


async def test_an_update_without_a_threshold_keeps_it_and_null_turns_it_off(
    api: TestClient, acme: Acme
) -> None:
    created = create(
        api,
        acme,
        "erin",
        "slack-general",
        "company-handbook",
        kind="slack",
        config={"channels": ["C0123ABCD"]},
    )
    body = {
        "name": "General",
        "config": {"channels": ["C0123ABCD"]},
        "interval_minutes": 60,
        "enabled": False,
    }
    headers = acme.as_("erin", write=True)
    kept = api.put("/api/v1/admin/sources/slack-general", json=body, headers=headers)
    set_ = api.put(
        "/api/v1/admin/sources/slack-general",
        json={**body, "stale_after_days": 30},
        headers=headers,
    )
    cleared = api.put(
        "/api/v1/admin/sources/slack-general",
        json={**body, "stale_after_days": None},
        headers=headers,
    )

    assert created.json()["stale_after_days"] is None  # Slack history is an archive
    assert created.json()["oauth"] == "slack"
    assert kept.json()["stale_after_days"] is None
    assert set_.json()["stale_after_days"] == 30
    assert cleared.json()["stale_after_days"] is None


async def test_stale_synced_documents_are_listed_and_titles_withheld_from_who_cannot_read_them(
    api: TestClient, acme: Acme, site: tuple[str, Path], app_sessions: Any
) -> None:
    url, pages = site
    for key, collection in (("intranet", "company-handbook"), ("hr-wiki", "hr-guidance")):
        api.post(
            "/api/v1/admin/sources",
            json={
                "key": key,
                "name": key.title(),
                "kind": "web",
                "collection": collection,
                "config": {"start_urls": [url]},
            },
            headers=acme.as_("erin", write=True),
        )
        sync(api, acme, key)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        await session.execute(
            update(Document)
            .where(Document.source_id.is_not(None), Document.title == "Travel policy")
            .values(source_changed_at=datetime.now(UTC) - timedelta(days=400))
        )
    (pages / "index.html").write_text("<html><title>Intranet</title></html>")  # links gone
    api.put(  # a crawl of one page: cut short, so the others are kept and flagged
        "/api/v1/admin/sources/intranet",
        json={
            "name": "Intranet",
            "config": {"start_urls": [url], "max_pages": 1},
            "interval_minutes": 60,
            "enabled": True,
        },
        headers=acme.as_("erin", write=True),
    )
    (pages / "index.html").write_text(
        "<html><title>Intranet</title><a href='leave.html'>Leave</a>"
        "<a href='travel.html'>Travel</a></html>"
    )
    sync(api, acme, "intranet")

    as_erin = api.get("/api/v1/admin/sources/stale-documents", headers=acme.as_("erin")).json()
    one = api.get(
        "/api/v1/admin/sources/stale-documents",
        params={"source": "intranet"},
        headers=acme.as_("erin"),
    ).json()
    as_alice = api.get(
        "/api/v1/admin/sources/stale-documents",
        params={"source": "hr-wiki"},
        headers=acme.as_("alice"),
    ).json()
    listed = source_named(api, acme, "intranet")

    by_source = {(d["source"], d["reason"]) for d in as_erin}
    assert ("intranet", "missing_upstream") in by_source
    assert ("hr-wiki", "not_updated") in by_source
    assert [d["reason"] for d in one][:2] == ["missing_upstream", "missing_upstream"]
    assert {d["title"] for d in one if d["reason"] == "missing_upstream"} == {
        "Leave policy",
        "Travel policy",
    }
    hr = [d for d in as_erin if d["source"] == "hr-wiki"]
    assert [(d["title"], d["url"]) for d in hr] == [(None, None)]  # Erin can't open HR Guidance
    assert hr[0]["collection_name"] == "HR Guidance (internal)"
    assert [d["title"] for d in as_alice] == ["Travel policy"]  # its owner can
    assert as_alice[0]["url"].endswith("travel.html")
    assert listed["stale"]["missing_upstream"] == 2
    assert listed["last_run"]["complete"] is False


def oauth_settings(database: Database, tmp_path: Path, **extra: Any) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        qdrant_url=QDRANT_URL or "",
        authz_outbox_worker=False,
        ingestion_mode="inline",
        embedding_provider="hash",
        embedding_model=f"test_{uuid.uuid4().hex[:10]}",
        embedding_dimensions=64,
        object_storage_path=str(tmp_path / "objects"),
        **extra,
    )


async def test_connect_slack_stores_the_bot_token_encrypted_and_never_returns_it(
    database: Database, acme: Acme, tmp_path: Path
) -> None:
    granted: list[dict[str, str]] = []

    def slack(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://slack.com/api/oauth.v2.access"
        form = dict(httpx.QueryParams(request.content.decode()))
        granted.append(form)
        if form["code"] != "good":
            return httpx.Response(200, json={"ok": False, "error": "invalid_code"})
        return httpx.Response(
            200,
            json={"ok": True, "access_token": "xoxb-secret-bot-token", "token_type": "bot"},
        )

    settings = oauth_settings(
        database, tmp_path, slack_client_id="123.456", slack_client_secret="s3cret"
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.resources.http = httpx.AsyncClient(transport=httpx.MockTransport(slack))
        options = client.get("/api/v1/admin/sources/options", headers=acme.as_("erin")).json()
        slack_source = create(
            client,
            acme,
            "alice",
            "team-chat",
            "company-handbook",
            kind="slack",
            config={"channels": ["C0123ABCD"]},
        )
        create(
            client,
            acme,
            "erin",
            "web-only",
            "company-handbook",
            kind="web",
            config={"start_urls": ["https://intranet.example/"]},
        )
        wrong_kind = client.get(
            "/api/v1/admin/sources/web-only/oauth/slack/start", headers=acme.as_("erin")
        )
        start = client.get(
            "/api/v1/admin/sources/team-chat/oauth/slack/start",
            params={"back": "knowledge"},
            headers=acme.as_("alice"),
            follow_redirects=False,
        )
        location = httpx.URL(start.headers["location"])
        state = location.params["state"]
        stolen = client.get(  # the same state in someone else's session
            "/api/v1/oauth/slack/callback",
            params={"code": "good", "state": state},
            headers=acme.as_("erin"),
            follow_redirects=False,
        )
        restart = client.get(
            "/api/v1/admin/sources/team-chat/oauth/slack/start",
            params={"back": "knowledge"},
            headers=acme.as_("alice"),
            follow_redirects=False,
        )
        state = httpx.URL(restart.headers["location"]).params["state"]
        done = client.get(
            "/api/v1/oauth/slack/callback",
            params={"code": "good", "state": state},
            headers=acme.as_("alice"),
            follow_redirects=False,
        )
        replay = client.get(
            "/api/v1/oauth/slack/callback",
            params={"code": "good", "state": state},
            headers=acme.as_("alice"),
            follow_redirects=False,
        )
        listed = client.get("/api/v1/admin/sources", headers=acme.as_("alice"))

    slack_kind = next(k for k in options["kinds"] if k["kind"] == "slack")
    assert (slack_kind["oauth"], slack_kind["oauth_configured"]) == ("slack", True)
    assert next(k for k in options["kinds"] if k["kind"] == "gdrive")["oauth_configured"] is False
    assert slack_source.status_code == 201
    assert (wrong_kind.status_code, wrong_kind.json()["code"]) == (409, "wrong_kind")
    assert start.status_code == 302
    assert str(location).startswith("https://slack.com/oauth/v2/authorize?")
    assert location.params["client_id"] == "123.456"
    assert "channels:history" in location.params["scope"]
    assert location.params["redirect_uri"].endswith("/api/v1/oauth/slack/callback")
    assert stolen.status_code == 303
    assert "oauth=failed" in stolen.headers["location"]
    assert done.headers["location"] == (
        "/knowledge/sources?oauth=connected&provider=slack&source=team-chat"
    )
    assert granted[-1]["client_secret"] == "s3cret"
    assert "oauth=failed" in replay.headers["location"]  # a state works once
    assert len(granted) == 1  # the stolen and replayed states never reached Slack
    team_chat = next(s for s in listed.json() if s["key"] == "team-chat")
    assert team_chat["has_credentials"] is True
    assert "xoxb-secret-bot-token" not in listed.text


async def test_slack_and_google_oauth_need_their_clients(api: TestClient, acme: Acme) -> None:
    create(
        api,
        acme,
        "erin",
        "chat",
        "company-handbook",
        kind="slack",
        config={"channels": ["C0123ABCD"]},
    )
    create(
        api,
        acme,
        "erin",
        "drive",
        "company-handbook",
        kind="gdrive",
        config={"folder_id": "folder-12345"},
    )

    slack = api.get("/api/v1/admin/sources/chat/oauth/slack/start", headers=acme.as_("erin"))
    google = api.get("/api/v1/admin/sources/drive/oauth/google/start", headers=acme.as_("erin"))

    assert (slack.status_code, slack.json()["code"]) == (409, "oauth_not_configured")
    assert (google.status_code, google.json()["code"]) == (409, "oauth_not_configured")

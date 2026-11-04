"""Sources over real HTTP, with the real web crawler against a real local web server: create,
write-only credentials, sync into a collection, follow changes and deletions on the site.

Needs TEST_QDRANT_URL and TEST_VALKEY_URL (and the test database)."""

import asyncio
import functools
import os
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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

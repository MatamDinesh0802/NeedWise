"""Knowledge Hub over real HTTP: uploads, versions, permissions, deletion, sharing changes.

Needs TEST_QDRANT_URL (the compose Qdrant: http://localhost:6333) as well as the database and
Valkey. Embeddings are the deterministic hash provider; ingestion runs inline in the API.
"""

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory
from workmind.core.config import Settings
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.authz.models import AuthzOutbox
from workmind.modules.knowledge.models import Collection
from workmind.modules.retrieval.qdrant import QdrantClient, match

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL), reason="set TEST_QDRANT_URL and TEST_VALKEY_URL"
)
POLICY = (
    b"# Travel Policy\n\n## Meals\nMeals are reimbursed up to 60 euros per day.\n\n"
    b"## Hotels\nHotels are capped at 180 euros per night.\n"
)


@pytest.fixture(scope="module")
def collection_name() -> str:
    return f"test_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def api(database: Database, tmp_path: Path, collection_name: str) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        qdrant_url=QDRANT_URL or "",
        authz_outbox_worker=False,
        ingestion_mode="inline",
        embedding_provider="hash",
        embedding_model=collection_name,  # gives every test run its own Qdrant collection
        embedding_dimensions=64,
        object_storage_path=str(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
async def qdrant(collection_name: str) -> AsyncIterator[tuple[QdrantClient, str]]:
    async with httpx.AsyncClient() as http:
        client = QdrantClient(http, QDRANT_URL or "")
        yield client, f"chunks_{collection_name}_64"


@pytest.fixture(scope="module", autouse=True)
def drop_test_collection(collection_name: str) -> Iterator[None]:
    yield

    async def drop() -> None:
        async with httpx.AsyncClient() as http:
            client = QdrantClient(http, QDRANT_URL or "")
            name = f"chunks_{collection_name}_64"
            if await client.collection_exists(name):
                await client.delete_collection(name)

    asyncio.run(drop())


async def _collection_id(sessions: SessionFactory, acme: Acme, key: str) -> uuid.UUID:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return (await session.scalars(select(Collection.id).where(Collection.key == key))).one()


def _upload(
    api: TestClient, acme: Acme, who: str, collection_id: uuid.UUID, filename: str, data: bytes
) -> Any:  # a Starlette TestClient response (httpx2 when installed)
    return api.post(
        f"/api/v1/collections/{collection_id}/documents",
        params={"filename": filename},
        content=data,
        headers={**acme.as_(who, write=True), "Content-Type": "text/markdown"},
    )


def _wait_ready(api: TestClient, acme: Acme, who: str, document_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        body = api.get(f"/api/v1/documents/{document_id}", headers=acme.as_(who)).json()
        if body["status"] in ("ready", "failed"):
            return body
        time.sleep(0.2)
    raise AssertionError("ingestion did not finish")


async def test_an_editor_uploads_and_the_document_becomes_searchable(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, qdrant: tuple[QdrantClient, str]
) -> None:
    handbook = await _collection_id(
        app_sessions, acme, "company-handbook"
    )  # Alice manages HR: editor

    response = _upload(api, acme, "alice", handbook, "travel-policy.md", POLICY)
    assert response.status_code == 202, response.text
    document = _wait_ready(api, acme, "alice", response.json()["document"]["id"])

    client, name = qdrant
    points = await client.count(name, {"must": [match("document_id", str(document["id"]))]})
    chunks = api.get(f"/api/v1/documents/{document['id']}/chunks", headers=acme.as_("bob")).json()[
        "items"
    ]
    assert (document["status"], document["title"], document["chunk_count"]) == (
        "ready",
        "Travel Policy",
        2,
    )
    assert points == 2
    assert [c["heading_path"] for c in chunks] == [["Meals"], ["Hotels"]]


async def test_viewers_cannot_upload_and_outsiders_cannot_even_see(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    handbook = await _collection_id(app_sessions, acme, "company-handbook")
    records = await _collection_id(app_sessions, acme, "hr-employee-records")

    as_viewer = _upload(api, acme, "bob", handbook, "x.md", b"# X\n\ntext")
    as_outsider = api.get(f"/api/v1/collections/{records}/documents", headers=acme.as_("bob"))

    assert (as_viewer.status_code, as_viewer.json()["code"]) == (403, "forbidden")
    assert (as_outsider.status_code, as_outsider.json()["code"]) == (404, "not_found")


async def test_each_person_lists_exactly_the_collections_they_may_see(
    api: TestClient, acme: Acme
) -> None:
    def keys(who: str) -> set[str]:
        return {
            c["key"] for c in api.get("/api/v1/collections", headers=acme.as_(who)).json()["items"]
        }

    assert keys("mallory") == {"contractor-onboarding"}
    assert {"hr-employee-records", "finance-payroll", "company-handbook"} <= keys("alice")
    assert "hr-employee-records" not in keys("bob")
    assert "leadership-strategy" not in keys(
        "erin"
    )  # admins don't automatically read restricted content


async def test_same_bytes_change_nothing_and_new_bytes_replace_the_old_version(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, qdrant: tuple[QdrantClient, str]
) -> None:
    handbook = await _collection_id(app_sessions, acme, "company-handbook")
    first = _upload(api, acme, "alice", handbook, "versions.md", POLICY).json()
    _wait_ready(api, acme, "alice", first["document"]["id"])

    again = _upload(api, acme, "alice", handbook, "versions.md", POLICY).json()
    updated = _upload(
        api, acme, "alice", handbook, "versions.md", POLICY + b"\n## Taxis\nTaxis need a receipt.\n"
    ).json()
    final = _wait_ready(api, acme, "alice", updated["document"]["id"])

    client, name = qdrant
    points = await client.count(name, {"must": [match("document_id", final["id"])]})
    assert again["unchanged"] is True
    assert updated["document"]["id"] == first["document"]["id"]
    assert (final["version"], final["chunk_count"], points) == (
        2,
        3,
        3,
    )  # old version's points are gone


async def test_unsupported_files_and_downloads(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    handbook = await _collection_id(app_sessions, acme, "company-handbook")
    rejected = api.post(
        f"/api/v1/collections/{handbook}/documents",
        params={"filename": "tool.exe"},
        content=b"MZ",
        headers={**acme.as_("alice", write=True), "Content-Type": "application/octet-stream"},
    )
    uploaded = _upload(api, acme, "alice", handbook, "download.md", POLICY).json()["document"]
    _wait_ready(api, acme, "alice", uploaded["id"])

    as_member = api.get(f"/api/v1/documents/{uploaded['id']}/file", headers=acme.as_("bob"))
    as_guest = api.get(f"/api/v1/documents/{uploaded['id']}/file", headers=acme.as_("mallory"))

    assert (rejected.status_code, rejected.json()["code"]) == (415, "unsupported_type")
    assert (as_member.status_code, as_member.content) == (200, POLICY)
    assert as_guest.status_code == 404


async def test_deleting_removes_chunks_from_search_and_queues_the_tuple_removal(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, qdrant: tuple[QdrantClient, str]
) -> None:
    handbook = await _collection_id(app_sessions, acme, "company-handbook")
    document = _upload(api, acme, "alice", handbook, "to-delete.md", POLICY).json()["document"]
    _wait_ready(api, acme, "alice", document["id"])

    response = api.delete(
        f"/api/v1/documents/{document['id']}", headers=acme.as_("alice", write=True)
    )

    client, name = qdrant
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        deletes = [
            d for row in (await session.scalars(select(AuthzOutbox))).all() for d in row.deletes
        ]
    assert response.status_code == 204
    assert await client.count(name, {"must": [match("document_id", document["id"])]}) == 0
    assert {
        "user": f"collection:{handbook}",
        "relation": "collection",
        "object": f"document:{document['id']}",
    } in deletes


async def test_sharing_changes_update_the_acl_inside_the_search_index(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, qdrant: tuple[QdrantClient, str]
) -> None:
    payroll = await _collection_id(app_sessions, acme, "finance-payroll")
    note = b"# Payroll note\n\nQ3 payouts were 182,400 euros.\n"
    # Payroll is edited by the finance-leads group (Hannah, the CFO); Alice may only read it.
    response = _upload(api, acme, "hannah.schmidt", payroll, "payroll-note.md", note)
    assert response.status_code == 202, response.text
    document = response.json()["document"]
    _wait_ready(api, acme, "hannah.schmidt", document["id"])
    bob = f"user:{acme.users['bob']}"
    client, name = qdrant
    visible_to_bob = {
        "must": [match("document_id", document["id"]), {"key": "acl", "match": {"any": [bob]}}]
    }
    assert await client.count(name, visible_to_bob) == 0

    viewers = [
        g["principal"]
        for g in api.get(f"/api/v1/collections/{payroll}", headers=acme.as_("alice")).json()[
            "grants"
        ]
        if g["relation"] == "viewer"
    ]
    shared = api.put(
        f"/api/v1/admin/collections/{payroll}/grants",
        json={"viewers": [*viewers, bob], "editors": []},
        headers=acme.as_("erin", write=True),
    )

    assert shared.status_code == 200, shared.text
    assert await client.count(name, visible_to_bob) == 1


async def test_admins_list_every_collection_with_sharing_but_still_cannot_read_restricted_ones(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    listing = api.get("/api/v1/admin/collections", headers=acme.as_("erin"))
    records = await _collection_id(app_sessions, acme, "hr-employee-records")

    keys = {c["key"] for c in listing.json()["items"]}
    assert listing.status_code == 200
    assert {"hr-employee-records", "leadership-strategy"} <= keys
    assert (
        api.get(f"/api/v1/collections/{records}/documents", headers=acme.as_("erin")).status_code
        == 404
    )
    assert api.get("/api/v1/admin/collections", headers=acme.as_("bob")).status_code == 403

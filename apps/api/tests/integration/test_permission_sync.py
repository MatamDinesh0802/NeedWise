"""Permission sync end to end, in both editions (docs/permission-sync.md): a Google Drive source
(a fake Drive API behind httpx.MockTransport) synced into Engineering Docs, with real
PostgreSQL, Qdrant and OpenFGA.

Community: the synced documents keep their collection's access. Enterprise (permission_sync
licensed, needwise_ee installed): each file gets its Drive permissions as its own list, mapped
fail closed; a person removed in Drive loses access at the next sync, or at once through the
Drive changes feed; permissions that cannot be read leave the file to nobody.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY).
"""

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from needwise.core.context import RequestContext
from needwise.core.edition import Edition, License
from needwise.core.extensions import ExtensionRegistry, enable_extensions
from needwise.core.tenancy import tenant_transaction
from needwise.modules.authz.openfga import OpenFgaClient, RelationTuple
from needwise.modules.authz.sync import ensure_store, process_outbox, reconcile
from needwise.modules.connectors.models import IdentityMapping, Source
from needwise.modules.connectors.oauth import OAuthApp, OAuthApps
from needwise.modules.connectors.registry import build_connector
from needwise.modules.connectors.secrets import SecretBox
from needwise.modules.connectors.sync import SyncDeps, SyncReport, sync_source
from needwise.modules.identity.service import load_identity
from needwise.modules.knowledge import document_acl
from needwise.modules.knowledge.dispatch import InlineDispatcher
from needwise.modules.knowledge.models import Collection, Document
from needwise.modules.knowledge.pipeline import IngestionDeps
from needwise.modules.knowledge.storage import LocalObjectStorage
from needwise.modules.retrieval.qdrant import QdrantClient, match
from needwise.modules.retrieval.service import Retriever
from needwise.providers.embeddings import HashEmbeddings
from tests.integration.conftest import VALKEY_URL, Acme, SessionFactory, seed_acme

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
OPENFGA_URL = os.environ.get("TEST_OPENFGA_URL")
OPENFGA_KEY = os.environ.get("TEST_OPENFGA_API_KEY")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL and OPENFGA_URL),
    reason="set TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL",
)
KEY = "c2VjcmV0LWtleS1mb3ItdGVzdHMtMzItYnl0ZXMtISE="
DRIVE = "/drive/v3"


@dataclass
class FakeDrive:
    """A Drive folder whose files, permissions and changes a test changes between calls."""

    files: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])
    permissions: dict[str, list[dict[str, Any]] | int] = field(
        default_factory=dict[str, list[dict[str, Any]] | int]
    )
    changes: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    versions: dict[str, int] = field(default_factory=dict[str, int])
    permission_reads: list[str] = field(default_factory=list[str])

    def put(self, file_id: str, name: str, text: str) -> None:
        self.files[file_id] = (name, text)
        self.versions[file_id] = self.versions.get(file_id, 0) + 1

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(DRIVE)
        if path == "/files":
            listed = [
                {
                    "id": file_id,
                    "name": name,
                    "mimeType": "text/markdown",
                    "version": str(self.versions[file_id]),
                }
                for file_id, (name, _) in sorted(self.files.items())
            ]
            return httpx.Response(200, json={"files": listed})
        if path.endswith("/permissions"):
            file_id = path.split("/")[2]
            self.permission_reads.append(file_id)
            found = self.permissions.get(file_id, [])
            if isinstance(found, int):
                return httpx.Response(found, json={"error": {"message": "nope"}})
            return httpx.Response(200, json={"permissions": found})
        if path == "/changes/startPageToken":
            return httpx.Response(200, json={"startPageToken": "t1"})
        if path == "/changes":
            changes, self.changes = self.changes, []
            return httpx.Response(200, json={"changes": changes, "newStartPageToken": "t2"})
        if path.startswith("/files/") and request.url.params.get("alt") == "media":
            return httpx.Response(200, content=self.files[path.split("/")[2]][1].encode())
        return httpx.Response(404)


def person(email: str) -> dict[str, Any]:
    return {"type": "user", "role": "reader", "emailAddress": email}


def licensed() -> ExtensionRegistry:
    """Enterprise with permission sync: needs the ee package (uv sync --extra ee)."""
    pytest.importorskip("needwise_ee", reason="install the ee extra: uv sync --extra ee")
    now = datetime.now(UTC)
    license_ = License(
        "Acme Robotics",
        frozenset({"permission_sync"}),
        now - timedelta(days=1),
        now + timedelta(days=30),
        key_id="test",
    )
    return enable_extensions(Edition(license_))


@dataclass
class World:
    acme: Acme
    store: str
    source: Source
    drive: FakeDrive
    deps: SyncDeps
    fga: OpenFgaClient
    retriever: Retriever
    canary: str

    async def sync(self, extensions: ExtensionRegistry | None) -> SyncReport:
        self.deps.extensions = extensions
        report = await sync_source(self.deps, self.acme.tenant_id, self.source.id)
        while await process_outbox(self.deps.sessions, self.fga, self.acme.tenant_id, self.store):
            pass
        return report

    async def ctx(self, name: str) -> RequestContext:
        async with tenant_transaction(self.deps.sessions, self.acme.tenant_id) as session:
            identity = await load_identity(session, self.acme.users[name])
        assert identity is not None
        return RequestContext(
            request_id="t",
            tenant_id=self.acme.tenant_id,
            user_id=identity.user.id,
            org_role=identity.user.org_role,
            principals=identity.principals,
            auth_method="service",
        )

    async def sees(self, name: str, query: str = "maintenance token outlives session") -> bool:
        """Search as `name` (both locks) finds the canary, and OpenFGA agrees."""
        result = await self.retriever.search(await self.ctx(name), query, limit=20)
        found = any(self.canary in p.text for p in result.passages)
        document = await self.document("f1")
        allowed = await self.fga.check(
            self.store,
            RelationTuple(f"user:{self.acme.users[name]}", "viewer", f"document:{document.id}"),
        )
        assert found == allowed, f"the locks disagree for {name}"
        return found

    async def document(self, external_id: str) -> Document:
        async with tenant_transaction(self.deps.sessions, self.acme.tenant_id) as session:
            return (
                await session.scalars(
                    select(Document).where(
                        Document.source_id == self.source.id, Document.external_id == external_id
                    )
                )
            ).one()

    async def entries(self, external_id: str) -> set[tuple[str | None, str | None]]:
        document = await self.document(external_id)
        async with tenant_transaction(self.deps.sessions, self.acme.tenant_id) as session:
            grants = (await document_acl.grants_of(session, [document.id]))[document.id]
        return {(g.principal, g.external) for g in grants}


@pytest.fixture
async def world(
    owner_sessions: SessionFactory, app_sessions: SessionFactory, tmp_path: Path
) -> AsyncIterator[World]:
    acme = await seed_acme(owner_sessions)
    drive = FakeDrive()
    canary = f"CANARY-SYNC-{uuid.uuid4().hex[:8].upper()}"
    drive.put(
        "f1", "advisory.md", f"# Advisory\n\nThe maintenance token outlives its session. {canary}\n"
    )
    drive.put("f2", "restarts.md", "# Restarts\n\nRestart the fleet controller with fleetctl.\n")
    drive.permissions = {
        "f1": [
            {**person("Wei.Zhang@acme.test"), "role": "owner"},
            person("marcus.bell@acme.test"),
            {"type": "group", "role": "reader", "emailAddress": "eng-leads@acme.test"},
            {"type": "domain", "role": "reader", "domain": "partner.example"},
            {**person("gone@acme.test"), "deleted": True},
        ],
        "f2": [person("marcus.bell@acme.test"), {"type": "anyone", "role": "reader"}],
    }
    secrets = SecretBox(KEY)
    name = f"chunks_test_{uuid.uuid4().hex[:8]}_32"
    async with (
        httpx.AsyncClient(timeout=15) as http,
        httpx.AsyncClient(transport=httpx.MockTransport(drive.handle)) as google,
    ):
        fga = OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)
        store = await ensure_store(owner_sessions, fga, acme.tenant_id)
        await reconcile(owner_sessions, fga, acme.tenant_id, store)
        async with tenant_transaction(app_sessions, acme.tenant_id) as session:
            collection = (
                await session.scalars(
                    select(Collection).where(Collection.key == "engineering-docs")
                )
            ).one()
            source = Source(
                tenant_id=acme.tenant_id,
                key="eng-drive",
                name="Engineering Drive",
                kind="gdrive",
                collection_id=collection.id,
                owner_id=acme.users["marcus.bell"],
                config={"folder_id": "eng-folder"},
            )
            session.add(source)
            await session.flush()
            source.credentials = secrets.seal(
                source.id,
                {
                    "access_token": "t",
                    "expires_at": "2999-01-01T00:00:00+00:00",
                    "refresh_token": "r",
                },
            )
            source_id = source.id
        async with tenant_transaction(app_sessions, acme.tenant_id) as session:
            stored = await session.get(Source, source_id)
        assert stored is not None
        qdrant = QdrantClient(http, QDRANT_URL or "")
        await qdrant.ensure_collection(name, dense_size=32)
        storage = LocalObjectStorage(str(tmp_path))
        embeddings = HashEmbeddings(32)
        deps = SyncDeps(
            sessions=app_sessions,
            storage=storage,
            ingestion=InlineDispatcher(
                IngestionDeps(app_sessions, storage, embeddings, qdrant, name)
            ),
            qdrant=qdrant,
            collection_name=name,
            secrets=secrets,
            connectors=lambda s, c: build_connector(
                s, c, http=google, apps=OAuthApps(google=OAuthApp("id", "x"))
            ),
            max_bytes=1_000_000,
        )
        retriever = Retriever(
            qdrant=qdrant,
            embeddings=embeddings,
            fga=fga,
            sessions=app_sessions,
            collection_name=name,
        )
        yield World(acme, store, stored, drive, deps, fga, retriever, canary)
        await qdrant.delete_collection(name)
        await fga.delete_store(store)


async def test_without_the_license_synced_files_keep_the_collections_access(world: World) -> None:
    report = await world.sync(None)

    assert (report.status, report.added) == ("ok", 2)
    assert not (await world.document("f1")).own_acl
    assert world.drive.permission_reads == []  # Community never asks the source for ACLs
    assert await world.sees("bob")  # in Engineering: the collection-level mapping
    assert not await world.sees("dan")


async def test_with_the_license_each_file_gets_its_drive_permissions(world: World) -> None:
    report = await world.sync(licensed())

    assert (report.status, report.added) == ("ok", 2)
    advisory = await world.document("f1")
    assert (advisory.own_acl, advisory.acl_origin) == (True, "source")
    wei, marcus = (f"user:{world.acme.users[n]}" for n in ("wei.zhang", "marcus.bell"))
    assert await world.entries("f1") == {
        (wei, "email:wei.zhang@acme.test"),
        (marcus, "email:marcus.bell@acme.test"),
        (None, "group:eng-leads@acme.test"),  # unmapped: grants nothing
        (None, "domain:partner.example"),
    }
    assert report.acl_unmapped == 2
    assert await world.sees("wei.zhang")
    assert await world.sees("marcus.bell")
    assert not await world.sees("bob")  # in Engineering, not shared with him in Drive
    assert not await world.sees("priya.raman")
    # Anyone with the link: the organization, within the collection's access.
    assert await world.entries("f2") == {
        (marcus, "email:marcus.bell@acme.test"),
        (f"org:{world.acme.tenant_id}", "anyone"),
    }
    restarts = await world.retriever.search(await world.ctx("bob"), "restart fleet controller")
    assert any("fleetctl" in p.text for p in restarts.passages)
    # The chunks carried the list from their very first indexing.
    points = await world.deps.qdrant.count(
        world.deps.collection_name,
        {"must": [match("document_id", str(advisory.id)), match("own_acl", True)]},
        tenant_id=world.acme.tenant_id,
    )
    assert points >= 1
    async with tenant_transaction(world.deps.sessions, world.acme.tenant_id) as session:
        unmapped = await document_acl.unmapped(session)
    assert [(u.external, u.documents, u.sources) for u in unmapped] == [
        ("domain:partner.example", 1, ["eng-drive"]),
        ("group:eng-leads@acme.test", 1, ["eng-drive"]),
    ]


async def test_a_person_removed_in_drive_loses_access_at_the_next_sync(world: World) -> None:
    extensions = licensed()
    await world.sync(extensions)
    assert await world.sees("wei.zhang")

    world.drive.permissions["f1"] = [person("marcus.bell@acme.test"), person("bob@acme.test")]
    report = await world.sync(extensions)

    assert (report.unchanged, report.acl_changed) == (2, 1)  # no content changed, one list did
    assert not await world.sees("wei.zhang")
    assert await world.sees("bob")
    assert await world.sees("marcus.bell")


async def test_an_admin_mapping_opens_a_drive_group_to_its_members_in_the_collection(
    world: World,
) -> None:
    await world.sync(licensed())
    assert not await world.sees("fatima.hassan")
    async with tenant_transaction(world.deps.sessions, world.acme.tenant_id) as session:
        engineering_member = f"user:{world.acme.users['fatima.hassan']}"
        session.add(
            IdentityMapping(
                tenant_id=world.acme.tenant_id,
                external="group:eng-leads@acme.test",
                principal=engineering_member,
            )
        )
    # Mappings apply at the next sync too (the admin API applies them at once).
    await world.sync(licensed())
    assert await world.sees("fatima.hassan")
    assert (engineering_member, "group:eng-leads@acme.test") in await world.entries("f1")


async def test_the_drive_changes_feed_revokes_without_waiting_for_a_sync(world: World) -> None:
    extensions = licensed()
    from needwise_ee.permission_sync import drive_changes

    await world.sync(extensions)
    valkey = Redis.from_url(VALKEY_URL or "")  # pyright: ignore[reportUnknownMemberType]
    try:
        assert await drive_changes(world.deps, valkey, world.source) == 0  # records where to start
        world.drive.permissions["f1"] = [person("marcus.bell@acme.test")]
        world.drive.changes = [
            {"fileId": "f1", "removed": False, "file": {"trashed": False}},
            {"fileId": "f2", "removed": False, "file": {"trashed": True}},
            {"fileId": "not-ours", "removed": True},
        ]

        applied = await drive_changes(world.deps, valkey, world.source)
        while await process_outbox(
            world.deps.sessions, world.fga, world.acme.tenant_id, world.store
        ):
            pass

        assert applied == 2
        assert not await world.sees("wei.zhang")  # at once, no sync ran
        assert await world.sees("marcus.bell")
        assert await world.entries("f2") == set()  # trashed: nobody until the sync deletes it
        bob = await world.retriever.search(await world.ctx("bob"), "restart fleet controller")
        assert not any("fleetctl" in p.text for p in bob.passages)
        token = await valkey.get(f"t:{world.acme.tenant_id}:permsync:drive:{world.source.id}")
        assert token == b"t2"
    finally:
        await valkey.delete(f"t:{world.acme.tenant_id}:permsync:drive:{world.source.id}")
        await valkey.aclose()


async def test_permissions_that_cannot_be_read_leave_the_file_to_nobody(world: World) -> None:
    extensions = licensed()
    await world.sync(extensions)

    world.drive.permissions["f1"] = 403  # Drive refuses to show who it is shared with
    report = await world.sync(extensions)

    assert report.status == "ok"
    assert any("f1: permissions not read" in e for e in report.errors)
    assert await world.entries("f1") == set()
    for name in ("wei.zhang", "marcus.bell", "bob"):
        assert not await world.sees(name), name


async def test_an_outage_while_reading_permissions_changes_nothing(world: World) -> None:
    extensions = licensed()
    await world.sync(extensions)
    before = await world.entries("f1")

    world.drive.permissions["f1"] = 503
    world.drive.put("f3", "new.md", f"# New\n\nThe maintenance token outlives it. {world.canary}\n")
    report = await world.sync(extensions)

    assert report.status == "error"
    assert await world.entries("f1") == before
    async with tenant_transaction(world.deps.sessions, world.acme.tenant_id) as session:
        created = await session.scalar(select(Document.id).where(Document.external_id == "f3"))
    assert created is None  # nothing new is indexed without its permissions
    assert await world.sees("wei.zhang")


async def test_after_the_license_lapses_lists_stay_as_they_were(world: World) -> None:
    """Documented behaviour: without the license nothing about ACLs changes. The lists written
    while licensed stay (they only narrow); changes in Drive no longer reach them."""
    await world.sync(licensed())
    world.drive.permissions["f1"] = [person("bob@acme.test")]

    report = await world.sync(None)

    assert report.status == "ok"
    assert await world.sees("wei.zhang")
    assert not await world.sees("bob")

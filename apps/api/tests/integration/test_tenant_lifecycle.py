"""Tenant lifecycle through `wm tenant` (docs/multi-tenancy.md): a tenant is created with its
authorization store, gets data in every layer, and is deleted from every one of them, after
which `verify-empty` finds nothing; another tenant's data is untouched throughout.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY).
"""

import asyncio
import os
import time
import uuid
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text, update

from tests.fakes import FakeLLM
from tests.integration.conftest import VALKEY_URL, Acme, Database, seed_acme
from workmind import cli
from workmind.core import middleware
from workmind.core.config import Settings
from workmind.core.db import create_engine, create_session_factory
from workmind.core.tenancy import global_transaction, tenant_transaction
from workmind.main import create_app
from workmind.modules.authz.openfga import OpenFgaClient
from workmind.modules.authz.sync import ensure_store, process_outbox, reconcile
from workmind.modules.governance.models import AuditEvent
from workmind.modules.identity import tenant_cli
from workmind.modules.identity.lifecycle import Stores, tenant_tables, verify_empty
from workmind.modules.identity.models import Tenant, User
from workmind.modules.identity.service import sign_in
from workmind.modules.identity.sessions import SessionStore
from workmind.modules.knowledge.storage import LocalObjectStorage
from workmind.modules.retrieval.qdrant import QdrantClient

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
OPENFGA_URL = os.environ.get("TEST_OPENFGA_URL")
OPENFGA_KEY = os.environ.get("TEST_OPENFGA_API_KEY")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL and OPENFGA_URL),
    reason="set TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL",
)
WRITE = {"Origin": "http://localhost:8480", "X-CSRF-Protection": "1"}


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@pytest.fixture
def settings(database: Database, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        migrations_database_url=database.owner_url,
        valkey_url=VALKEY_URL or "",
        qdrant_url=QDRANT_URL or "",
        openfga_api_url=OPENFGA_URL or "",
        openfga_api_key=OPENFGA_KEY or "",
        authz_outbox_worker=False,
        ingestion_mode="inline",
        embedding_provider="hash",
        retrieval_min_similarity=0.0,
        embedding_model=f"test_{uuid.uuid4().hex[:10]}",
        embedding_dimensions=64,
        object_storage_path=str(tmp_path),
        keycloak_url="http://127.0.0.1:9/auth",
    )


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        app.state.resources.llm = FakeLLM("Sixteen weeks.")
        yield client

    async def drop() -> None:
        async with httpx.AsyncClient() as http:
            qdrant = QdrantClient(http, QDRANT_URL or "")
            if await qdrant.collection_exists(settings.qdrant_collection):
                await qdrant.delete_collection(settings.qdrant_collection)

    _run(drop())


async def _stores_check(settings: Settings, tenant_id: uuid.UUID, **kw: Any) -> list[str]:
    engine = create_engine(settings.migrations_database_url)
    valkey = Redis.from_url(settings.valkey_url)  # pyright: ignore[reportUnknownMemberType]
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            stores = Stores(
                owner=create_session_factory(engine),
                fga=OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY),
                qdrant=QdrantClient(http, QDRANT_URL or ""),
                storage=LocalObjectStorage(settings.object_storage_path),
                analytics_root=settings.analytics_path,
                valkey=valkey,
            )
            return (await verify_empty(stores, tenant_id, **kw)).lines()
    finally:
        await valkey.aclose()
        await engine.dispose()


async def _tenant(settings: Settings, slug: str) -> Tenant | None:
    engine = create_engine(settings.migrations_database_url)
    try:
        async with global_transaction(create_session_factory(engine)) as session:
            return await session.scalar(select(Tenant).where(Tenant.slug == slug))
    finally:
        await engine.dispose()


async def _session_for(settings: Settings, tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    valkey = Redis.from_url(settings.valkey_url)  # pyright: ignore[reportUnknownMemberType]
    try:
        store = SessionStore(valkey, idle_timeout_s=1800, absolute_timeout_s=3600)
        return await store.create(
            user_id=user_id, tenant_id=tenant_id, keycloak_sub="", keycloak_sid=None, id_token="t"
        )
    finally:
        await valkey.aclose()


async def _counts(settings: Settings, tenant_id: uuid.UUID) -> dict[str, int]:
    engine = create_engine(settings.migrations_database_url)
    try:
        async with tenant_transaction(create_session_factory(engine), tenant_id) as session:
            counts: dict[str, int] = {}
            for table in await tenant_tables(session):
                query = text(f'SELECT count(*) FROM "{table}"')  # noqa: S608 (catalog names)
                counts[table] = int(await session.scalar(query) or 0)
            return counts
    finally:
        await engine.dispose()


async def _control(settings: Settings) -> Acme:
    """Another tenant, with its store: it must come through every deletion untouched."""
    engine = create_engine(settings.migrations_database_url)
    try:
        owner = create_session_factory(engine)
        acme = await seed_acme(owner)
        async with httpx.AsyncClient(timeout=15) as http:
            fga = OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)
            await reconcile(
                owner, fga, acme.tenant_id, await ensure_store(owner, fga, acme.tenant_id)
            )
        return acme
    finally:
        await engine.dispose()


async def _sync_outbox(settings: Settings, tenant_id: uuid.UUID) -> None:
    engine = create_engine(settings.migrations_database_url)
    try:
        owner = create_session_factory(engine)
        tenant = await _tenant_by_id(owner, tenant_id)
        async with httpx.AsyncClient(timeout=15) as http:
            fga = OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)
            while await process_outbox(owner, fga, tenant_id, tenant.openfga_store_id or ""):
                pass
    finally:
        await engine.dispose()


async def _tenant_by_id(owner: Any, tenant_id: uuid.UUID) -> Tenant:
    async with global_transaction(owner) as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        return tenant


def _wait_ready(api: TestClient, headers: dict[str, str], document_id: str) -> None:
    deadline = time.monotonic() + 30
    while api.get(f"/api/v1/documents/{document_id}", headers=headers).json()["status"] != "ready":
        assert time.monotonic() < deadline
        time.sleep(0.1)


def _fill(
    api: TestClient, owner: dict[str, str], user_id: uuid.UUID, collection_key: str
) -> dict[str, str]:
    """Data in every layer: a collection and a document (rows, points, a file), a conversation
    with an answer (run events in Valkey, usage), an analytics table (a DuckDB file)."""
    write = {**owner, **WRITE}
    collection = api.post(
        "/api/v1/admin/collections",
        json={
            "key": collection_key,
            "name": "Handbook",
            "scope": "enterprise",
            "editors": [f"user:{user_id}"],
        },
        headers=write,
    )
    assert collection.status_code == 201, collection.text
    document = api.post(
        f"/api/v1/collections/{collection.json()['id']}/documents",
        params={"filename": "leave.md"},
        content=b"# Leave\n\nParental leave is 16 weeks.\n",
        headers={**write, "Content-Type": "text/markdown"},
    )
    assert document.status_code == 202, document.text
    document_id = document.json()["document"]["id"]
    _wait_ready(api, owner, document_id)
    conversation = api.post("/api/v1/conversations", json={}, headers=write).json()["id"]
    run = api.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"content": "How long is parental leave?"},
        headers=write,
    ).json()["run_id"]
    with api.stream("GET", f"/api/v1/runs/{run}/events", headers=owner) as events:
        for line in events.iter_lines():
            if line.startswith("event: run.completed") or line.startswith("event: run.failed"):
                break
    table = api.post(
        "/api/v1/admin/analytics/tables",
        params={"name": "targets"},
        content=b"month,target\n2026-10-01,5\n",
        headers={**write, "Content-Type": "text/csv"},
    )
    assert table.status_code == 200, table.text
    return {"document_id": document_id, "run_id": run}


def test_a_tenant_is_created_filled_and_deleted_from_every_store(
    settings: Settings, api: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    slug = f"lc-{uuid.uuid4().hex[:8]}"
    control = _run(_control(settings))
    control_session = control.as_("alice")
    control_doc = _fill(
        api, control.as_("priya.raman"), control.users["priya.raman"], "control-handbook"
    )
    _run(_sync_outbox(settings, control.tenant_id))  # the outbox worker is off in tests
    control_before = _run(_counts(settings, control.tenant_id))

    created = tenant_cli.main(
        ["create", slug, "--name", "Lifecycle Co", "--owner-email", f"owner@{slug}.test"], settings
    )
    assert created == 0, capsys.readouterr().err
    tenant = _run(_tenant(settings, slug))
    assert tenant is not None
    assert tenant.openfga_store_id
    tenant_id, store_id = tenant.id, tenant.openfga_store_id

    async def owner_user() -> User:
        engine = create_engine(settings.migrations_database_url)
        try:
            async with tenant_transaction(create_session_factory(engine), tenant_id) as session:
                user = await session.scalar(select(User))
                event = await session.scalar(select(AuditEvent.action))
                assert event == "tenant.created"
                assert user is not None
                return user
        finally:
            await engine.dispose()

    owner = _run(owner_user())
    assert (owner.email, owner.org_role, owner.status) == (f"owner@{slug}.test", "owner", "invited")

    async def first_sign_in() -> User:
        """What /api/auth/callback does for a verified email of the tenant's organization."""
        engine = create_engine(settings.database_url)
        try:
            claims = {"sub": f"kc-{slug}", "email": owner.email, "email_verified": True}
            return await sign_in(
                create_session_factory(engine), tenant_id, claims, ip=None, user_agent=None
            )
        finally:
            await engine.dispose()

    signed_in = _run(first_sign_in())
    assert (signed_in.id, signed_in.status) == (owner.id, "active")
    session_id = _run(_session_for(settings, tenant_id, owner.id))
    headers = {"Cookie": f"wm_session={session_id}"}
    assert api.get("/api/v1/me", headers=headers).status_code == 200
    filled = _fill(api, headers, owner.id, "handbook")

    # Before: the check sees the tenant in every layer.
    present = _run(_stores_check(settings, tenant_id, slug=slug, store_id=store_id))
    layers = {line.split(":", 1)[0] for line in present}
    assert layers >= {"postgres", "qdrant", "object storage", "analytics", "valkey", "openfga"}

    assert tenant_cli.main(["delete", slug, "--confirm", "other"], settings) == 1
    capsys.readouterr()
    deleted = tenant_cli.main(["delete", slug, "--confirm", slug], settings)
    out = capsys.readouterr()
    assert deleted == 0, out.err
    assert "verified: nothing of the tenant remains" in out.out

    # After: nothing, by every check, and the CLI agrees on its own.
    assert _run(_stores_check(settings, tenant_id, slug=slug, store_id=store_id)) == []
    assert (
        tenant_cli.main(
            ["verify-empty", str(tenant_id), "--slug", slug, "--store", store_id], settings
        )
        == 0
    )
    assert api.get("/api/v1/me", headers=headers).status_code == 401
    assert api.get(f"/api/v1/runs/{filled['run_id']}/events", headers=headers).status_code == 401

    # The other tenant: every row, its search and its session are as they were.
    assert _run(_counts(settings, control.tenant_id)) == control_before
    assert api.get("/api/v1/me", headers=control_session).status_code == 200
    found = api.post(
        "/api/v1/search", json={"query": "parental leave"}, headers={**control_session, **WRITE}
    ).json()
    assert control_doc["document_id"] in {p["document_id"] for p in found["passages"]}

    async def drop_control_store() -> None:
        found = await _tenant(settings, control.slug)
        assert found is not None
        async with httpx.AsyncClient() as http:
            await OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY).delete_store(
                found.openfga_store_id or ""
            )

    _run(drop_control_store())


def test_create_refuses_a_taken_or_malformed_slug(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    slug = f"lc-{uuid.uuid4().hex[:8]}"
    assert tenant_cli.main(["create", slug, "--name", "First"], settings) == 0
    assert tenant_cli.main(["create", slug, "--name", "Again"], settings) == 1
    assert tenant_cli.main(["create", "Not_A_Slug", "--name", "Bad"], settings) == 1
    assert "already exists" in capsys.readouterr().err
    assert tenant_cli.main(["delete", slug, "--confirm", slug], settings) == 0
    assert tenant_cli.main(["delete", slug, "--confirm", slug], settings) == 1  # gone
    assert tenant_cli.main(["list"], settings) == 0


def test_a_suspended_tenant_ends_its_sessions_at_once(settings: Settings, api: TestClient) -> None:
    acme = _run(_control(settings))
    headers = acme.as_("alice")
    assert api.get("/api/v1/me", headers=headers).status_code == 200

    async def suspend() -> None:
        engine = create_engine(settings.migrations_database_url)
        try:
            async with global_transaction(create_session_factory(engine)) as session:
                await session.execute(
                    update(Tenant).where(Tenant.id == acme.tenant_id).values(status="suspended")
                )
        finally:
            await engine.dispose()

    _run(suspend())
    assert api.get("/api/v1/me", headers=headers).status_code == 401
    assert tenant_cli.main(["delete", acme.slug, "--confirm", acme.slug], settings) == 0


class AccessLog:
    """Records each access log line with the log context bound at that moment."""

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def info(self, _event: str, **fields: Any) -> None:
        self.lines.append({**structlog.contextvars.get_contextvars(), **fields})


def test_request_logs_name_the_tenant(
    settings: Settings, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    acme = _run(_control(settings))
    access = AccessLog()
    monkeypatch.setattr(middleware, "_access_log", access)

    assert api.get("/api/v1/me", headers=acme.as_("alice")).status_code == 200
    assert api.get("/api/v1/me").status_code == 401

    signed_in, anonymous = access.lines
    assert signed_in["tenant_id"] == str(acme.tenant_id)
    assert "tenant_id" not in anonymous
    assert tenant_cli.main(["delete", acme.slug, "--confirm", acme.slug], settings) == 0


def test_the_wm_command_routes_to_the_tenant_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["tenant", "--help"]) == 0
    assert "verify-empty" in capsys.readouterr().out
    assert cli.main(["tenant"]) == 2

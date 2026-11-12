"""Event triggers end to end: a document becomes ready -> the workflows listening for
"document.ready" run, once per document version, as their owner, and only if the owner can view
the document. Real HTTP, inline ingestion with hash embeddings, a scripted model.

Needs TEST_QDRANT_URL as well as the database and TEST_VALKEY_URL."""

import asyncio
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fakes import FakeLLM
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory
from workmind.core.config import Settings
from workmind.core.tenancy import tenant_transaction
from workmind.main import create_app
from workmind.modules.governance.models import AuditEvent
from workmind.modules.knowledge.models import Collection, Document
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.modules.tools.registry import ToolRegistry
from workmind.modules.work.events import DocumentReady, document_ready
from workmind.modules.work.models import Workflow, WorkflowRun
from workmind.modules.work.workflows import WorkflowDeps, queued_runs, start_queued

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL), reason="set TEST_QDRANT_URL and TEST_VALKEY_URL"
)
HANDBOOK = b"# Remote Work Policy\n\nEveryone may work remotely two days a week.\n"


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
        retrieval_min_similarity=0.0,
        embedding_model=collection_name,
        embedding_dimensions=64,
        object_storage_path=str(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        yield client


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


def use_model(api: TestClient, llm: FakeLLM) -> FakeLLM:
    app: Any = api.app
    app.state.resources.llm = llm
    return llm


def save_workflow(
    api: TestClient, acme: Acme, who: str, key: str, trigger: dict[str, Any], **extra: Any
) -> Any:
    return api.put(
        f"/api/v1/admin/workflows/{key}",
        json={
            "key": key,
            "name": key.replace("-", " ").title(),
            "definition": {
                "trigger": trigger,
                "steps": [
                    {
                        "id": "read",
                        "research": "Summarize {{ event.title }} from {{ event.collection }} "
                        "(version {{ event.version }}, {{ event.link }}).",
                    }
                ],
            },
            **extra,
        },
        headers=acme.as_(who, write=True),
    )


async def collection_id(sessions: SessionFactory, acme: Acme, key: str) -> uuid.UUID:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        return (await session.scalars(select(Collection.id).where(Collection.key == key))).one()


def upload(
    api: TestClient, acme: Acme, who: str, collection: uuid.UUID, filename: str, data: bytes
) -> str:
    response = api.post(
        f"/api/v1/collections/{collection}/documents",
        params={"filename": filename},
        content=data,
        headers={**acme.as_(who, write=True), "Content-Type": "text/markdown"},
    )
    assert response.status_code == 202, response.text
    document_id: str = response.json()["document"]["id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        body = api.get(f"/api/v1/documents/{document_id}", headers=acme.as_(who)).json()
        if body["status"] in ("ready", "failed"):
            assert body["status"] == "ready", body
            return document_id
        time.sleep(0.2)
    raise AssertionError("ingestion did not finish")


def finished_runs(api: TestClient, acme: Acme, who: str, key: str, count: int) -> list[Any]:
    deadline = time.monotonic() + 20
    runs: list[Any] = []
    while time.monotonic() < deadline:
        runs = api.get(f"/api/v1/admin/workflows/{key}/runs", headers=acme.as_(who)).json()
        if len(runs) >= count and all(r["status"] not in ("queued", "running") for r in runs):
            return runs
        time.sleep(0.2)
    raise AssertionError(f"runs did not finish: {runs}")


async def runs_of(sessions: SessionFactory, acme: Acme, key: str) -> list[WorkflowRun]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        workflow = (await session.scalars(select(Workflow).where(Workflow.key == key))).one()
        return list(
            (
                await session.scalars(
                    select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id)
                )
            ).all()
        )


async def test_a_ready_document_runs_the_workflows_listening_for_it_once_per_version(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    llm = use_model(api, FakeLLM(tool_turns=[], tool_text="Two remote days a week."))
    # Erin (admin) owns three workflows; she can view the handbook (enterprise).
    for key, trigger in (
        ("handbook-watch", {"event": "document.ready", "collection": "company-handbook"}),
        ("any-document", {"event": "document.ready"}),
        ("sops-only", {"event": "document.ready", "collection": "operations-sops"}),
    ):
        assert save_workflow(api, acme, "erin", key, trigger).status_code == 200
    assert (
        save_workflow(
            api, acme, "erin", "switched-off", {"event": "document.ready"}, enabled=False
        ).status_code
        == 200
    )

    handbook = await collection_id(app_sessions, acme, "company-handbook")
    document_id = upload(api, acme, "alice", handbook, "remote-work.md", HANDBOOK)

    for key in ("handbook-watch", "any-document"):
        (run,) = finished_runs(api, acme, "erin", key, 1)
        assert (run["trigger"], run["status"]) == ("event", "succeeded"), run
        assert run["steps"]["read"]["text"] == "Two remote days a week."
        assert "_event" not in run["steps"]  # the payload stays internal
    assert await runs_of(app_sessions, acme, "sops-only") == []  # another collection
    assert await runs_of(app_sessions, acme, "switched-off") == []  # disabled

    # The event fills the templates, quoted as data (the title is the author's), and the run
    # searched as Erin.
    questions = [str(request.messages[0].content) for _, request in llm.requests]
    summary = "Summarize <data>Remote Work Policy</data> from <data>company-handbook</data>"
    assert sum(summary in q for q in questions) == 2
    assert all(
        f"version <data>1</data>, <data>/knowledge/documents/{document_id}</data>" in q
        for q in questions
    )

    # Identical bytes change nothing; the same version again (a re-index, a re-delivered task)
    # finds its run taken.
    upload(api, acme, "alice", handbook, "remote-work.md", HANDBOOK)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        document = await session.get(Document, uuid.UUID(document_id))
        assert document is not None
        assert document.current_version_id is not None
        again = await document_ready(
            session,
            acme.tenant_id,
            DocumentReady(
                document_id=document.id,
                version_id=document.current_version_id,
                version=1,
                collection_id=handbook,
                title=document.title,
                filename=document.filename,
                source=document.source,
            ),
        )
        triggered = [
            e
            for e in await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "workflow.triggered")
            )
        ]
    assert again == []
    assert len(await runs_of(app_sessions, acme, "handbook-watch")) == 1
    assert sorted(str(e.resource_id) for e in triggered) == ["any-document", "handbook-watch"]
    assert all(e.details["document_id"] == document_id for e in triggered)

    # A new version is a new event.
    upload(api, acme, "alice", handbook, "remote-work.md", HANDBOOK + b"\nThree days in May.\n")
    runs = finished_runs(api, acme, "erin", "handbook-watch", 2)
    assert [r["status"] for r in runs] == ["succeeded", "succeeded"]


async def test_an_event_run_uses_the_tenants_models(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    """document.ready runs resolve the tenant's catalog (Admin > Models), like every request."""
    system = use_model(api, FakeLLM(tool_turns=[], tool_text="From the deployment's model."))
    tenant_model = FakeLLM(tool_turns=[], tool_text="From the organization's model.")
    app: Any = api.app
    app.state.resources.models.build = lambda provider, model, key: tenant_model
    admin = acme.as_("erin", write=True)
    provider = api.post(
        "/api/v1/admin/models/providers",
        json={"name": "Team", "kind": "openai_compatible", "base_url": "https://llm.example/v1"},
        headers=admin,
    )
    assert provider.status_code == 201, provider.text
    model = api.post(
        "/api/v1/admin/models/catalog",
        json={"provider_id": provider.json()["id"], "name": "team-model"},
        headers=admin,
    )
    assert model.status_code == 201, model.text
    roles = api.put("/api/v1/admin/models/roles", json={"agent": model.json()["id"]}, headers=admin)
    assert roles.status_code == 200, roles.text
    assert (
        save_workflow(api, acme, "erin", "any-document", {"event": "document.ready"}).status_code
        == 200
    )

    handbook = await collection_id(app_sessions, acme, "company-handbook")
    upload(api, acme, "alice", handbook, "remote-work.md", HANDBOOK)

    (run,) = finished_runs(api, acme, "erin", "any-document", 1)
    assert (run["status"], run["steps"]["read"]["text"]) == (
        "succeeded",
        "From the organization's model.",
    )
    assert tenant_model.roles() == ["agent"]
    assert "agent" not in system.roles()


async def test_no_run_for_a_document_the_owner_cannot_view(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    use_model(api, FakeLLM(tool_turns=[], tool_text="Read."))
    assert (
        save_workflow(api, acme, "erin", "any-document", {"event": "document.ready"}).status_code
        == 200
    )
    # Alice (HR records group) adds an employee record; Erin can't view that collection.
    records = await collection_id(app_sessions, acme, "hr-employee-records")
    upload(api, acme, "alice", records, "salary-review.md", b"# Salary review\n\nConfidential.\n")
    await asyncio.sleep(0.5)

    assert await runs_of(app_sessions, acme, "any-document") == []
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        audit = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "workflow.triggered")
            )
        ).all()
    assert audit == []


async def test_an_event_trigger_must_name_a_collection_its_owner_can_view(
    api: TestClient, acme: Acme
) -> None:
    hidden = save_workflow(
        api,
        acme,
        "erin",
        "records",
        {"event": "document.ready", "collection": "hr-employee-records"},
    )
    assert hidden.status_code == 422, hidden.text
    assert hidden.json()["code"] == "trigger_collection"
    missing = save_workflow(
        api, acme, "erin", "nothing", {"event": "document.ready", "collection": "no-such-thing"}
    )
    assert missing.json()["code"] == "trigger_collection"
    no_event = save_workflow(api, acme, "erin", "half", {"collection": "company-handbook"})
    assert (no_event.status_code, no_event.json()["code"]) == (422, "invalid_workflow")


async def test_a_queued_run_starts_exactly_once_and_the_sweep_finds_missed_ones(
    app_sessions: SessionFactory, acme: Acme
) -> None:
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        workflow = Workflow(
            tenant_id=acme.tenant_id,
            key="queued-once",
            name="Queued once",
            owner_id=acme.users["erin"],
            definition={
                "trigger": {"event": "document.ready"},
                "steps": [{"id": "read", "research": "What is {{ event.title }}?"}],
            },
        )
        session.add(workflow)
        await session.flush()
        handbook = (
            await session.scalars(select(Collection).where(Collection.key == "company-handbook"))
        ).one()
        document = Document(
            tenant_id=acme.tenant_id,
            collection_id=handbook.id,
            title="Expenses",
            filename="expenses.md",
            mime_type="text/markdown",
            trust="reference",
            sensitivity="internal",
        )
        session.add(document)
        await session.flush()
        (run_id,) = await document_ready(
            session,
            acme.tenant_id,
            DocumentReady(
                document_id=document.id,
                version_id=uuid.uuid4(),
                version=1,
                collection_id=handbook.id,
                title="Expenses",
                filename="expenses.md",
                source="upload",
            ),
        )
    later = (await runs_of(app_sessions, acme, "queued-once"))[0].created_at.replace(year=2099)
    assert await queued_runs(app_sessions, acme.tenant_id, later) == [run_id]

    llm = FakeLLM(tool_turns=[], tool_text="A policy.")
    deps = WorkflowDeps(app_sessions, llm, ToolRegistry())  # type: ignore[arg-type]
    first, second = await asyncio.gather(
        start_queued(deps, acme.tenant_id, run_id), start_queued(deps, acme.tenant_id, run_id)
    )
    assert [r is not None for r in (first, second)].count(True) == 1  # one claim wins
    assert len(llm.requests) == 1
    (run,) = await runs_of(app_sessions, acme, "queued-once")
    assert run.status == "succeeded"
    assert run.steps["_event"]["title"] == "Expenses"
    assert await queued_runs(app_sessions, acme.tenant_id, later) == []

"""Cross-tenant isolation, end to end (ARCHITECTURE section 8, docs/multi-tenancy.md).

Two tenants, A and B, each a full copy of the Acme company. A gets data in every module, and
every piece of it carries a canary phrase that exists nowhere in B. Then B's org owner plays the
attacker against every layer:

- every route of the app, enumerated from `app.routes`, is called with A's ids in the path, the
  query and the body (`test_every_route_is_classified` fails until a new route has a probe in
  PROBES below): no canary, secret or unrequested id of A ever comes back, and afterwards A's
  rows, points, files and Valkey keys are exactly as they were;
- Qdrant queries as B, also with filters crafted to reach A, and through MCP;
- every Valkey key written is under a tenant prefix (or one of the few global ones);
- OpenFGA checks for A's objects in B's store;
- Celery tasks without a tenant, and with B's tenant and A's ids;
- object storage and analytics files are under per-tenant paths; traversal is refused.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY). The model
is scripted (FakeLLM), embeddings are the hash provider, ingestion runs inline.
"""

import asyncio
import hashlib
import inspect
import json
import os
import re
import time
import uuid
from collections.abc import Callable, Coroutine, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text

from tests.analytics_support import ACME_ANALYTICS
from tests.fakes import FakeLLM
from tests.integration.conftest import (
    VALKEY_URL,
    Acme,
    Database,
    SessionFactory,
    seed_acme,
)
from workmind.core.config import Settings
from workmind.core.context import RequestContext
from workmind.core.db import create_engine, create_session_factory
from workmind.core.edition import FEATURES, Edition, License
from workmind.core.extensions import enable_extensions
from workmind.core.tenancy import GLOBAL_KEY_PREFIXES, key_tenant, tenant_transaction
from workmind.main import create_app
from workmind.modules.analytics import dataset
from workmind.modules.analytics.agent import Composed
from workmind.modules.analytics.models import AnalyticsRun, AnalyticsSource
from workmind.modules.authz.openfga import OpenFgaClient, RelationTuple
from workmind.modules.authz.sync import ensure_store, process_outbox, reconcile
from workmind.modules.connectors.models import Source
from workmind.modules.identity.models import Department, Group
from workmind.modules.knowledge.models import Collection, Document, DocumentVersion
from workmind.modules.knowledge.storage import (
    ForeignObjectKeyError,
    LocalObjectStorage,
    object_key,
    owned_key,
)
from workmind.modules.meetings.extract import ActionItem, Decision, Extraction
from workmind.modules.retrieval.qdrant import QdrantClient, match, match_any
from workmind.modules.retrieval.sparse import encode_query
from workmind.modules.work.approvals import propose
from workmind.modules.work.models import Notification, Workflow, WorkflowRun
from workmind.modules.work.routing import RouteChoice
from workmind.seed.analytics_load import seed_analytics

QDRANT_URL = os.environ.get("TEST_QDRANT_URL")
OPENFGA_URL = os.environ.get("TEST_OPENFGA_URL")
OPENFGA_KEY = os.environ.get("TEST_OPENFGA_API_KEY")
pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and VALKEY_URL and OPENFGA_URL),
    reason="set TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL",
)

# One word that only tenant A's data contains, plus a phrase per kind of data. B's requests may
# contain the word (to search for it); they never contain a phrase, so a phrase in a response
# can only have come from A's data.
CANARY = "xc" + uuid.uuid4().hex[:10]
PHRASES = {
    kind: f"{CANARY}{kind}"
    for kind in (
        "doc",
        "body",
        "chat",
        "answer",
        "person",
        "dept",
        "group",
        "coll",
        "agent",
        "agentv2",
        "provider",
        "model",
        "flow",
        "approval",
        "task",
        "meeting",
        "decision",
        "action",
        "table",
        "run",
        "source",
        "sqlsource",
        "drive",
        "wiki",
        "library",
        "token",
        "review",
        "slack",
        "mapping",
        "rule",
        "request",
    )
}
SECRET_PROVIDER_KEY = f"sk-{uuid.uuid4().hex}{uuid.uuid4().hex}"
SECRET_GITHUB_TOKEN = f"ghp_{uuid.uuid4().hex}"
SECRET_SLACK_TOKEN = f"xoxb-{uuid.uuid4().hex}"
SECRET_SLACK_HOOK = f"https://hooks.slack.com/services/{uuid.uuid4().hex}"
TABLE = f"t{CANARY}"  # an analytics table only A has
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class ScriptedLLM(FakeLLM):
    """Answers every kind of request the routes make, with nothing tenant-specific in it."""

    async def structured(self, role: Any, request: Any, schema: Any) -> Any:
        if self.structured_replies:
            return await super().structured(role, request, schema)
        self.requests.append((role, request))
        if schema is Composed:
            return Composed(narrative="Nothing to report.", chart=None, main_query=None), (
                self.usage(30, 8)
            )
        if schema is Extraction:
            return Extraction(summary="Nothing.", decisions=[], action_items=[]), self.usage(30, 8)
        if schema is RouteChoice:
            return RouteChoice(department="none", confidence=0.0), self.usage(30, 8)
        raise AssertionError(f"unscripted structured output: {schema}")


def scripted_llm() -> ScriptedLLM:
    return ScriptedLLM("An answer without sources.", tool_script=lambda _request: [])


# --- The world: two tenants, A with canaries everywhere ----------------------------------------


@dataclass
class Side:
    acme: Acme
    values: dict[str, str] = field(default_factory=dict[str, str])  # what probes address
    store: str = ""  # OpenFGA store
    token: str = ""  # a personal access token (alice)


@dataclass
class Snapshot:
    rows: dict[str, tuple[int, str]]  # table -> (rows, digest) of one tenant's rows
    points: tuple[int, str]  # Qdrant points of the tenant and a digest of their payloads
    files: dict[str, str]  # path -> sha256 of every file under the tenant's directories
    keys: frozenset[str]  # Valkey keys under the tenant's prefix


@dataclass
class World:
    client: TestClient
    a: Side
    b: Side
    settings: Settings
    database: Database
    storage: Path
    collection_name: str
    a_ids: frozenset[str]  # every id of every row A owns, and A's tenant id, slug and store
    keys_before: frozenset[str]  # Valkey keys that existed before the world was built
    enterprise: bool  # workmind_ee installed: its routes run (else the core's 402 stand-ins)
    before: Snapshot | None = None

    @property
    def llm(self) -> ScriptedLLM:
        app: Any = self.client.app
        return app.state.resources.llm


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


async def _owner(database: Database) -> tuple[Any, SessionFactory]:
    engine = create_engine(database.owner_url)
    return engine, create_session_factory(engine)


def _check(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def _wait_document(client: TestClient, side: Side, who: str, document_id: str) -> None:
    deadline = time.monotonic() + 30
    while True:
        status = client.get(f"/api/v1/documents/{document_id}", headers=side.acme.as_(who))
        if status.json()["status"] == "ready":
            return
        assert time.monotonic() < deadline, status.text
        time.sleep(0.1)


def _upload(
    client: TestClient, side: Side, who: str, collection_id: str, name: str, data: bytes
) -> str:
    response = client.post(
        f"/api/v1/collections/{collection_id}/documents",
        params={"filename": name},
        content=data,
        headers={**side.acme.as_(who, write=True), "Content-Type": "text/markdown"},
    )
    document_id = _check(response, 202)["document"]["id"]
    _wait_document(client, side, who, document_id)
    return document_id


def _finish_run(client: TestClient, side: Side, who: str, run_id: str) -> None:
    with client.stream(
        "GET", f"/api/v1/runs/{run_id}/events", headers=side.acme.as_(who)
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event: run.") and line[7:] in (
                "run.completed",
                "run.failed",
                "run.cancelled",
            ):
                assert line[7:] == "run.completed", line
                return


async def _seed_side(database: Database, storage: Path) -> Side:
    engine, owner = await _owner(database)
    try:
        acme = await seed_acme(owner)
        loaded = await seed_analytics(owner, acme.tenant_id, ACME_ANALYTICS, storage / "analytics")
        assert loaded is not None
        async with httpx.AsyncClient(timeout=15) as http:
            fga = OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)
            store = await ensure_store(owner, fga, acme.tenant_id)
            await reconcile(owner, fga, acme.tenant_id, store)
        side = Side(acme=acme, store=store)
        async with tenant_transaction(owner, acme.tenant_id) as session:
            collections = {c.key: c.id for c in await session.scalars(select(Collection))}
            departments = {d.slug: d.id for d in await session.scalars(select(Department))}
            groups = {g.slug: g.id for g in await session.scalars(select(Group))}
        side.values.update(
            collection_id=str(collections["company-handbook"]),
            ops_collection_id=str(collections["operations-sops"]),
            department_id=str(departments["engineering"]),
            group_id=str(groups["leadership"]),
            child_id=str(groups["hr-records"]),
            user_id=str(acme.users["bob"]),
            number="1",
            index="0",
        )
        return side
    finally:
        await engine.dispose()


def _build_a(client: TestClient, a: Side) -> None:
    """Tenant A's data, through the API where a person would create it."""
    v, acme = a.values, a.acme
    erin, alice = acme.as_("erin", write=True), acme.as_("alice", write=True)

    token = _check(
        client.post(
            "/api/v1/me/tokens",
            json={"name": PHRASES["token"], "scopes": ["profile:read", "knowledge:read"]},
            headers=alice,
        ),
        201,
    )
    a.token, v["token_id"] = token["token"], token["id"]

    body = (
        f"# {PHRASES['doc']} leave policy\n\n## Parental leave\n{CANARY} parental leave is "
        f"16 weeks. {PHRASES['body']}\n"
    ).encode()
    v["document_id"] = _upload(client, a, "alice", v["collection_id"], f"{CANARY}.md", body)
    _check(
        client.post(
            f"/api/v1/documents/{v['document_id']}/review",
            json={"note": PHRASES["review"]},
            headers=alice,
        )
    )
    # Its own access list (docs/permission-sync.md): the organization, so A's people keep
    # reading it; and an identity mapping that names a canary.
    _check(
        client.put(
            f"/api/v1/documents/{v['document_id']}/acl",
            json={"viewers": [f"org:{acme.tenant_id}"]},
            headers=alice,
        )
    )
    v["mapping_id"] = _check(
        client.put(
            "/api/v1/admin/identity-mappings",
            json={
                "external": f"group:{PHRASES['mapping']}@acme.test",
                "principal": f"group:{v['group_id']}",
            },
            headers=erin,
        )
    )["mapping"]["id"]

    conversation = _check(
        client.post("/api/v1/conversations", json={"title": PHRASES["chat"]}, headers=alice), 201
    )
    v["conversation_id"] = conversation["id"]
    started = _check(
        client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            json={"content": f"{PHRASES['chat']} how long is {CANARY} parental leave?"},
            headers=alice,
        ),
        202,
    )
    v["run_id"] = started["run_id"]
    _finish_run(client, a, "alice", v["run_id"])
    messages = _check(
        client.get(f"/api/v1/conversations/{conversation['id']}", headers=acme.as_("alice"))
    )["messages"]
    v["message_id"] = next(m["id"] for m in messages if m["role"] == "assistant")

    v["person_id"] = _check(
        client.post(
            "/api/v1/admin/users",
            json={"email": f"{PHRASES['person']}@acme.test", "display_name": PHRASES["person"]},
            headers=erin,
        ),
        201,
    )["id"]
    _check(
        client.post(
            "/api/v1/admin/departments",
            json={"slug": f"{CANARY}-dept", "name": PHRASES["dept"]},
            headers=erin,
        ),
        201,
    )
    _check(
        client.post(
            "/api/v1/admin/groups",
            json={"slug": f"{CANARY}-group", "name": PHRASES["group"]},
            headers=erin,
        ),
        201,
    )
    v["collection_key"] = f"{CANARY}-coll"
    _check(
        client.post(
            "/api/v1/admin/collections",
            json={"key": v["collection_key"], "name": PHRASES["coll"], "scope": "enterprise"},
            headers=erin,
        ),
        201,
    )

    v["agent_key"] = f"{CANARY}-agent"
    agent = {
        "name": PHRASES["agent"],
        "description": PHRASES["agent"],
        "instructions": PHRASES["agent"],
        "collections": ["company-handbook"],
    }
    _check(
        client.post("/api/v1/admin/agents", json={**agent, "key": v["agent_key"]}, headers=erin),
        201,
    )
    _check(
        client.put(
            f"/api/v1/admin/agents/{v['agent_key']}",
            json={**agent, "description": PHRASES["agentv2"]},
            headers=erin,
        )
    )

    provider = _check(
        client.post(
            "/api/v1/admin/models/providers",
            json={
                "name": PHRASES["provider"],
                "kind": "openai_compatible",
                "base_url": "https://llm.example/v1",
                "api_key": SECRET_PROVIDER_KEY,
            },
            headers=erin,
        ),
        201,
    )
    v["provider_id"] = provider["id"]
    v["model_id"] = _check(
        client.post(
            "/api/v1/admin/models/catalog",
            json={"provider_id": provider["id"], "name": PHRASES["model"]},
            headers=erin,
        ),
        201,
    )["id"]
    _check(
        client.put(
            "/api/v1/admin/budgets",
            json={
                "budgets": [
                    {"scope": "user", "subject_id": v["user_id"], "period": "month", "limit_usd": 7}
                ]
            },
            headers=erin,
        )
    )

    v["workflow_key"] = f"{CANARY}-flow"
    _check(
        client.put(
            f"/api/v1/admin/workflows/{v['workflow_key']}",
            json={
                "key": v["workflow_key"],
                "name": PHRASES["flow"],
                "definition": {"steps": [{"id": "gather", "research": PHRASES["flow"]}]},
                "enabled": False,
            },
            headers=erin,
        )
    )

    v["task_id"] = _check(
        client.post(
            "/api/v1/tasks",
            json={"title": PHRASES["task"], "assignee_id": v["user_id"]},
            headers=alice,
        ),
        201,
    )["id"]

    # Automation: a routing rule, a routed request, Slack secrets, an approver rule, and a
    # backup approver.
    _check(
        client.put(
            "/api/v1/admin/routing-rules",
            json={"rules": [{"department": f"{CANARY}-dept", "keywords": [PHRASES["rule"]]}]},
            headers=erin,
        )
    )
    v["request_task_id"] = _check(
        client.post(
            "/api/v1/requests",
            json={"title": f"{PHRASES['request']} {PHRASES['rule']}"},
            headers=alice,
        ),
        201,
    )["task"]["id"]
    _check(
        client.put(
            "/api/v1/admin/slack",
            json={
                "bot_token": SECRET_SLACK_TOKEN,
                "webhook_url": SECRET_SLACK_HOOK,
                "channel_categories": ["escalations"],
            },
            headers=erin,
        )
    )
    v["person_email"] = f"{PHRASES['person']}@acme.test"
    _check(
        client.put(
            "/api/v1/admin/automation",
            json={"approver_rules": {"send_email": v["person_email"]}},
            headers=erin,
        )
    )
    _check(
        client.put(
            "/api/v1/me/work-preferences",
            json={"backup_approver_id": v["user_id"], "digest_hour": 7},
            headers=alice,
        )
    )

    v["source_key"] = f"{CANARY}-src"
    _check(
        client.post(
            "/api/v1/admin/sources",
            json={
                "key": v["source_key"],
                "name": PHRASES["source"],
                "kind": "github",
                "collection": "company-handbook",
                "config": {"repo": f"acme/{CANARY}"},
                "credentials": {"token": SECRET_GITHUB_TOKEN},
                "enabled": False,
            },
            headers=erin,
        ),
        201,
    )
    v["gdrive_key"] = f"{CANARY}-drive"
    _check(
        client.post(
            "/api/v1/admin/sources",
            json={
                "key": v["gdrive_key"],
                "name": PHRASES["drive"],
                "kind": "gdrive",
                "collection": "company-handbook",
                "config": {"folder_id": f"folder-{CANARY}"},
                "enabled": False,
            },
            headers=erin,
        ),
        201,
    )
    started_oauth = client.get(
        f"/api/v1/admin/sources/{v['gdrive_key']}/oauth/google/start",
        headers=acme.as_("erin"),
        follow_redirects=False,
    )
    assert started_oauth.status_code == 302, started_oauth.text
    v["table"] = TABLE
    v["oauth_state"] = re.findall(r"[?&]state=([^&]+)", started_oauth.headers["location"])[0]
    v["slack_key"] = f"{CANARY}-slack"
    _check(
        client.post(
            "/api/v1/admin/sources",
            json={
                "key": v["slack_key"],
                "name": PHRASES["slack"],
                "kind": "slack",
                "collection": "company-handbook",
                "config": {"channels": ["C0123ABCD"]},
                "enabled": False,
            },
            headers=erin,
        ),
        201,
    )
    started_slack = client.get(
        f"/api/v1/admin/sources/{v['slack_key']}/oauth/slack/start",
        headers=acme.as_("erin"),
        follow_redirects=False,
    )
    assert started_slack.status_code == 302, started_slack.text
    v["slack_state"] = re.findall(r"[?&]state=([^&]+)", started_slack.headers["location"])[0]
    # Confluence and SharePoint sources, each with an OAuth flow left open.
    for provider, kind, phrase, config in (
        (
            "atlassian",
            "confluence",
            "wiki",
            {"site_url": "https://acme.atlassian.net", "spaces": [f"X{CANARY.upper()}"]},
        ),
        (
            "microsoft",
            "sharepoint",
            "library",
            {"site_url": "https://contoso.sharepoint.com/sites/acme", "folder": CANARY},
        ),
    ):
        v[f"{kind}_key"] = f"{CANARY}-{kind}"
        _check(
            client.post(
                "/api/v1/admin/sources",
                json={
                    "key": v[f"{kind}_key"],
                    "name": PHRASES[phrase],
                    "kind": kind,
                    "collection": "company-handbook",
                    "config": config,
                    "enabled": False,
                },
                headers=erin,
            ),
            201,
        )
        started = client.get(
            f"/api/v1/admin/sources/{v[f'{kind}_key']}/oauth/{provider}/start",
            headers=acme.as_("erin"),
            follow_redirects=False,
        )
        assert started.status_code == 302, started.text
        v[f"{provider}_state"] = re.findall(r"[?&]state=([^&]+)", started.headers["location"])[0]

    _check(
        client.post(
            "/api/v1/admin/analytics/tables",
            params={"name": TABLE},
            content=f"id,label\n1,{PHRASES['table']}\n".encode(),
            headers={**erin, "Content-Type": "text/csv"},
        )
    )

    llm: Any = client.app
    llm.state.resources.llm.structured_replies.append(
        Extraction(
            summary=PHRASES["meeting"],
            decisions=[Decision(text=PHRASES["decision"], segment="T1")],
            action_items=[
                ActionItem(text=PHRASES["action"], owner="Olivia Grant", due="", segment="T1")
            ],
        )
    )
    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n"
        f"<v Olivia Grant>{PHRASES['meeting']} drill moves.\n"
    )
    meeting = _check(
        client.post(
            "/api/v1/meetings",
            params={
                "collection": "operations-sops",
                "filename": "sync.vtt",
                "title": PHRASES["meeting"],
            },
            content=vtt.encode(),
            headers={
                **acme.as_("olivia.grant", write=True),
                "Content-Type": "application/octet-stream",
            },
        ),
        202,
    )
    v["meeting_id"] = meeting["id"]
    deadline = time.monotonic() + 30
    while True:
        status = _check(
            client.get(f"/api/v1/meetings/{meeting['id']}", headers=acme.as_("olivia.grant"))
        )
        if status["status"] in ("ready", "failed"):
            assert status["status"] == "ready", status
            break
        assert time.monotonic() < deadline
        time.sleep(0.1)


async def _build_a_rows(database: Database, a: Side) -> None:
    """A's records that no route creates directly: an approval waiting for a person, a workflow
    run, an analytics answer, a SQL source (pointing at no database), and their
    notifications."""
    engine, owner = await _owner(database)
    acme, v = a.acme, a.values
    try:
        async with tenant_transaction(owner, acme.tenant_id) as session:
            ctx = RequestContext(
                request_id="seed",
                tenant_id=acme.tenant_id,
                user_id=acme.users["alice"],
                org_role="member",
                principals=frozenset(),
                auth_method="session",
            )
            approval = await propose(
                session,
                ctx,
                tool="send_email",
                arguments={"to": f"{PHRASES['approval']}@acme.test", "body": PHRASES["approval"]},
                summary=PHRASES["approval"],
                approver_id=acme.users["priya.raman"],
            )
            v["approval_id"] = str(approval.id)
            workflow = (
                await session.scalars(select(Workflow).where(Workflow.key == v["workflow_key"]))
            ).one()
            run = WorkflowRun(
                tenant_id=acme.tenant_id,
                workflow_id=workflow.id,
                trigger="manual",
                status="succeeded",
                steps={"gather": {"status": "done", "output": PHRASES["flow"]}},
            )
            analytics_run = AnalyticsRun(
                tenant_id=acme.tenant_id,
                user_id=acme.users["carol"],
                question=PHRASES["run"],
                status="done",
                answer={"narrative": PHRASES["run"]},
            )
            sql_source = AnalyticsSource(
                tenant_id=acme.tenant_id,
                key="a-shop",
                name=PHRASES["sqlsource"],
                kind="postgres",
                host="db.invalid",
                port=5432,
                database="shop",
                username=PHRASES["sqlsource"],
                schemas=["shop"],
            )
            session.add_all([run, analytics_run, sql_source])
            await session.flush()
            v["workflow_run_id"], v["analytics_run_id"] = str(run.id), str(analytics_run.id)
            v["analytics_source_key"] = sql_source.key
            v["notification_id"] = str(
                (
                    await session.scalars(
                        select(Notification.id).where(
                            Notification.user_id == acme.users["priya.raman"]
                        )
                    )
                ).first()
            )
        async with httpx.AsyncClient(timeout=15) as http:
            fga = OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)
            while await process_outbox(owner, fga, acme.tenant_id, a.store):
                pass
    finally:
        await engine.dispose()


def _build_b(client: TestClient, b: Side) -> None:
    """B's own data of the same kinds, without canaries: B's lists are not empty."""
    v, acme = b.values, b.acme
    leave = b"# Leave policy\n\n## Parental leave\nParental leave is 20 weeks.\n"
    v["document_id"] = _upload(client, b, "alice", v["collection_id"], "leave.md", leave)
    b.token = _check(
        client.post(
            "/api/v1/me/tokens",
            json={"name": "b", "scopes": ["profile:read", "knowledge:read", "assistant:ask"]},
            headers=acme.as_("priya.raman", write=True),
        ),
        201,
    )["token"]
    v["task_id"] = _check(
        client.post(
            "/api/v1/tasks", json={"title": "B task"}, headers=acme.as_("priya.raman", write=True)
        ),
        201,
    )["id"]
    v.update(probe_flow="probe-flow", probe_table="probe_table")


async def _a_ids(database: Database, a: Side) -> frozenset[str]:
    """Every id of every row A owns (columns named id), plus what names A as a tenant."""
    engine, owner = await _owner(database)
    ids = {str(a.acme.tenant_id), a.acme.slug, a.store}
    try:
        async with tenant_transaction(owner, a.acme.tenant_id) as session:
            for table in await _tenant_tables(session):
                has_id = await session.scalar(
                    text(
                        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                        "AND table_name = :t AND column_name = 'id'"
                    ),
                    {"t": table},
                )
                if has_id:
                    rows = await session.scalars(text(f"SELECT id::text FROM {table}"))  # noqa: S608
                    ids.update(rows)
    finally:
        await engine.dispose()
    return frozenset(ids)


async def _tenant_tables(session: Any) -> list[str]:
    return list(
        await session.scalars(
            text(
                "SELECT c.table_name FROM information_schema.columns c "
                "JOIN information_schema.tables t USING (table_schema, table_name) "
                "WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' "
                "AND t.table_type = 'BASE TABLE' ORDER BY 1"
            )
        )
    )


async def _valkey_keys(pattern: str = "*") -> frozenset[str]:
    valkey = Redis.from_url(VALKEY_URL or "")  # pyright: ignore[reportUnknownMemberType]
    try:
        return frozenset([k.decode() async for k in valkey.scan_iter(pattern, count=1000)])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    finally:
        await valkey.aclose()


async def _snapshot(world: World, side: Side) -> Snapshot:
    tenant_id = side.acme.tenant_id
    engine, owner = await _owner(world.database)
    rows: dict[str, tuple[int, str]] = {}
    try:
        async with tenant_transaction(owner, tenant_id) as session:
            for table in await _tenant_tables(session):
                count, digest = (
                    await session.execute(
                        text(
                            f"SELECT count(*), md5(coalesce(string_agg(md5(r::text), '' "  # noqa: S608
                            f"ORDER BY md5(r::text)), '')) FROM {table} r WHERE tenant_id = :t"
                        ),
                        {"t": tenant_id},
                    )
                ).one()
                rows[table] = (count, digest)
    finally:
        await engine.dispose()
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.post(
            f"{QDRANT_URL}/collections/{world.collection_name}/points/scroll",
            json={
                "filter": {"must": [match("tenant_id", str(tenant_id))]},
                "limit": 10_000,
                "with_payload": True,
            },
        )
        points = response.json()["result"]["points"]
    payloads = sorted(json.dumps(p["payload"], sort_keys=True) for p in points)
    files = {
        str(path.relative_to(world.storage)): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in (
            world.storage / "tenants" / str(tenant_id),
            world.storage / "analytics" / str(tenant_id),
        )
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }
    return Snapshot(
        rows=rows,
        points=(len(points), hashlib.sha256("\n".join(payloads).encode()).hexdigest()),
        files=files,
        keys=await _valkey_keys(f"t:{tenant_id}:*"),
    )


@pytest.fixture(scope="module")
def world(database: Database, tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    storage = tmp_path_factory.mktemp("storage")
    model_name = f"test_{uuid.uuid4().hex[:10]}"
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        qdrant_url=QDRANT_URL or "",
        openfga_api_url=OPENFGA_URL or "",
        openfga_api_key=OPENFGA_KEY or "",
        authz_outbox_worker=False,
        ingestion_mode="inline",
        embedding_provider="hash",
        retrieval_min_similarity=0.0,
        embedding_model=model_name,
        embedding_dimensions=64,
        object_storage_path=str(storage),
        transcription_provider="none",
        keycloak_url="http://127.0.0.1:9/auth",  # never the running stack's Keycloak
        google_client_id="test-google-client",
        google_client_secret="test-google-secret",
        slack_client_id="test-slack-client",
        slack_client_secret="test-slack-secret",
        atlassian_client_id="test-atlassian-client",
        atlassian_client_secret="test-atlassian-secret",
        microsoft_client_id="test-microsoft-client",
        microsoft_client_secret="test-microsoft-secret",
    )
    now = datetime.now(UTC)
    license_ = License(
        "Acme Robotics",
        frozenset(FEATURES),
        now - timedelta(days=1),
        now + timedelta(days=30),
        key_id="test",
    )
    keys_before = _run(_valkey_keys())
    a = _run(_seed_side(database, storage))
    b = _run(_seed_side(database, storage))
    extensions = enable_extensions(Edition(license_))
    app = create_app(settings, extensions=extensions)
    try:
        # An address like uvicorn's: the audit log stores the client's IP.
        # Server errors come back as 500 responses, so the sweep reports every route at once.
        with TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False) as client:
            app.state.resources.llm = scripted_llm()
            _build_a(client, a)
            _run(_build_a_rows(database, a))
            _build_b(client, b)
            world = World(
                client=client,
                a=a,
                b=b,
                settings=settings,
                database=database,
                storage=storage,
                collection_name=settings.qdrant_collection,
                a_ids=_run(_a_ids(database, a)),
                keys_before=keys_before,
                enterprise=bool(extensions.routers),
            )
            yield world
    finally:

        async def cleanup() -> None:
            async with httpx.AsyncClient(timeout=15) as http:
                fga = OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)
                for side in (a, b):
                    if side.store:
                        await fga.delete_store(side.store)
                qdrant = QdrantClient(http, QDRANT_URL or "")
                if await qdrant.collection_exists(settings.qdrant_collection):
                    await qdrant.delete_collection(settings.qdrant_collection)

        _run(cleanup())


# --- Every route ---------------------------------------------------------------------------------

NOT_FOUND = frozenset({403, 404})
# Served by workmind_ee when it is installed (the gate installs it: uv sync --extra ee).
ENTERPRISE_ONLY = frozenset(
    {
        "/api/v1/admin/retention",
        "/api/v1/admin/retention/preview",
        "/api/v1/admin/retention/run",
        "/api/v1/admin/audit-events/export.jsonl",
    }
)
Body = Callable[["World"], Any] | Any


@dataclass(frozen=True)
class Probe:
    """One request B makes against A. `params` maps a path parameter to the name of a value
    (default: the parameter's own name); "b:<name>" takes B's value instead (B's own resource,
    A's ids in the body). `expect`: the statuses allowed (empty: any status below 500)."""

    expect: frozenset[int] = frozenset()
    params: Mapping[str, str] = field(default_factory=dict[str, str])
    query: Body = None
    json: Body = None
    content: bytes | None = None
    content_type: str | None = None
    auth: str = "session"  # session (B's owner) | spare (another B session) | token | none
    headers: Mapping[str, str] = field(default_factory=dict[str, str])
    note: str = ""


def deny(**kwargs: Any) -> Probe:
    """A's resource, addressed by its id or key: B must get 403 or 404."""
    return Probe(expect=NOT_FOUND, **kwargs)


def refuse(*statuses: int, **kwargs: Any) -> Probe:
    """B's own resource or a new one, pointing at A's data: refused with one of `statuses`."""
    return Probe(expect=frozenset(statuses), **kwargs)


def own(*statuses: int, **kwargs: Any) -> Probe:
    """B's own data (lists, searches, creations): allowed, but nothing of A may appear."""
    return Probe(expect=frozenset(statuses or (200,)), **kwargs)


def public(*statuses: int, **kwargs: Any) -> Probe:
    return Probe(expect=frozenset(statuses), auth="none", **kwargs)


def a(name: str) -> Callable[[World], str]:
    return lambda w: w.a.values[name]


def tenant_principals(w: World) -> list[str]:
    v = w.a.values
    return [
        f"user:{v['user_id']}",
        f"org:{w.a.acme.tenant_id}",
        f"group:{v['group_id']}",
        f"dept:{v['department_id']}",
    ]


AGENT = {
    "name": "Probe",
    "description": "A probe agent of tenant B.",
    "collections": ["company-handbook"],
}
WORKFLOW = {"steps": [{"id": "gather", "research": "What happened?"}]}
RANGE = {"from": "2026-01-01", "to": "2027-01-01"}

PROBES: dict[tuple[str, str], list[Probe]] = {
    # Infrastructure and documentation: no tenant data at all.
    ("GET", "/api/openapi.json"): [public(200)],
    ("GET", "/api/docs"): [public(200)],
    ("GET", "/docs/oauth2-redirect"): [public(200)],
    ("GET", "/healthz"): [public(200)],
    ("GET", "/readyz"): [public(200, 503)],
    # Sign-in: before a tenant is known.
    ("GET", "/api/auth/login"): [
        public(302, 303, query={"return_to": "/", "login_hint": "x@acme.test"})
    ],
    ("GET", "/api/auth/callback"): [
        public(303, query=lambda w: {"code": "x", "state": w.a.values["oauth_state"]})
    ],
    ("POST", "/api/auth/logout"): [Probe(expect=frozenset({200}), auth="spare")],
    ("POST", "/api/auth/backchannel-logout"): [public(400, content=b"logout_token=forged")],
    ("GET", "/api/v1/system/status"): [own()],
    ("GET", "/api/v1/edition"): [own()],
    # Identity
    ("GET", "/api/v1/me/tokens"): [own()],
    ("POST", "/api/v1/me/tokens"): [own(201, json={"name": "probe", "scopes": ["profile:read"]})],
    ("DELETE", "/api/v1/me/tokens/{token_id}"): [deny()],
    ("GET", "/api/v1/me"): [own()],
    ("GET", "/api/v1/people"): [own(query={"q": CANARY})],
    ("GET", "/api/v1/admin/users"): [own(query={"q": CANARY})],
    ("POST", "/api/v1/admin/users"): [
        own(201, json={"email": "probe@acme.test", "display_name": "Probe"})
    ],
    ("PATCH", "/api/v1/admin/users/{user_id}"): [
        deny(json={"display_name": "pwned"}),
        deny(params={"user_id": "person_id"}, json={"org_role": "admin"}),
    ],
    ("GET", "/api/v1/admin/departments"): [own()],
    ("POST", "/api/v1/admin/departments"): [own(201, json={"slug": "probe-dept", "name": "Probe"})],
    ("PATCH", "/api/v1/admin/departments/{department_id}"): [deny(json={"name": "pwned"})],
    ("DELETE", "/api/v1/admin/departments/{department_id}"): [deny()],
    ("GET", "/api/v1/admin/departments/{department_id}/members"): [deny()],
    ("PUT", "/api/v1/admin/departments/{department_id}/members/{user_id}"): [
        deny(json={"role": "manager"}),
        refuse(404, 422, params={"department_id": "b:department_id"}, json={"role": "member"}),
    ],
    ("DELETE", "/api/v1/admin/departments/{department_id}/members/{user_id}"): [
        deny(),
        refuse(204, 404, params={"department_id": "b:department_id"}),
    ],
    ("GET", "/api/v1/admin/groups"): [own()],
    ("POST", "/api/v1/admin/groups"): [own(201, json={"slug": "probe-group", "name": "Probe"})],
    ("DELETE", "/api/v1/admin/groups/{group_id}"): [deny()],
    ("GET", "/api/v1/admin/groups/{group_id}/members"): [deny()],
    ("PUT", "/api/v1/admin/groups/{group_id}/members/users/{user_id}"): [
        deny(),
        refuse(404, 422, params={"group_id": "b:group_id"}),
    ],
    ("DELETE", "/api/v1/admin/groups/{group_id}/members/users/{user_id}"): [
        deny(),
        refuse(204, 404, params={"group_id": "b:group_id"}),
    ],
    ("PUT", "/api/v1/admin/groups/{group_id}/members/groups/{child_id}"): [
        deny(),
        refuse(404, 422, params={"group_id": "b:group_id"}),
    ],
    ("DELETE", "/api/v1/admin/groups/{group_id}/members/groups/{child_id}"): [
        deny(),
        refuse(204, 404, params={"group_id": "b:group_id"}),
    ],
    ("GET", "/api/v1/admin/audit-events"): [
        own(query=lambda w: {"actor_id": w.a.values["user_id"], "q": CANARY}),
        own(query=lambda w: {"resource_id": w.a.values["document_id"]}),
    ],
    ("GET", "/api/v1/admin/audit-events/export.csv"): [
        own(query=lambda w: {"resource_id": w.a.values["document_id"], "q": CANARY})
    ],
    ("GET", "/api/v1/admin/audit-events/verify"): [own()],
    ("GET", "/api/v1/admin/audit-events/export.jsonl"): [own()],
    # Knowledge
    ("GET", "/api/v1/collections"): [own()],
    ("GET", "/api/v1/collections/{collection_id}"): [deny()],
    ("GET", "/api/v1/collections/{collection_id}/documents"): [deny()],
    ("POST", "/api/v1/collections/{collection_id}/documents"): [
        deny(
            query={"filename": "probe.md"},
            content=b"# Probe\n\nText.\n",
            content_type="text/markdown",
        )
    ],
    ("GET", "/api/v1/documents/{document_id}"): [deny()],
    ("PATCH", "/api/v1/documents/{document_id}"): [deny(json={"trust": "discussion"})],
    ("DELETE", "/api/v1/documents/{document_id}"): [deny()],
    ("GET", "/api/v1/documents/{document_id}/chunks"): [deny()],
    ("GET", "/api/v1/documents/{document_id}/file"): [deny()],
    ("POST", "/api/v1/documents/{document_id}/reindex"): [deny()],
    ("PUT", "/api/v1/documents/{document_id}/owner"): [
        deny(json=lambda w: {"owner_id": w.b.values["user_id"]}),
        refuse(
            404,
            422,
            params={"document_id": "b:document_id"},
            json=lambda w: {"owner_id": w.a.values["user_id"]},
        ),
    ],
    ("POST", "/api/v1/documents/{document_id}/review"): [deny(json={"note": "probe"})],
    ("GET", "/api/v1/documents/{document_id}/acl"): [deny()],
    ("PUT", "/api/v1/documents/{document_id}/acl"): [
        deny(json={"viewers": None}),
        refuse(
            403,
            404,
            422,
            params={"document_id": "b:document_id"},
            json=lambda w: {"viewers": tenant_principals(w)},
        ),
    ],
    ("GET", "/api/v1/admin/identity-mappings"): [own()],
    ("PUT", "/api/v1/admin/identity-mappings"): [
        refuse(
            404,
            422,
            json=lambda w: {
                "external": "group:probe@acme.test",
                "principal": f"group:{w.a.values['group_id']}",
            },
        ),
        own(
            json=lambda w: {
                "external": "group:probe@acme.test",
                "principal": f"group:{w.b.values['group_id']}",
            }
        ),
    ],
    ("DELETE", "/api/v1/admin/identity-mappings/{mapping_id}"): [deny()],
    ("POST", "/api/v1/documents/{document_id}/official"): [deny(json={"note": "probe"})],
    ("GET", "/api/v1/admin/collections"): [own()],
    ("POST", "/api/v1/admin/collections"): [
        refuse(
            404,
            422,
            json=lambda w: {
                "key": "probe-restricted",
                "name": "Probe",
                "scope": "restricted",
                "viewers": tenant_principals(w),
            },
        ),
        refuse(
            404,
            422,
            json=lambda w: {
                "key": "probe-dept",
                "name": "Probe",
                "scope": "department",
                "department_id": w.a.values["department_id"],
            },
        ),
    ],
    ("PATCH", "/api/v1/admin/collections/{collection_id}"): [
        deny(json={"review_interval_days": 30}),
        refuse(
            404,
            422,
            params={"collection_id": "b:collection_id"},
            json=lambda w: {"owner_id": w.a.values["user_id"]},
        ),
    ],
    ("PUT", "/api/v1/admin/collections/{collection_id}/grants"): [
        deny(json={"viewers": [], "editors": []}),
        refuse(
            404,
            422,
            params={"collection_id": "b:ops_collection_id"},
            json=lambda w: {"viewers": tenant_principals(w), "editors": []},
        ),
    ],
    ("GET", "/api/v1/governance/overview"): [own()],
    # Assistant and conversations
    ("POST", "/api/v1/search"): [own(json={"query": f"{CANARY} parental leave", "limit": 20})],
    ("POST", "/api/v1/ask"): [own(json={"question": f"How long is {CANARY} parental leave?"})],
    ("POST", "/api/v1/research"): [own(json={"question": f"{CANARY} parental leave", "tools": []})],
    ("POST", "/api/v1/conversations"): [own(201, json={"title": "probe"})],
    ("GET", "/api/v1/conversations"): [own()],
    ("GET", "/api/v1/conversations/{conversation_id}"): [deny()],
    ("PATCH", "/api/v1/conversations/{conversation_id}"): [deny(json={"title": "pwned"})],
    ("DELETE", "/api/v1/conversations/{conversation_id}"): [deny()],
    ("POST", "/api/v1/conversations/{conversation_id}/messages"): [deny(json={"content": "hello"})],
    ("GET", "/api/v1/runs/{run_id}/events"): [deny()],
    ("POST", "/api/v1/runs/{run_id}/cancel"): [deny()],
    ("POST", "/api/v1/messages/{message_id}/feedback"): [
        deny(json={"rating": "down", "comment": "x"})
    ],
    # Agents and tools
    ("GET", "/api/v1/admin/agents"): [own()],
    ("POST", "/api/v1/admin/agents"): [
        refuse(
            404,
            422,
            json=lambda w: {
                **AGENT,
                "key": "probe-agent",
                "collections": [w.a.values["collection_key"]],
            },
        )
    ],
    ("PUT", "/api/v1/admin/agents/{key}"): [deny(params={"key": "agent_key"}, json=AGENT)],
    ("DELETE", "/api/v1/admin/agents/{key}"): [deny(params={"key": "agent_key"})],
    ("GET", "/api/v1/admin/agents/{key}/versions"): [deny(params={"key": "agent_key"})],
    ("GET", "/api/v1/admin/agents/{key}/versions/{number}"): [deny(params={"key": "agent_key"})],
    ("POST", "/api/v1/admin/agents/{key}/versions/{number}/restore"): [
        deny(params={"key": "agent_key"}, json={})
    ],
    ("POST", "/api/v1/admin/agents/test"): [
        deny(json=lambda w: {"question": "hello", "key": w.a.values["agent_key"]})
    ],
    ("GET", "/api/v1/admin/tools"): [own()],
    ("GET", "/api/v1/agents"): [own()],
    # Connectors
    ("GET", "/api/v1/admin/sources"): [own()],
    ("POST", "/api/v1/admin/sources"): [
        refuse(
            404,
            422,
            json=lambda w: {
                "key": "probe-src",
                "name": "Probe",
                "kind": "github",
                "collection": w.a.values["collection_key"],
                "config": {"repo": "x/y"},
            },
        )
    ],
    ("PUT", "/api/v1/admin/sources/{key}"): [
        deny(
            params={"key": "source_key"},
            json={
                "name": "pwned",
                "config": {"repo": "x/y"},
                "interval_minutes": 60,
                "enabled": True,
            },
        )
    ],
    ("PUT", "/api/v1/admin/sources/{key}/credentials"): [
        deny(params={"key": "source_key"}, json={"token": "ghp_probe"})
    ],
    ("DELETE", "/api/v1/admin/sources/{key}"): [deny(params={"key": "source_key"})],
    ("POST", "/api/v1/admin/sources/{key}/sync"): [deny(params={"key": "source_key"})],
    ("GET", "/api/v1/admin/sources/{key}/oauth/google/start"): [deny(params={"key": "gdrive_key"})],
    ("GET", "/api/v1/admin/sources/{key}/oauth/atlassian/start"): [
        deny(params={"key": "confluence_key"})
    ],
    ("GET", "/api/v1/admin/sources/{key}/oauth/microsoft/start"): [
        deny(params={"key": "sharepoint_key"})
    ],
    ("GET", "/api/v1/admin/sources/stale"): [own(query={"days": 0})],
    ("GET", "/api/v1/admin/sources/options"): [own()],
    ("POST", "/api/v1/admin/sources/{key}/pause"): [deny(params={"key": "source_key"})],
    ("POST", "/api/v1/admin/sources/{key}/resume"): [deny(params={"key": "source_key"})],
    ("GET", "/api/v1/admin/sources/{key}/runs"): [deny(params={"key": "source_key"})],
    ("GET", "/api/v1/admin/sources/{key}/oauth/slack/start"): [deny(params={"key": "slack_key"})],
    ("GET", "/api/v1/admin/sources/stale-documents"): [
        own(query={"days": 1}),
        refuse(404, query=lambda w: {"source": w.a.values["source_key"]}),
    ],
    ("GET", "/api/v1/admin/sources/kinds"): [own()],
    # Another tenant's OAuth state: the callback fails, and A's flow is left alone.
    ("GET", "/api/v1/oauth/google/callback"): [
        own(303, query=lambda w: {"code": "x", "state": w.a.values["oauth_state"]})
    ],
    ("GET", "/api/v1/oauth/slack/callback"): [
        own(303, query=lambda w: {"code": "x", "state": w.a.values["slack_state"]})
    ],
    ("GET", "/api/v1/oauth/atlassian/callback"): [
        own(303, query=lambda w: {"code": "x", "state": w.a.values["atlassian_state"]})
    ],
    ("GET", "/api/v1/oauth/microsoft/callback"): [
        own(303, query=lambda w: {"code": "x", "state": w.a.values["microsoft_state"]})
    ],
    # Approvals, tasks, notifications, workflows
    ("GET", "/api/v1/approvals"): [
        own(query=lambda w: {"conversation_id": w.a.values["conversation_id"]}),
        own(query=lambda w: {"workflow_run_id": w.a.values["workflow_run_id"]}),
    ],
    ("GET", "/api/v1/approvals/{approval_id}"): [deny()],
    ("POST", "/api/v1/approvals/{approval_id}/decision"): [deny(json={"decision": "approve"})],
    ("GET", "/api/v1/tasks"): [own()],
    ("POST", "/api/v1/tasks"): [
        refuse(404, 422, json=lambda w: {"title": "probe", "assignee_id": w.a.values["user_id"]})
    ],
    ("GET", "/api/v1/tasks/{task_id}"): [deny()],
    ("PATCH", "/api/v1/tasks/{task_id}"): [
        deny(json={"status": "done"}),
        refuse(
            404,
            422,
            params={"task_id": "b:task_id"},
            json=lambda w: {"assignee_id": w.a.values["user_id"]},
        ),
    ],
    ("GET", "/api/v1/notifications"): [own()],
    ("POST", "/api/v1/notifications/read"): [
        own(200, 204, json=lambda w: {"ids": [w.a.values["notification_id"]]})
    ],
    ("GET", "/api/v1/admin/workflows"): [own()],
    ("PUT", "/api/v1/admin/workflows/{key}"): [
        own(
            params={"key": "b:probe_flow"},
            json={"key": "probe-flow", "name": "Probe", "definition": WORKFLOW},
        )
    ],
    ("DELETE", "/api/v1/admin/workflows/{key}"): [deny(params={"key": "workflow_key"})],
    ("POST", "/api/v1/admin/workflows/{key}/run"): [deny(params={"key": "workflow_key"})],
    ("GET", "/api/v1/admin/workflows/{key}/runs"): [deny(params={"key": "workflow_key"})],
    ("POST", "/api/v1/admin/workflows/validate"): [
        own(json={"source": "steps:\n  - id: gather\n    research: What happened?\n"})
    ],
    # Automation: preferences, requests and routing, escalation and approver rules, Slack
    ("GET", "/api/v1/me/work-preferences"): [own()],
    ("PUT", "/api/v1/me/work-preferences"): [
        refuse(422, json=lambda w: {"backup_approver_id": w.a.values["user_id"]})
    ],
    ("GET", "/api/v1/requests/options"): [own()],
    ("POST", "/api/v1/requests"): [
        own(201, json={"title": f"{CANARY} request", "category": CANARY}),
    ],
    ("POST", "/api/v1/tasks/{task_id}/route"): [
        deny(json={"department": "finance"}),
        deny(params={"task_id": "request_task_id"}, json={"department": "finance"}),
    ],
    ("POST", "/api/v1/admin/requests/inbound-email"): [
        refuse(
            422,
            json=lambda w: {"from": w.a.values["person_email"], "subject": "probe", "body": "x"},
        )
    ],
    ("GET", "/api/v1/admin/automation"): [own()],
    ("PUT", "/api/v1/admin/automation"): [
        refuse(422, json=lambda w: {"approver_rules": {"send_email": w.a.values["person_email"]}}),
        refuse(422, json={"routing": {"triage_department": f"{CANARY}-dept"}}),
    ],
    ("GET", "/api/v1/admin/routing-rules"): [own()],
    ("PUT", "/api/v1/admin/routing-rules"): [
        refuse(422, json={"rules": [{"department": f"{CANARY}-dept", "keywords": [CANARY]}]})
    ],
    ("POST", "/api/v1/admin/routing/preview"): [own(json={"title": f"{CANARY} request"})],
    ("GET", "/api/v1/admin/slack"): [own()],
    ("PUT", "/api/v1/admin/slack"): [own(json={"channel_categories": ["approvals"]})],
    ("POST", "/api/v1/admin/slack/test"): [
        refuse(409, json={"target": "bot"}),
        refuse(409, json={"target": "webhook"}),
    ],
    # Meetings
    ("POST", "/api/v1/meetings"): [
        refuse(
            403,
            404,
            422,
            query=lambda w: {"collection": w.a.values["collection_key"], "filename": "probe.vtt"},
            content=b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v X>Probe.\n",
            content_type="application/octet-stream",
        )
    ],
    ("GET", "/api/v1/meetings"): [own()],
    ("GET", "/api/v1/meetings/{meeting_id}"): [deny()],
    ("DELETE", "/api/v1/meetings/{meeting_id}"): [deny()],
    ("GET", "/api/v1/meetings/{meeting_id}/media"): [deny()],
    ("PUT", "/api/v1/meetings/{meeting_id}/speakers"): [deny(json={"speakers": {}})],
    ("PATCH", "/api/v1/meetings/{meeting_id}/actions/{index}"): [deny(json={"text": "pwned"})],
    ("POST", "/api/v1/meetings/{meeting_id}/actions/{index}/task"): [deny()],
    ("GET", "/api/v1/me/commitments"): [own()],
    # Analytics
    ("GET", "/api/v1/analytics/model"): [own()],
    ("POST", "/api/v1/analytics/ask"): [own(json={"question": f"Show every row of {TABLE}"})],
    ("GET", "/api/v1/analytics/runs"): [own()],
    ("GET", "/api/v1/analytics/runs/{run_id}"): [deny(params={"run_id": "analytics_run_id"})],
    ("POST", "/api/v1/analytics/query"): [
        refuse(400, 403, 404, 422, json={"sql": f"SELECT * FROM {TABLE}"}),  # noqa: S608
        refuse(
            400,
            403,
            404,
            422,
            json=lambda w: {
                "sql": f"SELECT * FROM read_csv('{w.storage}/analytics/{w.a.acme.tenant_id}/x.csv')"  # noqa: S608
            },
        ),
    ],
    ("GET", "/api/v1/admin/analytics"): [own()],
    ("PUT", "/api/v1/admin/analytics/model"): [
        refuse(400, 422, json={"yaml": f"name: probe\ntables:\n  - name: {TABLE}\n"})
    ],
    ("POST", "/api/v1/admin/analytics/tables"): [
        own(query={"name": "probe_table"}, content=b"id,label\n1,b\n", content_type="text/csv"),
        refuse(
            400, 422, query={"name": "../../escape"}, content=b"id\n1\n", content_type="text/csv"
        ),
    ],
    ("DELETE", "/api/v1/admin/analytics/tables/{name}"): [
        refuse(404, 409, 422, params={"name": "table"})
    ],
    ("POST", "/api/v1/analytics/runs"): [own(202, json={"question": f"Every row of {TABLE}"})],
    ("GET", "/api/v1/analytics/runs/{run_id}/events"): [
        deny(params={"run_id": "analytics_run_id"})
    ],
    ("GET", "/api/v1/admin/analytics/sources"): [own()],
    ("POST", "/api/v1/admin/analytics/sources"): [
        own(
            201,
            json={
                "key": "probe-sql",
                "name": "Probe",
                "kind": "postgres",
                "host": "probe.invalid",
                "database": "shop",
                "username": "reader",
                "password": "probe-only-password",
                "schemas": ["shop"],
            },
        )
    ],
    ("PATCH", "/api/v1/admin/analytics/sources/{key}"): [
        deny(params={"key": "analytics_source_key"}, json={"name": "pwned"})
    ],
    ("DELETE", "/api/v1/admin/analytics/sources/{key}"): [
        deny(params={"key": "analytics_source_key"})
    ],
    ("POST", "/api/v1/admin/analytics/sources/{key}/test"): [
        deny(params={"key": "analytics_source_key"})
    ],
    ("GET", "/api/v1/admin/analytics/sources/{key}/catalog"): [
        deny(params={"key": "analytics_source_key"})
    ],
    ("PUT", "/api/v1/admin/analytics/sources/{key}/model"): [
        deny(params={"key": "analytics_source_key"}, json={"yaml": "name: pwned"})
    ],
    # Models and usage
    ("GET", "/api/v1/admin/models"): [own()],
    ("POST", "/api/v1/admin/models/providers"): [
        own(
            201,
            json={
                "name": "probe",
                "kind": "openai_compatible",
                "base_url": "https://llm.example/v1",
            },
        )
    ],
    ("PATCH", "/api/v1/admin/models/providers/{provider_id}"): [deny(json={"name": "pwned"})],
    ("DELETE", "/api/v1/admin/models/providers/{provider_id}"): [deny()],
    ("POST", "/api/v1/admin/models/providers/{provider_id}/test"): [deny(json={})],
    ("POST", "/api/v1/admin/models/catalog"): [
        refuse(
            404,
            422,
            json=lambda w: {"provider_id": w.a.values["provider_id"], "name": "probe-model"},
        )
    ],
    ("PATCH", "/api/v1/admin/models/catalog/{model_id:path}"): [deny(json={"label": "pwned"})],
    ("DELETE", "/api/v1/admin/models/catalog/{model_id:path}"): [deny()],
    ("PUT", "/api/v1/admin/models/roles"): [
        refuse(404, 422, json=lambda w: {"answer": w.a.values["model_id"]})
    ],
    ("PUT", "/api/v1/admin/models/policy"): [
        refuse(
            404,
            422,
            json=lambda w: {
                "max_external_sensitivity": "internal",
                "local_model": w.a.values["model_id"],
            },
        )
    ],
    ("GET", "/api/v1/usage/summary"): [own(query=RANGE)],
    ("GET", "/api/v1/usage/events"): [
        own(query=lambda w: {**RANGE, "user_id": w.a.values["user_id"]}),
        own(query=lambda w: {**RANGE, "department_id": w.a.values["department_id"]}),
    ],
    ("GET", "/api/v1/usage/export.csv"): [own(query=RANGE)],
    ("GET", "/api/v1/admin/budgets"): [own()],
    ("PUT", "/api/v1/admin/budgets"): [
        refuse(
            404,
            422,
            json=lambda w: {
                "budgets": [
                    {
                        "scope": "user",
                        "subject_id": w.a.values["user_id"],
                        "period": "month",
                        "limit_usd": 1,
                    }
                ]
            },
        ),
        refuse(
            404,
            422,
            json=lambda w: {
                "budgets": [
                    {
                        "scope": "department",
                        "subject_id": w.a.values["department_id"],
                        "period": "month",
                        "limit_usd": 1,
                    }
                ]
            },
        ),
    ],
    # Enterprise (licensed in this test): B's own policy, which keeps everything.
    ("GET", "/api/v1/admin/retention"): [own()],
    ("PUT", "/api/v1/admin/retention"): [own(json={})],
    ("POST", "/api/v1/admin/retention/preview"): [own()],
    ("POST", "/api/v1/admin/retention/run"): [own(json={"confirm": "delete"})],
    # MCP: B's access token; the tools themselves are probed in test_mcp_as_b_never_reaches_a.
    ("POST", "/mcp"): [
        Probe(
            auth="token",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            expect=frozenset({200}),
        )
    ],
    # GET opens a server-sent event stream; asking for JSON only keeps the probe from waiting.
    ("GET", "/mcp"): [Probe(auth="token", headers={"Accept": "application/json"})],
    ("DELETE", "/mcp"): [Probe(auth="token")],
    ("HEAD", "/mcp"): [Probe(auth="token", headers={"Accept": "application/json"})],
}


def app_routes(client: TestClient) -> set[tuple[str, str]]:
    """Every (method, path) the app serves, from app.routes (included routers flattened)."""
    app: FastAPI = client.app  # pyright: ignore[reportAssignmentType]
    routes: set[tuple[str, str]] = set()
    for route in iter_route_contexts(app.routes):
        methods = route.methods or set[str]()
        for method in sorted(methods):
            if method == "HEAD" and "GET" in methods and route.path != "/mcp":
                continue  # Starlette answers HEAD for every GET route
            routes.add((method, route.path or ""))
    return routes


def test_every_route_is_classified(world: World) -> None:
    """A new route fails here until PROBES says how B attacks it."""
    routes = app_routes(world.client)

    assert sorted(routes - set(PROBES)) == [], "routes without a cross-tenant probe"
    assert sorted(set(PROBES) - routes) == [], "probes for routes that no longer exist"


def _resolve(value: Body, world: World) -> Any:
    return value(world) if callable(value) else value


def _path(template: str, probe: Probe, world: World) -> str:
    def fill(found: re.Match[str]) -> str:
        name = probe.params.get(found.group(1), found.group(1))
        side = world.b if name.startswith("b:") else world.a
        return side.values[name.removeprefix("b:")]

    return re.sub(r"\{(\w+)(?::\w+)?\}", fill, template)


def _secrets(world: World) -> list[str]:
    return [
        *PHRASES.values(),
        SECRET_PROVIDER_KEY,
        SECRET_GITHUB_TOKEN,
        SECRET_SLACK_TOKEN,
        SECRET_SLACK_HOOK,
        world.a.token,
    ]


def _leaks(world: World, request_text: str, response_text: str, sent: set[str]) -> list[str]:
    """What of A's appears in a response: any canary phrase or secret not in the request, and any
    id of A's that B never sent (B may legitimately see an id it sent echoed back, now or later,
    e.g. in its own audit log)."""
    found = [s for s in _secrets(world) if s in response_text and s not in request_text]
    ids = set(
        re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", response_text)
    )
    found += sorted((ids & world.a_ids) - sent)
    if world.a.acme.slug in response_text and world.a.acme.slug not in request_text:
        found.append(world.a.acme.slug)
    return found


def _send(world: World, method: str, template: str, probe: Probe) -> tuple[str, Any]:
    client, b = world.client, world.b
    path = _path(template, probe, world)
    headers: dict[str, str] = {}
    if probe.auth == "session":
        headers = b.acme.as_("priya.raman", write=method not in ("GET", "HEAD"))
    elif probe.auth == "spare":
        headers = b.acme.as_("bob", write=True)
    elif probe.auth == "token":
        headers = {"Authorization": f"Bearer {b.token}", **MCP_HEADERS}
    query = _resolve(probe.query, world)
    body = _resolve(probe.json, world)
    content = probe.content
    if body is not None:
        content = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    if probe.content_type:
        headers["Content-Type"] = probe.content_type
    headers.update(probe.headers)
    request_text = f"{path} {json.dumps(query)} {content!r}"
    response = client.request(
        method, path, params=query, content=content, headers=headers, follow_redirects=False
    )
    return request_text, response


def test_tenant_b_never_reaches_tenant_a_through_any_route(world: World) -> None:
    world.before = _run(_snapshot(world, world.a))
    world.llm.requests.clear()
    sent: set[str] = set()
    failures: list[str] = []
    routes = app_routes(world.client)
    for (method, template), probes in PROBES.items():
        if (method, template) not in routes:
            continue  # reported by test_every_route_is_classified
        for number, given in enumerate(probes):
            probe = given
            if not world.enterprise and template in ENTERPRISE_ONLY:
                probe = replace(given, expect=frozenset({402}))  # without the ee package
            request_text, response = _send(world, method, template, probe)
            sent.update(set(re.findall(r"[0-9a-f-]{36}", request_text)) & world.a_ids)
            text_out = f"{response.headers} {response.text}"
            label = f"{method} {template} #{number}"
            if response.status_code >= 500 and response.status_code not in probe.expect:
                failures.append(
                    f"{label}: server error {response.status_code} {response.text[:300]}"
                )
            elif probe.expect and response.status_code not in probe.expect:
                failures.append(
                    f"{label}: status {response.status_code} not in {sorted(probe.expect)}: "
                    f"{response.text[:300]}"
                )
            leaked = _leaks(world, request_text, text_out, sent)
            if leaked:
                failures.append(f"{label}: tenant A's data in the response: {leaked[:5]}")

    # Nothing B did reached A's model: every prompt was built from B's data.
    for _role, llm_request in world.llm.requests:
        prompt = json.dumps(llm_request, default=str)
        leaked = [s for s in _secrets(world) if s in prompt]
        if leaked:
            failures.append(f"model prompt for B contained A's data: {leaked[:5]}")

    after = _run(_snapshot(world, world.a))
    changed = {
        t: (world.before.rows[t], after.rows.get(t))
        for t in world.before.rows
        if world.before.rows[t] != after.rows.get(t)
    }
    if changed:
        failures.append(f"A's rows changed while B probed: {changed}")
    if after.points != world.before.points:
        failures.append(f"A's points changed: {world.before.points} -> {after.points}")
    if after.files != world.before.files:
        failures.append("A's files changed")
    if after.keys != world.before.keys:
        failures.append(
            f"A's Valkey keys changed: +{sorted(after.keys - world.before.keys)} "
            f"-{sorted(world.before.keys - after.keys)}"
        )
    assert failures == [], "\n".join(failures)


# The detector itself: the same requests, made by A's own people, do return A's data. Without
# this, a probe pointing at nothing would pass the sweep for the wrong reason.
A_OWNER = {
    "token_id": "alice",
    "document_id": "alice",
    "conversation_id": "alice",
    "run_id": "alice",
    "message_id": "alice",
    "task_id": "alice",
    "meeting_id": "olivia.grant",
    "approval_id": "priya.raman",
    "analytics_run_id": "carol",
}


# A's meeting is a transcript: without a speech-to-text model in tests there is no recording.
# A's analytics answer was stored, not streamed, so it has no event stream; A's SQL source
# points at no database, so its catalog is unavailable to A too (B gets 404 for both).
NO_A_RECORD = frozenset(
    {
        "/api/v1/meetings/{meeting_id}/media",
        "/api/v1/analytics/runs/{run_id}/events",
        "/api/v1/admin/analytics/sources/{key}/catalog",
    }
)


def test_the_probes_address_real_data_of_a(world: World) -> None:
    client, acme = world.client, world.a.acme
    found = 0
    for (method, template), probes in PROBES.items():
        for probe in probes:
            if method != "GET" or probe.expect != NOT_FOUND or template in NO_A_RECORD:
                continue
            first = re.findall(r"\{(\w+)", template)[0]
            name = probe.params.get(first, first)
            person = A_OWNER.get(name, "priya.raman")
            path = _path(template, probe, world)
            response = client.get(path, headers=acme.as_(person), follow_redirects=False)
            # 302: the OAuth start redirects A's admin to Google.
            assert response.status_code in (200, 302), (
                f"{template} as A's {person}: {response.text}"
            )
            found += bool(_leaks(world, path, response.text, set()))
    assert found >= 10  # most of A's records name a phrase or an id the request did not


# --- MCP ----------------------------------------------------------------------------------------


def _mcp(world: World, tool: str, **arguments: Any) -> str:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    response = world.client.post(
        "/mcp",
        content=json.dumps(body),
        headers={**MCP_HEADERS, "Authorization": f"Bearer {world.b.token}"},
    )
    assert response.status_code == 200, response.text
    return response.text


def test_mcp_as_b_never_reaches_a(world: World) -> None:
    searched = _mcp(world, "search_knowledge", query=f"{CANARY} parental leave", limit=12)
    collections = _mcp(world, "list_collections")
    model = _mcp(world, "describe_data")

    assert "20 weeks" in searched  # B's own document is found
    for text_out in (searched, collections, model):
        assert _leaks(world, CANARY, text_out, set()) == []
        assert TABLE not in text_out


def test_another_tenants_token_does_not_open_mcp_for_a_forged_slug(world: World) -> None:
    """The token names its tenant; changing the slug sends it to a tenant without that hash."""
    secret = world.b.token.rsplit("_", 1)[1]
    forged = f"wm_pat_{world.a.acme.slug}_{secret}"
    response = world.client.post(
        "/mcp",
        content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
        headers={**MCP_HEADERS, "Authorization": f"Bearer {forged}"},
    )
    assert response.status_code == 401
    me = world.client.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"})
    assert me.status_code == 401


# --- Qdrant -------------------------------------------------------------------------------------


async def test_qdrant_queries_as_b_never_return_a_points(world: World) -> None:
    a_tenant, b_tenant = world.a.acme.tenant_id, world.b.acme.tenant_id
    v = world.a.values
    sparse = encode_query(f"{CANARY} parental leave").as_qdrant()
    crafted: list[dict[str, Any]] = [
        {},
        {"should": [match("tenant_id", str(a_tenant))]},
        {"must": [match("tenant_id", str(a_tenant))]},
        {"must": [match("document_id", v["document_id"])]},
        {"must": [match_any("collection_id", [v["collection_id"]])]},
        {"must_not": [match("tenant_id", str(b_tenant))]},
        {"must": [{"should": [match("tenant_id", str(a_tenant))]}]},
    ]
    async with httpx.AsyncClient(timeout=30) as http:
        qdrant = QdrantClient(http, QDRANT_URL or "")
        name = world.collection_name
        for flt in crafted:
            hits = await qdrant.query(
                name, tenant_id=b_tenant, query=sparse, using="bm25", flt=flt, limit=100
            )
            assert {h["payload"]["tenant_id"] for h in hits} <= {str(b_tenant)}, flt
            assert CANARY not in json.dumps(hits), flt
            counted = await qdrant.count(name, flt, tenant_id=b_tenant)
            assert counted <= await qdrant.count(name, tenant_id=b_tenant), flt
        # Deleting or relabelling what a filter naming A's document matches leaves A alone.
        before = await qdrant.count(name, tenant_id=a_tenant)
        await qdrant.delete(
            name, {"must": [match("document_id", v["document_id"])]}, tenant_id=b_tenant
        )
        await qdrant.set_payload(
            name,
            {"acl": ["org:nobody"]},
            {"must": [match("document_id", v["document_id"])]},
            tenant_id=b_tenant,
        )
        assert before > 0
        assert await qdrant.count(name, tenant_id=a_tenant) == before
        a_points = await qdrant.query(
            name,
            tenant_id=a_tenant,
            query=sparse,
            using="bm25",
            flt={"must": [match("document_id", v["document_id"])]},
            limit=10,
        )
        assert a_points
        assert all("org:nobody" not in p["payload"]["acl"] for p in a_points)


def test_retrieval_as_b_with_a_principals_and_scope_finds_nothing_of_a(world: World) -> None:
    """Even a context that wrongly carried A's principals, scoped to A's collection, stays in B:
    the tenant condition comes from the context's tenant, not from its principals."""
    app: Any = world.client.app
    retriever = app.state.resources.retriever
    v = world.a.values
    forged = RequestContext(
        request_id="t",
        tenant_id=world.b.acme.tenant_id,
        user_id=world.b.acme.users["priya.raman"],
        org_role="owner",
        principals=frozenset(
            {
                f"org:{world.a.acme.tenant_id}",
                f"user:{world.a.acme.users['alice']}",
                f"user:{v['user_id']}",
            }
        ),
        auth_method="session",
    )

    async def search(collection_ids: list[str] | None) -> Any:
        return await retriever.search(
            forged, f"{CANARY} parental leave", limit=20, collection_ids=collection_ids
        )

    portal: Any = world.client.portal  # pyright: ignore[reportAttributeAccessIssue]
    for scope in (None, [v["collection_id"]]):
        result = portal.call(search, scope)
        assert result.passages == [], scope
        assert result.candidates == 0, scope


# --- Valkey -------------------------------------------------------------------------------------


def test_every_valkey_key_written_is_under_a_tenant_prefix(world: World) -> None:
    a_tenant, b_tenant = world.a.acme.tenant_id, world.b.acme.tenant_id
    written = _run(_valkey_keys()) - world.keys_before
    known = {a_tenant, b_tenant}

    stray = [
        k for k in written if key_tenant(k) not in known and not k.startswith(GLOBAL_KEY_PREFIXES)
    ]
    b_keys = [k for k in written if key_tenant(k) == b_tenant]
    crossed = [k for k in b_keys if any(i in k for i in world.a_ids)]
    a_keys = {k for k in written if key_tenant(k) == a_tenant}
    v = world.a.values

    assert stray == [], "keys outside every tenant prefix"
    assert crossed == [], "B's keys naming A's records"
    assert f"t:{a_tenant}:run:{v['run_id']}" in a_keys
    assert f"t:{a_tenant}:run:{v['run_id']}:events" in a_keys
    assert f"t:{a_tenant}:sessions:by-expiry" in a_keys
    # B's callback with A's state neither found nor consumed A's OAuth flow.
    assert f"t:{a_tenant}:oauth:google:{v['oauth_state']}" in a_keys
    assert f"t:{a_tenant}:oauth:slack:{v['slack_state']}" in a_keys
    assert f"t:{a_tenant}:oauth:atlassian:{v['atlassian_state']}" in a_keys
    assert f"t:{a_tenant}:oauth:microsoft:{v['microsoft_state']}" in a_keys
    assert any(k.startswith(f"t:{a_tenant}:usage:spent:") for k in a_keys)


# --- OpenFGA ------------------------------------------------------------------------------------


async def test_openfga_checks_for_a_objects_in_b_store_fail(world: World) -> None:
    a, b = world.a, world.b
    a_alice = f"user:{a.acme.users['alice']}"
    b_priya = f"user:{b.acme.users['priya.raman']}"
    a_collection = f"collection:{a.values['collection_id']}"
    a_document = f"document:{a.values['document_id']}"
    async with httpx.AsyncClient(timeout=15) as http:
        fga = OpenFgaClient(http, OPENFGA_URL or "", OPENFGA_KEY)

        assert await fga.check(a.store, RelationTuple(a_alice, "viewer", a_collection))
        assert await fga.check(a.store, RelationTuple(a_alice, "viewer", a_document))
        for user in (a_alice, b_priya):
            for obj in (a_collection, a_document):
                for relation in ("viewer", "editor"):
                    assert not await fga.check(b.store, RelationTuple(user, relation, obj))
        visible = await fga.list_objects(
            b.store, user=b_priya, relation="viewer", type="collection"
        )
        assert a_collection not in visible
        assert visible  # B's own collections
        b_tuples = json.dumps([t.key() for t in await fga.read_all(b.store)])
        assert not [i for i in world.a_ids if i in b_tuples]


# --- Celery -------------------------------------------------------------------------------------


@pytest.fixture
def worker(world: World, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The Celery tasks, run in this process (no broker) against the test database."""
    from workmind.workers import celery_app

    monkeypatch.setattr(celery_app, "settings", world.settings)
    return celery_app


async def _a_records(world: World) -> dict[str, str]:
    engine = create_engine(world.database.owner_url)
    try:
        async with tenant_transaction(create_session_factory(engine), world.a.acme.tenant_id) as s:
            document = await s.get(Document, uuid.UUID(world.a.values["document_id"]))
            source_id = await s.scalar(
                select(Source.id).where(Source.key == world.a.values["source_key"])
            )
            assert document is not None
            return {
                "status": document.status,
                "version_id": str(document.current_version_id),
                "source_id": str(source_id),
            }
    finally:
        await engine.dispose()


def test_tenant_tasks_take_the_tenant_first(worker: Any) -> None:
    tasks = {
        name: task
        for name, task in worker.app.tasks.items()
        if name.startswith("workmind.") and inspect.signature(task.run).parameters
    }
    assert set(tasks) == {
        worker.INGEST_TASK,
        worker.SYNC_TASK,
        worker.WORKFLOW_RUN_TASK,
        worker.MEETING_TASK,
    }
    for name, task in tasks.items():
        assert next(iter(inspect.signature(task.run).parameters)) == "tenant_id", name


@pytest.mark.parametrize("tenant", ["", "   ", "not-a-uuid", None])
def test_tenant_tasks_refuse_to_run_without_a_tenant(worker: Any, tenant: Any) -> None:
    some = str(uuid.uuid4())
    for call in (
        lambda: worker.ingest_document(tenant, some, some),
        lambda: worker.sync_source_task(tenant, some),
        lambda: worker.start_workflow_run(tenant, some),
        lambda: worker.process_meeting_task(tenant, some),
    ):
        with pytest.raises(worker.TenantRequiredError):
            call()


def test_tenant_tasks_never_cross_into_another_tenant(world: World, worker: Any) -> None:
    before = _run(_snapshot(world, world.a))
    records = _run(_a_records(world))
    v, b = world.a.values, str(world.b.acme.tenant_id)
    gone = str(uuid.uuid4())  # a deleted (or never created) tenant

    assert worker.ingest_document(b, v["document_id"], records["version_id"]) is None
    assert worker.sync_source_task(b, records["source_id"])["status"] == "skipped"
    assert worker.start_workflow_run(b, v["workflow_run_id"]) is None
    assert worker.process_meeting_task(b, v["meeting_id"]) is None
    assert worker.ingest_document(gone, v["document_id"], records["version_id"]) is None
    assert worker.sync_source_task(gone, records["source_id"])["status"] == "skipped"
    assert worker.start_workflow_run(gone, v["workflow_run_id"]) is None
    assert worker.process_meeting_task(gone, v["meeting_id"]) is None

    after = _run(_snapshot(world, world.a))
    assert after.rows == before.rows
    assert after.points == before.points
    assert after.files == before.files
    assert _run(_a_records(world))["status"] == "ready"


# --- Object storage and analytics files ---------------------------------------------------------


def test_files_live_under_per_tenant_paths(world: World) -> None:
    a_tenant, b_tenant = str(world.a.acme.tenant_id), str(world.b.acme.tenant_id)
    files = [p.relative_to(world.storage) for p in world.storage.rglob("*") if p.is_file()]

    for path in files:
        top, tenant = path.parts[0], path.parts[1]
        assert top in ("tenants", "analytics"), path
        assert tenant in (a_tenant, b_tenant), path
    a_objects = [p for p in files if p.parts[:2] == ("tenants", a_tenant)]
    a_datasets = [p for p in files if p.parts[:2] == ("analytics", a_tenant)]
    assert len(a_objects) >= 2  # the document and the meeting recording
    assert a_datasets
    assert all(p.suffix == ".duckdb" for p in a_datasets)


@pytest.mark.parametrize(
    "key",
    [
        "../outside",
        "tenants/../../outside",
        "/etc/passwd",
        "tenants/{a}/../{b}/objects/x",
        "tenants/{b}/objects/aa/x",
        "tenants/{a}//objects",
    ],
)
def test_storage_keys_of_another_tenant_or_outside_the_root_are_refused(
    world: World, key: str
) -> None:
    a_tenant, b_tenant = world.a.acme.tenant_id, world.b.acme.tenant_id
    key = key.format(a=a_tenant, b=b_tenant)
    storage = LocalObjectStorage(world.storage)

    with pytest.raises(ForeignObjectKeyError):
        owned_key(a_tenant, key)
    if world.storage.resolve() not in (world.storage / key).resolve().parents:
        with pytest.raises(ValueError, match="invalid storage key"):
            _run(storage.get(key))


def test_names_never_become_paths(world: World) -> None:
    tenant = world.a.acme.tenant_id
    with pytest.raises(ValueError, match="SHA-256"):
        object_key(tenant, "../../etc/passwd")
    with pytest.raises(ValueError, match="invalid dataset version"):
        dataset.dataset_path(world.storage / "analytics", tenant, "../../../x")
    with pytest.raises(dataset.DatasetError):
        dataset.check_table_name("../escape")


def test_a_row_pointing_at_another_tenants_file_never_serves_it(world: World) -> None:
    """If a version row of B ever named A's object (a bug, a bad restore), B still gets no byte
    of it: every key read from the database is checked against the reader's tenant."""
    a_key = next(
        str(p.relative_to(world.storage))
        for p in sorted((world.storage / "tenants" / str(world.a.acme.tenant_id)).rglob("*"))
        if p.is_file() and PHRASES["body"].encode() in p.read_bytes()
    )
    b_document = world.b.values["document_id"]

    async def point_b_at_a() -> str:
        engine = create_engine(world.database.owner_url)
        try:
            async with tenant_transaction(
                create_session_factory(engine), world.b.acme.tenant_id
            ) as s:
                document = await s.get(Document, uuid.UUID(b_document))
                assert document is not None
                version = await s.get(DocumentVersion, document.current_version_id)
                assert version is not None
                original, version.storage_key = version.storage_key, a_key
                return original
        finally:
            await engine.dispose()

    async def restore(original: str) -> None:
        engine = create_engine(world.database.owner_url)
        try:
            async with tenant_transaction(
                create_session_factory(engine), world.b.acme.tenant_id
            ) as s:
                document = await s.get(Document, uuid.UUID(b_document))
                assert document is not None
                version = await s.get(DocumentVersion, document.current_version_id)
                assert version is not None
                version.storage_key = original
        finally:
            await engine.dispose()

    original = _run(point_b_at_a())
    try:
        response = world.client.get(
            f"/api/v1/documents/{b_document}/file", headers=world.b.acme.as_("alice")
        )
    finally:
        _run(restore(original))

    assert response.status_code == 500
    assert PHRASES["body"] not in response.text

"""Agent configuration over real HTTP and PostgreSQL: versions, restore, refused tools and
models, audit events, seeding, the legal collection, and changes taking effect on the next
question in every process."""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.fakes import FakeLLM
from tests.integration.conftest import (
    ACME,
    ADMIN_URL,
    API_DIR,
    APP_ROLE,
    OWNER_ROLE,
    TEST_DB,
    VALKEY_URL,
    Acme,
    Database,
    SessionFactory,
    seed_acme,
)
from workmind.core.tenancy import tenant_transaction
from workmind.modules.agents.models import AgentDefinition, AgentDefinitionVersion
from workmind.modules.agents.planning import FastRouter, PlanOut, load_agents, plan_question
from workmind.modules.governance.models import AuditEvent
from workmind.modules.identity.models import User
from workmind.modules.knowledge.models import Collection, CollectionGrant
from workmind.providers.embeddings import HashEmbeddings
from workmind.seed.apply import seed_database
from workmind.seed.company import load_company

BASE = "/api/v1/admin/agents"
LEGAL: dict[str, Any] = {
    "key": "legal",
    "name": "Legal agent",
    "description": (
        "Contracts and compliance: who may sign which contract, NDAs, supplier contract "
        "reviews, data processing agreements, customer terms, export control, records "
        "retention, IP and anti-bribery."
    ),
    "collections": ["legal-contracts"],
    "examples": ["Who has to sign a supplier contract worth 80,000 euros?"],
    "note": "First version",
}


def create(api: TestClient, acme: Acme, body: dict[str, Any] = LEGAL) -> dict[str, Any]:
    response = api.post(BASE, json=body, headers=acme.as_("erin", write=True))
    assert response.status_code == 201, response.text
    return response.json()


def update(api: TestClient, acme: Acme, key: str, **changes: Any) -> Any:
    current = next(
        a for a in api.get(BASE, headers=acme.as_("erin")).json()["items"] if a["key"] == key
    )
    body = {k: v for k, v in current.items() if k not in ("id", "key", "version", "updated_at")}
    return api.put(f"{BASE}/{key}", json={**body, **changes}, headers=acme.as_("erin", write=True))


async def audit_actions(sessions: SessionFactory, acme: Acme, resource: str) -> list[str]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        rows = await session.scalars(
            select(AuditEvent.action)
            .where(AuditEvent.resource_id == resource)
            .order_by(AuditEvent.seq)
        )
        return list(rows)


async def test_every_create_update_and_restore_writes_a_version(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    created = create(api, acme)
    edited = update(
        api,
        acme,
        "legal",
        examples=[*LEGAL["examples"], "Can I use the standard NDA without legal review?"],
        tools=["draft_email"],
        max_tool_calls=2,
        model="system:claude-sonnet-5-5",
        note="More examples; may draft e-mails",
    )
    history = api.get(f"{BASE}/legal/versions", headers=acme.as_("erin")).json()
    first = api.get(f"{BASE}/legal/versions/1", headers=acme.as_("erin")).json()
    restored = api.post(
        f"{BASE}/legal/versions/1/restore", json={}, headers=acme.as_("erin", write=True)
    )
    after = api.get(f"{BASE}/legal/versions", headers=acme.as_("erin")).json()
    missing = api.get(f"{BASE}/legal/versions/9", headers=acme.as_("erin"))

    assert created["version"] == 1
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2
    assert (edited.json()["tools"], edited.json()["model"]) == (
        ["draft_email"],
        "system:claude-sonnet-5-5",
    )
    assert [v["version"] for v in history["items"]] == [2, 1]  # newest first
    assert history["items"][0]["note"] == "More examples; may draft e-mails"
    assert history["items"][0]["author"]["name"] == "Erin Takahashi"
    assert history["items"][0]["current"] is True
    assert first["snapshot"]["examples"] == LEGAL["examples"]
    assert first["snapshot"]["tools"] == []
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3
    assert (restored.json()["tools"], restored.json()["model"]) == ([], None)
    assert after["items"][0]["note"] == "Restored version 1"
    assert {k: v for k, v in after["items"][0]["snapshot"].items()} == first["snapshot"]
    assert missing.status_code == 404
    assert await audit_actions(app_sessions, acme, "legal") == [
        "admin.agent.created",
        "admin.agent.updated",
        "admin.agent.restored",
    ]


async def test_versions_go_with_a_deleted_agent_and_stay_in_their_tenant(
    api: TestClient, acme: Acme, owner_sessions: SessionFactory, app_sessions: SessionFactory
) -> None:
    other = await seed_acme(owner_sessions)
    create(api, acme)

    foreign = api.get(f"{BASE}/legal/versions", headers=other.as_("erin"))
    deleted = api.delete(f"{BASE}/legal", headers=acme.as_("erin", write=True))
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        left = await session.scalar(
            select(func.count())
            .select_from(AgentDefinitionVersion)
            .join(AgentDefinition, AgentDefinition.id == AgentDefinitionVersion.agent_id)
            .where(AgentDefinition.key == "legal")
        )

    assert foreign.status_code == 404  # another tenant's agent does not exist for them
    assert deleted.status_code == 204
    assert left == 0


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"tools": ["send_email"]}, "tool_not_allowed"),  # external: needs an approval
        ({"tools": ["create_task"]}, "tool_not_allowed"),  # write: needs an approval
        ({"tools": ["search_knowledge"]}, "tool_not_allowed"),  # built in, scoped
        ({"tools": ["no_such_tool"]}, "unknown_tool"),
        ({"model": "claude-sonnet-5-5"}, "validation_error"),  # not a catalog id
        ({"max_tool_calls": 6}, "validation_error"),
        ({"max_output_tokens": 100}, "validation_error"),
    ],
)
def test_tools_that_change_things_and_malformed_settings_are_refused(
    api: TestClient, acme: Acme, changes: dict[str, Any], code: str
) -> None:
    response = api.post(BASE, json={**LEGAL, **changes}, headers=acme.as_("erin", write=True))

    assert response.status_code == 422
    assert response.json()["code"] == code
    if changes.get("tools") == ["send_email"]:
        assert "cannot wait for approvals" in response.json()["detail"]


def test_the_tool_list_says_which_tools_an_agent_may_have(api: TestClient, acme: Acme) -> None:
    tools = {t["name"]: t for t in api.get("/api/v1/admin/tools", headers=acme.as_("erin")).json()}

    assert tools["draft_email"]["for_agents"] is True
    assert tools["send_email"]["for_agents"] is False
    assert "approval" in tools["send_email"]["not_for_agents_reason"]
    assert tools["search_knowledge"]["for_agents"] is False


def test_only_administrators_configure_or_test_agents(api: TestClient, acme: Acme) -> None:
    write = acme.as_("bob", write=True)

    assert api.get(f"{BASE}/hr/versions", headers=acme.as_("bob")).status_code == 403
    assert api.post(f"{BASE}/test", json={"question": "q"}, headers=write).status_code == 403
    assert api.post(f"{BASE}/hr/versions/1/restore", headers=write).status_code == 403


async def test_seeded_agents_start_at_version_one_and_reseeding_adds_none(
    owner_sessions: SessionFactory, app_sessions: SessionFactory
) -> None:
    spec = load_company(ACME)
    slug = f"acme-{uuid.uuid4().hex[:8]}"
    spec = spec.model_copy(update={"company": spec.company.model_copy(update={"slug": slug})})
    first = await seed_database(spec, owner_sessions, {})
    await seed_database(spec, owner_sessions, {})  # unchanged file: no new versions
    changed = spec.model_copy(
        update={
            "agents": [
                a.model_copy(update={"examples": [*a.examples, "Extra?"]}) if a.key == "hr" else a
                for a in spec.agents
            ]
        }
    )
    await seed_database(changed, owner_sessions, {})

    async with tenant_transaction(app_sessions, first.tenant_id) as session:
        rows = (
            await session.execute(
                select(AgentDefinition.key, AgentDefinition.version, func.count())
                .join(AgentDefinitionVersion, AgentDefinitionVersion.agent_id == AgentDefinition.id)
                .group_by(AgentDefinition.key, AgentDefinition.version)
            )
        ).all()
        note = await session.scalar(
            select(AgentDefinitionVersion.note).where(AgentDefinitionVersion.version == 1).limit(1)
        )

    versions = {key: (version, count) for key, version, count in rows}
    assert versions["hr"] == (2, 2)  # the file changed it once
    assert versions["support"] == (1, 1)
    assert "legal" not in versions  # created in the UI, never seeded
    assert note == "Seeded from company.yaml"


async def test_the_legal_collection_is_for_members_not_guests_and_owned_by_management(
    acme: Acme, app_sessions: SessionFactory
) -> None:
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        legal = (
            await session.scalars(select(Collection).where(Collection.key == "legal-contracts"))
        ).one()
        grants = {
            (g.relation, g.principal)
            for g in await session.scalars(
                select(CollectionGrant).where(CollectionGrant.collection_id == legal.id)
            )
        }
        owner = await session.get(User, legal.owner_id)

    assert (legal.scope, legal.default_trust) == ("enterprise", "official")
    assert ("viewer", f"org:{acme.tenant_id}") in grants
    assert not any(p == f"user:{acme.users['mallory']}" for _, p in grants)  # the guest
    assert owner is not None
    assert owner.email == "priya.raman@acme.test"


class PlannerSpy(FakeLLM):
    """Plans "single" for the first listed agent and records what the planner was shown."""

    def __init__(self) -> None:
        super().__init__(structured_script=self._plan)
        self.listings: list[str] = []

    def _plan(self, request: Any) -> PlanOut:
        self.listings.append(request.system)
        return PlanOut(mode="direct", subtasks=[])


async def test_a_new_edited_or_disabled_agent_takes_effect_on_the_next_question_everywhere(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    """Two routers stand for two API processes (or a worker): each warms its own cache, and
    neither is told about the change. Both read the definitions per question."""
    if not VALKEY_URL:
        pytest.skip("set TEST_VALKEY_URL to run")
    processes = [FastRouter(HashEmbeddings(256)), FastRouter(HashEmbeddings(256))]
    question = "Who has to sign a supplier contract worth 80,000 euros?"

    async def route(router: FastRouter) -> str | None:
        return await router.route(question, await load_agents(app_sessions, acme.tenant_id))

    before = [await route(r) for r in processes]
    create(api, acme)
    created = [await route(r) for r in processes]
    update(api, acme, "legal", enabled=False, note="Paused")
    disabled = [await route(r) for r in processes]
    update(api, acme, "legal", enabled=True, description="Robot maintenance schedules only.")
    planner = PlannerSpy()
    await plan_question(planner, None, question, await load_agents(app_sessions, acme.tenant_id))

    assert before == [None, None]  # no agent is close enough without legal
    assert created == ["legal", "legal"]  # its examples now match, in both processes
    assert disabled == [None, None]
    assert '"legal": Robot maintenance schedules only.' in planner.listings[0]


def _alembic(url: str, target: str) -> None:
    env = {**os.environ, "MIGRATIONS_DATABASE_URL": url, "DB_APP_ROLE": APP_ROLE}
    result = subprocess.run(  # noqa: S603  (fixed arguments, our own migrations)
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def test_upgrading_gives_every_existing_agent_version_one(database: Database) -> None:
    """Migration 0013 backfills versions under forced RLS, once per tenant. In its own
    database, whose schema starts at 0012 with an agent in it."""
    assert ADMIN_URL is not None
    name = f"{TEST_DB}_upgrade"
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {name} OWNER {OWNER_ROLE}")
    finally:
        await admin.close()
    url = database.owner_url.rsplit("/", 1)[0] + f"/{name}"
    try:
        await asyncio.to_thread(_alembic, url, "0012")
        owner = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"))
        try:
            tenant = await owner.fetchval(
                "INSERT INTO tenants (slug, name) VALUES ('old', 'Old Co') RETURNING id"
            )
            async with owner.transaction():
                await owner.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
                await owner.execute(
                    "INSERT INTO agent_definitions (tenant_id, key, name, description, "
                    "collection_keys, examples) VALUES ($1, 'hr', 'HR agent', 'People things.', "
                    "'{company-handbook}', '{Leave?}')",
                    tenant,
                )
            await asyncio.to_thread(_alembic, url, "head")
            async with owner.transaction():
                await owner.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
                row = await owner.fetchrow(
                    "SELECT a.version AS current, v.version, v.snapshot::text AS snapshot, "
                    "v.note FROM agent_definitions a "
                    "JOIN agent_definition_versions v ON v.agent_id = a.id"
                )
        finally:
            await owner.close()
    finally:
        admin = await asyncpg.connect(ADMIN_URL)
        try:
            await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        finally:
            await admin.close()

    assert row is not None
    assert (row["current"], row["version"]) == (1, 1)
    assert row["note"] == "Definition before versioning"
    assert json.loads(row["snapshot"]) == {
        "key": "hr",
        "name": "HR agent",
        "description": "People things.",
        "instructions": "",
        "collections": ["company-handbook"],
        "examples": ["Leave?"],
        "tools": [],
        "model": None,
        "max_searches": 2,
        "max_tool_calls": 0,
        "max_output_tokens": 6000,
        "enabled": True,
    }

"""OpenFGA stays in step with PostgreSQL. Needs TEST_OPENFGA_URL and TEST_OPENFGA_API_KEY
(the compose OpenFGA: http://localhost:8401, key dev-only-openfga-key)."""

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update

from tests.integration.conftest import SessionFactory
from workmind.core.tenancy import global_transaction, tenant_transaction
from workmind.modules.authz.openfga import OpenFgaClient, RelationTuple
from workmind.modules.authz.sync import (
    enqueue,
    ensure_store,
    org_object,
    process_outbox,
    reconcile,
    sync_tenants,
)
from workmind.modules.identity.models import Group, GroupMember, Tenant, User
from workmind.modules.identity.service import load_identity
from workmind.modules.knowledge.acl import acl_principals
from workmind.modules.knowledge.models import Collection, CollectionGrant
from workmind.seed.apply import seed_database
from workmind.seed.company import load_company

OPENFGA_URL = os.environ.get("TEST_OPENFGA_URL")
pytestmark = pytest.mark.skipif(not OPENFGA_URL, reason="set TEST_OPENFGA_URL to run")
ACME = Path(__file__).resolve().parents[4] / "seed" / "acme" / "company.yaml"


@dataclass
class SyncedAcme:
    tenant_id: uuid.UUID
    store_id: str
    users: dict[str, uuid.UUID]  # first part of the email -> id

    def user(self, name: str) -> str:
        return f"user:{self.users[name]}"


@pytest.fixture
async def fga() -> AsyncIterator[OpenFgaClient]:
    assert OPENFGA_URL is not None
    async with httpx.AsyncClient(timeout=15) as http:
        yield OpenFgaClient(http, OPENFGA_URL, os.environ.get("TEST_OPENFGA_API_KEY"))


@pytest.fixture
async def acme(owner_sessions: SessionFactory, fga: OpenFgaClient) -> AsyncIterator[SyncedAcme]:
    spec = load_company(ACME)
    spec = spec.model_copy(
        update={"company": spec.company.model_copy(update={"slug": f"acme-{uuid.uuid4().hex[:8]}"})}
    )
    seeded = await seed_database(spec, owner_sessions, {})
    store_id = await ensure_store(owner_sessions, fga, seeded.tenant_id)
    await reconcile(owner_sessions, fga, seeded.tenant_id, store_id)
    async with tenant_transaction(owner_sessions, seeded.tenant_id) as session:
        users = {u.email.split("@")[0]: u.id for u in await session.scalars(select(User))}
    yield SyncedAcme(tenant_id=seeded.tenant_id, store_id=store_id, users=users)
    await fga.delete_store(store_id)


async def _allowed(
    fga: OpenFgaClient, acme: SyncedAcme, user: str, relation: str, obj: str
) -> bool:
    return await fga.check(acme.store_id, RelationTuple(user, relation, obj))


async def test_organization_roles_follow_the_company_file(
    fga: OpenFgaClient, acme: SyncedAcme
) -> None:
    org = org_object(acme.tenant_id)
    questions = {
        ("priya.raman", "owner"): True,
        ("erin", "can_manage"): True,
        ("alice", "can_manage"): False,
        ("alice", "member"): True,
        ("amara.diallo", "can_view_audit"): True,
        ("amara.diallo", "member"): False,  # auditors read audit logs, not knowledge
        ("mallory", "member"): False,  # the contractor is a guest
    }
    answers = await fga.batch_check(
        acme.store_id, [RelationTuple(acme.user(name), rel, org) for name, rel in questions]
    )
    assert list(answers.values()) == list(questions.values())


async def test_postgres_principals_match_openfga_for_every_user(
    app_sessions: SessionFactory, fga: OpenFgaClient, acme: SyncedAcme
) -> None:
    # Lock 1 (principals from PostgreSQL) and lock 2 (OpenFGA) must agree on memberships.
    for name, user_id in acme.users.items():
        async with tenant_transaction(app_sessions, acme.tenant_id) as session:
            identity = await load_identity(session, user_id)
        assert identity is not None
        from_postgres = {
            "group": sorted(f"group:{g.id}" for g in identity.groups),
            "department": sorted(f"department:{d.id}" for d, _ in identity.departments),
        }
        from_openfga = {
            kind: await fga.list_objects(
                acme.store_id, user=acme.user(name), relation="member", type=kind
            )
            for kind in ("group", "department")
        }
        assert from_openfga == from_postgres, name


async def test_reconcile_repairs_drift_and_is_then_a_no_op(
    owner_sessions: SessionFactory, fga: OpenFgaClient, acme: SyncedAcme
) -> None:
    org = org_object(acme.tenant_id)
    legit = RelationTuple(acme.user("bob"), "member", org)
    bogus = RelationTuple(acme.user("mallory"), "admin", org)
    await fga.write(acme.store_id, writes=[bogus], deletes=[legit])

    repaired = await reconcile(owner_sessions, fga, acme.tenant_id, acme.store_id)
    again = await reconcile(owner_sessions, fga, acme.tenant_id, acme.store_id)

    assert (repaired.written, repaired.deleted) == (1, 1)
    assert (again.written, again.deleted) == (0, 0)
    assert await _allowed(fga, acme, acme.user("bob"), "member", org)
    assert not await _allowed(fga, acme, acme.user("mallory"), "can_manage", org)


async def test_disabled_users_lose_every_relation(
    owner_sessions: SessionFactory, fga: OpenFgaClient, acme: SyncedAcme
) -> None:
    async with tenant_transaction(owner_sessions, acme.tenant_id) as session:
        await session.execute(
            update(User).where(User.id == acme.users["bob"]).values(status="disabled")
        )
    await reconcile(owner_sessions, fga, acme.tenant_id, acme.store_id)

    org = org_object(acme.tenant_id)
    assert not await _allowed(fga, acme, acme.user("bob"), "member", org)
    assert (
        await fga.list_objects(
            acme.store_id, user=acme.user("bob"), relation="member", type="department"
        )
        == []
    )


async def test_nested_groups_resolve_transitively(
    owner_sessions: SessionFactory, fga: OpenFgaClient, acme: SyncedAcme
) -> None:
    async with tenant_transaction(owner_sessions, acme.tenant_id) as session:
        hr_records = (await session.scalars(select(Group).where(Group.slug == "hr-records"))).one()
        people_ops = Group(tenant_id=acme.tenant_id, slug="people-ops", name="People Ops")
        session.add(people_ops)
        await session.flush()
        session.add_all(
            [
                GroupMember(
                    tenant_id=acme.tenant_id,
                    group_id=people_ops.id,
                    member_user_id=acme.users["bob"],
                ),
                GroupMember(
                    tenant_id=acme.tenant_id, group_id=hr_records.id, member_group_id=people_ops.id
                ),
            ]
        )
        hr_records_id = hr_records.id
    await reconcile(owner_sessions, fga, acme.tenant_id, acme.store_id)

    assert await _allowed(fga, acme, acme.user("bob"), "member", f"group:{hr_records_id}")


async def test_outbox_applies_changes_in_commit_order(
    app_sessions: SessionFactory, fga: OpenFgaClient, acme: SyncedAcme
) -> None:
    org = org_object(acme.tenant_id)
    grant = RelationTuple(acme.user("dan"), "admin", org)
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        enqueue(session, acme.tenant_id, writes=[grant])
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        enqueue(session, acme.tenant_id, deletes=[grant])
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        enqueue(session, acme.tenant_id, writes=[RelationTuple(acme.user("dan"), "auditor", org)])

    applied = await process_outbox(app_sessions, fga, acme.tenant_id, acme.store_id)
    replayed = await process_outbox(app_sessions, fga, acme.tenant_id, acme.store_id)

    assert (applied, replayed) == (3, 0)
    assert not await _allowed(fga, acme, acme.user("dan"), "can_manage", org)
    assert await _allowed(fga, acme, acme.user("dan"), "can_view_audit", org)


async def test_both_permission_locks_agree_for_every_person_and_collection(
    owner_sessions: SessionFactory, fga: OpenFgaClient, acme: SyncedAcme
) -> None:
    """Lock 1 (the ACL stored with every chunk) and lock 2 (OpenFGA) must give the same answer.
    Any difference is drift: lock 2 would silently hide results, or lock 1 would be too open."""
    async with tenant_transaction(owner_sessions, acme.tenant_id) as session:
        collections = (await session.scalars(select(Collection))).all()
        grants = (await session.scalars(select(CollectionGrant))).all()
        identities = {name: await load_identity(session, uid) for name, uid in acme.users.items()}
    acl = {
        c.id: set(acl_principals(c, [g for g in grants if g.collection_id == c.id]))
        for c in collections
    }

    disagreements: list[str] = []
    for name, identity in identities.items():
        assert identity is not None
        questions = [
            RelationTuple(acme.user(name), "viewer", f"collection:{c.id}") for c in collections
        ]
        answers = await fga.batch_check(acme.store_id, questions)
        for c in collections:
            lock1 = bool(acl[c.id] & identity.principals)
            lock2 = answers[RelationTuple(acme.user(name), "viewer", f"collection:{c.id}")]
            if lock1 != lock2:
                disagreements.append(f"{name} / {c.key}: index={lock1} openfga={lock2}")

    assert disagreements == []


async def test_wm_authz_sync_rewrites_an_outdated_model_and_repairs_drift(
    owner_sessions: SessionFactory, fga: OpenFgaClient, acme: SyncedAcme
) -> None:
    # As after an upgrade that changed the model, or a restore whose OpenFGA dump is older.
    async with global_transaction(owner_sessions) as session:
        tenant = await session.get(Tenant, acme.tenant_id)
        assert tenant is not None
        tenant.settings = {**tenant.settings, "openfga_model_sha256": "outdated"}
        slug = tenant.slug
    org = org_object(acme.tenant_id)
    lost = RelationTuple(acme.user("bob"), "member", org)
    await fga.write(acme.store_id, deletes=[lost])

    synced = await sync_tenants(owner_sessions, fga, slug)

    assert [(t.slug, t.store_id, t.written, t.deleted) for t in synced] == [
        (slug, acme.store_id, 1, 0)
    ]
    assert await _allowed(fga, acme, acme.user("bob"), "member", org)
    async with global_transaction(owner_sessions) as session:
        tenant = await session.get(Tenant, acme.tenant_id)
        assert tenant is not None
        assert tenant.settings["openfga_model_sha256"] != "outdated"
    assert await sync_tenants(owner_sessions, fga, "no-such-tenant") == []

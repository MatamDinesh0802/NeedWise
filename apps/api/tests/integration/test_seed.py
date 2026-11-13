import uuid
from pathlib import Path

from sqlalchemy import delete, func, select

from needwise.core.tenancy import tenant_transaction
from needwise.modules.identity.models import DepartmentMember, GroupMember, User
from needwise.modules.work.models import RoutingRule
from needwise.seed.apply import seed_database
from needwise.seed.company import CompanySpec, load_company
from tests.integration.conftest import SessionFactory

ACME = Path(__file__).resolve().parents[4] / "seed" / "acme" / "company.yaml"


def _unique_acme() -> CompanySpec:
    spec = load_company(ACME)
    company = spec.company.model_copy(update={"slug": f"acme-{uuid.uuid4().hex[:8]}"})
    return spec.model_copy(update={"company": company})


async def _counts(sessions: SessionFactory, tenant_id: uuid.UUID) -> tuple[int, int, int]:
    async with tenant_transaction(sessions, tenant_id) as session:
        users = await session.scalar(select(func.count()).select_from(User))
        departments = await session.scalar(select(func.count()).select_from(DepartmentMember))
        groups = await session.scalar(select(func.count()).select_from(GroupMember))
    return users or 0, departments or 0, groups or 0


async def test_seeding_creates_the_company_and_is_idempotent(
    owner_sessions: SessionFactory, app_sessions: SessionFactory
) -> None:
    spec = _unique_acme()
    subjects = {"alice@acme.test": "kc-alice"}

    first = await seed_database(spec, owner_sessions, subjects)
    second = await seed_database(spec, owner_sessions, subjects)

    assert first.tenant_id == second.tenant_id
    # 20 people; everyone except the contractor has a department; 9 group memberships.
    assert await _counts(app_sessions, first.tenant_id) == (20, 19, 9)
    async with tenant_transaction(app_sessions, first.tenant_id) as session:
        alice = (await session.scalars(select(User).where(User.email == "alice@acme.test"))).one()
    assert (alice.keycloak_sub, alice.org_role, alice.title) == ("kc-alice", "member", "HR Manager")


async def test_routing_rules_are_seeded_once_and_an_admins_edits_survive_a_reseed(
    owner_sessions: SessionFactory, app_sessions: SessionFactory
) -> None:
    spec = _unique_acme()
    seeded = await seed_database(spec, owner_sessions, {})
    async with tenant_transaction(app_sessions, seeded.tenant_id) as session:
        rules = (await session.scalars(select(RoutingRule).order_by(RoutingRule.position))).all()
        assert len(rules) == len(spec.request_routing) == 7
        assert rules[0].categories == ["Billing and expenses"]
        assert "invoice" in rules[0].keywords
        await session.execute(delete(RoutingRule).where(RoutingRule.id != rules[0].id))

    await seed_database(spec, owner_sessions, {})

    async with tenant_transaction(app_sessions, seeded.tenant_id) as session:
        assert await session.scalar(select(func.count()).select_from(RoutingRule)) == 1

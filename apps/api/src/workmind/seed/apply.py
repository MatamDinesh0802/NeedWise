"""Applies a company file to Keycloak and to the WorkMind database. Safe to run repeatedly:
records are upserted by their natural keys, and memberships of seeded people are replaced
with exactly what the file says."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import global_transaction, tenant_transaction
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import (
    Department,
    DepartmentMember,
    Group,
    GroupMember,
    Tenant,
    User,
)
from workmind.seed.company import CompanySpec
from workmind.seed.keycloak_admin import KeycloakAdmin, KeycloakAdminError


@dataclass(frozen=True)
class SeedResult:
    tenant_id: uuid.UUID
    people: int
    departments: int
    groups: int
    keycloak_logins: int


async def seed_keycloak(spec: CompanySpec, admin: KeycloakAdmin, password: str) -> dict[str, str]:
    """Creates the organization and a login for each demo person. Returns email -> Keycloak id."""
    if not await admin.realm_exists():
        raise KeycloakAdminError(
            "realm not found: start Keycloak with --import-realm (see compose.yaml)"
        )
    org_id = await admin.ensure_organization(
        alias=spec.company.slug, name=spec.company.name, domain=spec.company.domain
    )
    subjects: dict[str, str] = {}
    for person in spec.demo_people:
        user_id = await admin.ensure_user(
            email=person.email,
            first_name=person.first_name,
            last_name=person.last_name,
            password=password,
        )
        await admin.ensure_org_member(org_id, user_id)
        subjects[person.email.lower()] = user_id
    return subjects


async def seed_database(
    spec: CompanySpec,
    sessions: async_sessionmaker[AsyncSession],
    keycloak_subjects: Mapping[str, str],
) -> SeedResult:
    """Must use the owner role: the runtime role cannot create tenants."""
    async with global_transaction(sessions) as session:
        tenant_id = (
            await session.execute(
                insert(Tenant)
                .values(slug=spec.company.slug, name=spec.company.name)
                .on_conflict_do_update(
                    index_elements=["slug"],
                    set_={"name": spec.company.name, "updated_at": func.now()},
                )
                .returning(Tenant.id)
            )
        ).scalar_one()

    async with tenant_transaction(sessions, tenant_id) as session:
        department_ids = {
            d.key: await _upsert(session, Department, tenant_id, {"slug": d.key}, {"name": d.name})
            for d in spec.departments
        }
        group_ids = {
            g.key: await _upsert(
                session,
                Group,
                tenant_id,
                {"slug": g.key},
                {"name": g.key.replace("-", " ").title(), "description": g.description},
            )
            for g in spec.groups
        }
        user_ids: dict[str, uuid.UUID] = {}
        for person in spec.people:
            fields: dict[str, object] = {
                "display_name": person.name,
                "title": person.title,
                "org_role": person.org_role,
                "status": "active",
            }
            if subject := keycloak_subjects.get(person.email.lower()):
                fields["keycloak_sub"] = subject
            user_ids[person.email.lower()] = await _upsert(
                session, User, tenant_id, {"email": person.email}, fields
            )

        seeded = list(user_ids.values())
        await session.execute(delete(DepartmentMember).where(DepartmentMember.user_id.in_(seeded)))
        await session.execute(delete(GroupMember).where(GroupMember.member_user_id.in_(seeded)))
        for person in spec.people:
            user_id = user_ids[person.email.lower()]
            if person.department:
                session.add(
                    DepartmentMember(
                        tenant_id=tenant_id,
                        department_id=department_ids[person.department],
                        user_id=user_id,
                        role=person.dept_role,
                    )
                )
            session.add_all(
                GroupMember(tenant_id=tenant_id, group_id=group_ids[g], member_user_id=user_id)
                for g in person.groups
            )
        await session.flush()

        await record_event(
            session,
            tenant_id=tenant_id,
            actor=Actor.system(),
            action="seed.applied",
            resource_type="tenant",
            resource_id=str(tenant_id),
            details={"people": len(spec.people), "keycloak_logins": len(keycloak_subjects)},
        )

    return SeedResult(
        tenant_id=tenant_id,
        people=len(spec.people),
        departments=len(spec.departments),
        groups=len(spec.groups),
        keycloak_logins=len(keycloak_subjects),
    )


async def _upsert(
    session: AsyncSession,
    model: type[Department] | type[Group] | type[User],
    tenant_id: uuid.UUID,
    natural_key: dict[str, object],
    fields: dict[str, object],
) -> uuid.UUID:
    statement = (
        insert(model)
        .values(tenant_id=tenant_id, **natural_key, **fields)
        .on_conflict_do_update(
            index_elements=["tenant_id", *natural_key],
            set_={**fields, "updated_at": func.now()},
        )
        .returning(model.id)
    )
    return (await session.execute(statement)).scalar_one()

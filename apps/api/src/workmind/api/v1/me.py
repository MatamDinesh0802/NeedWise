"""The signed-in user's profile, roles and memberships."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel

from workmind.api.deps import require_scope, resources_of
from workmind.core.context import RequestContext
from workmind.core.errors import unauthenticated
from workmind.core.tenancy import tenant_transaction
from workmind.modules.identity.models import Tenant
from workmind.modules.identity.service import load_identity

router = APIRouter(tags=["identity"])


class TenantRef(BaseModel):
    id: uuid.UUID
    slug: str
    name: str


class DepartmentRef(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    role: str


class GroupRef(BaseModel):
    id: uuid.UUID
    slug: str
    name: str


class Me(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    title: str | None
    org_role: str
    tenant: TenantRef
    departments: list[DepartmentRef]
    groups: list[GroupRef]


@router.get("/me")
async def me(ctx: Annotated[RequestContext, require_scope("profile:read")], request: Request) -> Me:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as db:
        identity = await load_identity(db, ctx.user_id)
        tenant = await db.get(Tenant, ctx.tenant_id)
    if identity is None or tenant is None:
        raise unauthenticated()
    user = identity.user
    return Me(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        title=user.title,
        org_role=user.org_role,
        tenant=TenantRef(id=tenant.id, slug=tenant.slug, name=tenant.name),
        departments=[
            DepartmentRef(id=d.id, slug=d.slug, name=d.name, role=role)
            for d, role in identity.departments
        ],
        groups=[GroupRef(id=g.id, slug=g.slug, name=g.name) for g in identity.groups],
    )

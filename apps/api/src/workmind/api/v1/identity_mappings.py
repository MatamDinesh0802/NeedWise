"""Identity mappings, for administrators (docs/permission-sync.md).

Permission sync writes each synced document's own access list from its source's ACL. People
are matched by e-mail on their own and "anyone" is the organization; everything else a source
names (a Google group, a domain, a Slack or GitHub account, a GitHub team) maps to nobody until
an administrator maps it here. Unmapped entries grant nothing (fail closed) and are listed, with
the number of documents that name them and their sources, never the documents' titles.

    GET    /admin/identity-mappings                 mappings, and what is still unmapped
    PUT    /admin/identity-mappings                 map a source principal (applied at once)
    DELETE /admin/identity-mappings/{mapping_id}    remove a mapping (access ends at once)

A change applies to every document that names the source principal right away: the OpenFGA
tuples are queued in the same transaction and the chunks are refreshed after it commits.
"""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workmind.api.deps import resources_of
from workmind.api.v1.admin import AdminContext
from workmind.api.v1.knowledge import Principal, check_principals
from workmind.core.errors import ApiError
from workmind.core.resources import Resources
from workmind.core.tenancy import tenant_transaction
from workmind.modules.connectors.models import EXTERNAL_PATTERN, IdentityMapping
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import Department, Group, User
from workmind.modules.knowledge import document_acl

router = APIRouter(prefix="/admin/identity-mappings", tags=["admin"])


def _external(value: str) -> str:
    normalized = document_acl.normalize_external(value)
    if not re.fullmatch(EXTERNAL_PATTERN, normalized):
        raise ValueError(
            "a source principal looks like email:<address>, group:<address or id>, "
            "domain:<domain>, anyone, slack-user:<id>, github-user:<login>, "
            "github-team:<org>/<slug> or github-org:<org>"
        )
    return normalized


External = Annotated[str, Field(min_length=3, max_length=340), AfterValidator(_external)]


class MappingIn(BaseModel):
    external: External = Field(description="the principal as the source names it")
    principal: Principal = Field(description="who that is in WorkMind")


class MappingOut(BaseModel):
    id: uuid.UUID
    external: str
    principal: str
    # Who that is, for people: a person's, group's or department's name ("... (managers)",
    # "Everyone in the organization"); None if it no longer exists.
    principal_name: str | None
    created_by: uuid.UUID | None
    updated_at: datetime


class UnmappedOut(BaseModel):
    external: str
    documents: int  # documents whose own access list names it
    sources: list[str]  # keys of the sources those documents come from


class MappingsOut(BaseModel):
    mappings: list[MappingOut]
    unmapped: list[UnmappedOut]


class MappingSaved(BaseModel):
    mapping: MappingOut
    documents_changed: int


def _out(m: IdentityMapping, names: dict[str, str]) -> MappingOut:
    return MappingOut(
        id=m.id,
        external=m.external,
        principal=m.principal,
        principal_name=names.get(m.principal),
        created_by=m.created_by,
        updated_at=m.updated_at,
    )


async def _names(session: AsyncSession, principals: list[str]) -> dict[str, str]:
    """Readable names of principals (only this tenant's rows are visible: RLS)."""
    ids: dict[str, set[uuid.UUID]] = {"user": set(), "group": set(), "dept": set()}
    for principal in principals:
        kind, _, raw = principal.partition(":")
        if kind != "org":
            ids["dept" if kind == "dept-manager" else kind].add(uuid.UUID(raw))
    found: dict[str, str] = {}
    for kind, model, column in (
        ("user", User, User.display_name),
        ("group", Group, Group.name),
        ("dept", Department, Department.name),
    ):
        if ids[kind]:
            rows = await session.execute(select(model.id, column).where(model.id.in_(ids[kind])))
            found |= {f"{kind}:{row_id}": str(name) for row_id, name in rows}
    names: dict[str, str] = {}
    for principal in principals:
        kind, _, raw = principal.partition(":")
        if kind == "org":
            names[principal] = "Everyone in the organization"
        elif kind == "dept-manager" and f"dept:{raw}" in found:
            names[principal] = f"{found[f'dept:{raw}']} (managers)"
        elif principal in found:
            names[principal] = found[principal]
    return names


async def _refresh(resources: Resources, tenant_id: uuid.UUID, payloads: dict[Any, Any]) -> None:
    for document_id, payload in payloads.items():
        await document_acl.refresh_payload(
            resources.qdrant, resources.collection_name, tenant_id, document_id, payload
        )


@router.get("")
async def list_mappings(ctx: AdminContext, request: Request) -> MappingsOut:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        mappings = (
            await session.scalars(select(IdentityMapping).order_by(IdentityMapping.external))
        ).all()
        unmapped = await document_acl.unmapped(session)
        names = await _names(session, [m.principal for m in mappings])
    return MappingsOut(
        mappings=[_out(m, names) for m in mappings],
        unmapped=[
            UnmappedOut(external=u.external, documents=u.documents, sources=u.sources)
            for u in unmapped
        ],
    )


@router.put("")
async def save_mapping(ctx: AdminContext, request: Request, body: MappingIn) -> MappingSaved:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        await check_principals(session, ctx.tenant_id, [body.principal])
        mapping = await session.scalar(
            select(IdentityMapping).where(IdentityMapping.external == body.external)
        )
        before = mapping.principal if mapping else None
        if mapping is None:
            mapping = IdentityMapping(
                tenant_id=ctx.tenant_id,
                external=body.external,
                principal=body.principal,
                created_by=ctx.user_id,
            )
            session.add(mapping)
        else:
            mapping.principal, mapping.created_by = body.principal, ctx.user_id
        await session.flush()
        payloads = await document_acl.remap(session, ctx.tenant_id, [body.external])
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.identity_mapping.saved",
            resource_type="identity_mapping",
            resource_id=body.external,
            details={
                "before": before,
                "after": body.principal,
                "documents_changed": len(payloads),
            },
        )
        await session.refresh(mapping)
        out = _out(mapping, await _names(session, [mapping.principal]))
    await _refresh(resources, ctx.tenant_id, payloads)
    return MappingSaved(mapping=out, documents_changed=len(payloads))


@router.delete("/{mapping_id}", status_code=204)
async def delete_mapping(ctx: AdminContext, request: Request, mapping_id: uuid.UUID) -> Response:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        mapping = await session.get(IdentityMapping, mapping_id)
        if mapping is None:
            raise ApiError(404, "not_found", "Mapping not found")
        external, principal = mapping.external, mapping.principal
        await session.delete(mapping)
        await session.flush()
        payloads = await document_acl.remap(session, ctx.tenant_id, [external])
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="admin.identity_mapping.deleted",
            resource_type="identity_mapping",
            resource_id=external,
            details={"before": principal, "documents_changed": len(payloads)},
        )
    await _refresh(resources, ctx.tenant_id, payloads)
    return Response(status_code=204)

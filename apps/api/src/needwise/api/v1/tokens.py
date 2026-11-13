"""The signed-in user's personal access tokens (/api/v1/me/tokens)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from needwise.api.deps import SessionContext, resources_of
from needwise.core.tenancy import tenant_transaction
from needwise.modules.identity import tokens
from needwise.modules.identity.models import ApiToken, Tenant

router = APIRouter(prefix="/me/tokens", tags=["identity"])


class TokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class TokenCreated(TokenOut):
    token: str = Field(description="Shown only in this response. Store it securely.")


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=len(tokens.GRANTABLE_SCOPES))
    expires_in_days: int = Field(default=90, ge=1, le=tokens.MAX_LIFETIME_DAYS)


class TokenList(BaseModel):
    items: list[TokenOut]


def _out(token: ApiToken) -> TokenOut:
    return TokenOut.model_validate(token, from_attributes=True)


@router.get("")
async def list_my_tokens(ctx: SessionContext, request: Request) -> TokenList:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        return TokenList(items=[_out(t) for t in await tokens.list_tokens(session, ctx.user_id)])


@router.post("", status_code=201)
async def create_my_token(ctx: SessionContext, request: Request, body: TokenCreate) -> TokenCreated:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        assert tenant is not None  # noqa: S101  (the session's tenant always exists)
        token, plain = await tokens.create_token(
            session,
            ctx,
            tenant_slug=tenant.slug,
            name=body.name,
            scopes=body.scopes,
            expires_in_days=body.expires_in_days,
        )
        return TokenCreated(**_out(token).model_dump(), token=plain)


@router.delete("/{token_id}", status_code=204)
async def revoke_my_token(ctx: SessionContext, request: Request, token_id: uuid.UUID) -> Response:
    async with tenant_transaction(resources_of(request).db, ctx.tenant_id) as session:
        await tokens.revoke_token(session, ctx, token_id)
    return Response(status_code=204)

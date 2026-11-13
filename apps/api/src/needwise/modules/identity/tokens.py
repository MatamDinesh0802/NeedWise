"""Personal access tokens for MCP clients, CLIs and scripts (ARCHITECTURE section 6.5).

Format: nw_pat_<tenant-slug>_<43 random characters>. The slug says which tenant to look in
(slugs never contain "_"); the random part carries 256 bits of entropy. Only the SHA-256 of
the whole token is stored, so a database dump yields no usable tokens.
"""

import hashlib
import re
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.context import RequestContext
from needwise.core.errors import ApiError
from needwise.core.tenancy import global_transaction, tenant_transaction
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.identity.models import SLUG_PATTERN, ApiToken, Tenant
from needwise.modules.identity.service import Identity, load_identity

TOKEN_PREFIX = "nw_pat_"  # noqa: S105  (a format marker, not a secret)
# Scopes a token can be granted. knowledge:read and assistant:ask take effect in Phases 3-4.
GRANTABLE_SCOPES = ("profile:read", "knowledge:read", "assistant:ask")
MAX_LIFETIME_DAYS = 365
_MAX_TOKEN_LENGTH = 200
_TOUCH_INTERVAL = timedelta(minutes=5)
_SLUG = re.compile(SLUG_PATTERN)


def generate_token(tenant_slug: str) -> str:
    return f"{TOKEN_PREFIX}{tenant_slug}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def tenant_slug_of(token: str) -> str | None:
    if len(token) > _MAX_TOKEN_LENGTH or not token.startswith(TOKEN_PREFIX):
        return None
    slug, separator, secret = token[len(TOKEN_PREFIX) :].partition("_")
    if not separator or len(secret) < 32 or not _SLUG.fullmatch(slug):
        return None
    return slug


def display_prefix(token: str) -> str:
    slug = tenant_slug_of(token) or ""
    return token[: len(TOKEN_PREFIX) + len(slug) + 1 + 4]


async def create_token(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    tenant_slug: str,
    name: str,
    scopes: Sequence[str],
    expires_in_days: int,
) -> tuple[ApiToken, str]:
    """Returns the stored token and the plain token, which is never retrievable again."""
    unknown = sorted(set(scopes) - set(GRANTABLE_SCOPES))
    if unknown or not scopes:
        raise ApiError(
            422, "invalid_scopes", "Choose at least one valid scope", f"Unknown: {unknown}"
        )
    plain = generate_token(tenant_slug)
    token = ApiToken(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        name=name,
        prefix=display_prefix(plain),
        token_hash=hash_token(plain),
        scopes=sorted(set(scopes)),
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
    )
    session.add(token)
    await session.flush()
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action="token.created",
        resource_type="api_token",
        resource_id=str(token.id),
        details={"name": name, "scopes": token.scopes, "expires_at": token.expires_at.isoformat()},
    )
    return token, plain


async def list_tokens(session: AsyncSession, user_id: uuid.UUID) -> Sequence[ApiToken]:
    return (
        await session.scalars(
            select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.created_at.desc())
        )
    ).all()


async def revoke_token(session: AsyncSession, ctx: RequestContext, token_id: uuid.UUID) -> None:
    token = await session.get(ApiToken, token_id)
    if token is None or token.user_id != ctx.user_id:  # someone else's token looks like no token
        raise ApiError(404, "not_found", "Token not found")
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="token.revoked",
            resource_type="api_token",
            resource_id=str(token.id),
            details={"name": token.name},
        )


async def authenticate(
    sessions: async_sessionmaker[AsyncSession], plain: str
) -> tuple[ApiToken, Identity] | None:
    """The token and its user's identity, or None if it is unknown, revoked, expired, or its
    user is not active."""
    slug = tenant_slug_of(plain)
    if slug is None:
        return None
    async with global_transaction(sessions) as session:
        tenant_id = await session.scalar(
            select(Tenant.id).where(Tenant.slug == slug, Tenant.status == "active")
        )
    if tenant_id is None:
        return None

    now = datetime.now(UTC)
    async with tenant_transaction(sessions, tenant_id) as session:
        token = (
            await session.scalars(
                select(ApiToken).where(
                    ApiToken.token_hash == hash_token(plain),
                    ApiToken.revoked_at.is_(None),
                    ApiToken.expires_at > now,
                )
            )
        ).first()
        if token is None:
            return None
        identity = await load_identity(session, token.user_id)
        if identity is None or identity.user.status != "active":
            return None
        if token.last_used_at is None or now - token.last_used_at > _TOUCH_INTERVAL:
            token.last_used_at = now
        return token, identity

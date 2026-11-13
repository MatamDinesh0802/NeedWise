"""Models and providers (docs/models.md): bring your own keys, the catalog with prices, which
model serves each role, and the data policy for external models. Owners and admins only.

    GET    /admin/models                          the catalog (system and tenant), roles, policy
    POST   /admin/models/providers                add a provider (api_key is write-only)
    PATCH  /admin/models/providers/{id}           change it (no api_key: the key is kept)
    DELETE /admin/models/providers/{id}           remove it and its models (409 while in use)
    POST   /admin/models/providers/{id}/test      one tiny request -> {ok, latency_ms, error}
    POST   /admin/models/catalog                  add a model to a provider
    PATCH  /admin/models/catalog/{id}             label, prices, enabled
    DELETE /admin/models/catalog/{id}             remove it (409 while in use)
    PUT    /admin/models/roles                    the model of each role (null: the default)
    PUT    /admin/models/policy                   the data policy

API keys are never returned, logged or audited: responses show `has_key` and `key_hint`.
"""

import unicodedata
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from needwise.api.deps import rate_limit, resources_of, settings_of
from needwise.api.v1.admin import AdminContext
from needwise.core.context import RequestContext
from needwise.core.errors import ApiError
from needwise.core.resources import Resources
from needwise.core.tenancy import tenant_transaction
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.model_catalog import service
from needwise.modules.model_catalog.catalog import ROLES, Catalog, ModelInfo, ProviderInfo
from needwise.modules.model_catalog.models import ModelProvider
from needwise.modules.model_catalog.policy import Sensitivity
from needwise.modules.model_catalog.store import model_info, provider_info
from needwise.providers.llm import Role

admin_router = APIRouter(prefix="/admin/models", tags=["admin"])
ProviderKind = Literal["anthropic", "openai_compatible"]


def _plain(value: str, what: str) -> str:
    value = value.strip()
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError(f"{what} cannot contain control characters")
    return value


def _key(value: SecretStr | None) -> SecretStr | None:
    """Keys are pasted: surrounding spaces go; a key with spaces inside is refused. The
    message never repeats the key."""
    if value is None:
        return None
    key = value.get_secret_value().strip()
    if not key:
        return None
    if len(key) > 500 or any(c.isspace() or unicodedata.category(c).startswith("C") for c in key):
        raise ValueError("the API key must be one line without spaces, at most 500 characters")
    return SecretStr(key)


# --- Schemas ------------------------------------------------------------------------------


class ProviderOut(BaseModel):
    id: str
    name: str
    kind: str
    base_url: str | None
    local: bool
    has_key: bool
    key_hint: str | None
    enabled: bool
    system: bool


class ModelOut(BaseModel):
    id: str
    provider_id: str
    name: str
    label: str
    local: bool
    input_usd_per_mtok: float | None
    output_usd_per_mtok: float | None
    enabled: bool


class RolesOut(BaseModel):
    answer: str
    router: str
    judge: str
    generator: str
    agent: str


class PolicyOut(BaseModel):
    max_external_sensitivity: Sensitivity
    local_model: str | None


class DefaultsOut(BaseModel):
    """What the deployment gives a tenant that chose nothing."""

    roles: RolesOut
    policy: PolicyOut


class CatalogOut(BaseModel):
    providers: list[ProviderOut]
    models: list[ModelOut]
    roles: RolesOut
    policy: PolicyOut
    defaults: DefaultsOut


class ProviderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    base_url: str | None = Field(default=None, max_length=500)
    api_key: SecretStr | None = None  # write-only
    local: bool = False
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _plain(value, "the name")

    @field_validator("api_key")
    @classmethod
    def _api_key(cls, value: SecretStr | None) -> SecretStr | None:
        return _key(value)


class ProviderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: SecretStr | None = None  # write-only; leave it out to keep the key
    clear_key: bool = False
    local: bool | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _plain(value, "the name")

    @field_validator("api_key")
    @classmethod
    def _api_key(cls, value: SecretStr | None) -> SecretStr | None:
        return _key(value)


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200, description="the name sent to the provider")
    label: str | None = Field(default=None, max_length=200)
    input_usd_per_mtok: float | None = Field(default=None, ge=0, le=10_000)
    output_usd_per_mtok: float | None = Field(default=None, ge=0, le=10_000)
    enabled: bool = True

    @field_validator("name", "label")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return None if value is None else _plain(value, "the text") or None


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=200)
    input_usd_per_mtok: float | None = Field(default=None, ge=0, le=10_000)
    output_usd_per_mtok: float | None = Field(default=None, ge=0, le=10_000)
    enabled: bool | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        return None if value is None else _plain(value, "the label")


class RolesIn(BaseModel):
    """A catalog model id per role; null (or left out) follows the deployment's default."""

    model_config = ConfigDict(extra="forbid")

    answer: str | None = None
    router: str | None = None
    judge: str | None = None
    generator: str | None = None
    agent: str | None = None


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_external_sensitivity: Sensitivity
    local_model: str | None = None


class TestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, min_length=1, max_length=200)


class TestOut(BaseModel):
    ok: bool
    latency_ms: int | None
    error: str | None
    model: str | None


# --- Endpoints ----------------------------------------------------------------------------


def _provider_out(info: ProviderInfo) -> ProviderOut:
    return ProviderOut(
        id=info.id,
        name=info.name,
        kind=info.kind,
        base_url=info.base_url,
        local=info.local,
        has_key=info.has_key,
        key_hint=info.key_hint,
        enabled=info.enabled,
        system=info.system,
    )


def _model_out(info: ModelInfo) -> ModelOut:
    return ModelOut(
        id=info.id,
        provider_id=info.provider_id,
        name=info.name,
        label=info.label,
        local=info.local,
        input_usd_per_mtok=info.input_usd_per_mtok,
        output_usd_per_mtok=info.output_usd_per_mtok,
        enabled=info.enabled,
    )


def catalog_out(catalog: Catalog) -> CatalogOut:
    defaults = catalog.policy_defaults
    return CatalogOut(
        providers=[_provider_out(p) for p in catalog.providers],
        models=[_model_out(m) for m in catalog.models],
        roles=RolesOut(**catalog.roles),
        policy=PolicyOut(**catalog.policy),
        defaults=DefaultsOut(
            roles=RolesOut(**catalog.role_defaults),
            policy=PolicyOut(
                max_external_sensitivity=defaults["max_external_sensitivity"],
                local_model=defaults["local_model"],
            ),
        ),
    )


async def _current(resources: Resources, ctx: RequestContext) -> CatalogOut:
    return catalog_out(await resources.models.catalog(ctx.tenant_id))


@admin_router.get("")
async def get_models(ctx: AdminContext, request: Request) -> CatalogOut:
    return await _current(resources_of(request), ctx)


@admin_router.post("/providers", status_code=201)
async def create_provider(ctx: AdminContext, request: Request, body: ProviderIn) -> ProviderOut:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        row = await service.create_provider(
            session,
            ctx,
            resources.models.keyring,
            name=body.name,
            kind=body.kind,
            base_url=body.base_url,
            api_key=body.api_key.get_secret_value() if body.api_key else None,
            local=body.local,
            enabled=body.enabled,
        )
        out = _provider_out(provider_info(row))
    resources.models.invalidate(ctx.tenant_id)
    return out


@admin_router.patch("/providers/{provider_id}")
async def update_provider(
    ctx: AdminContext, request: Request, provider_id: str, body: ProviderPatch
) -> ProviderOut:
    resources = resources_of(request)
    changes: dict[str, Any] = {
        name: getattr(body, name)
        for name in body.model_fields_set
        if name not in ("api_key", "clear_key") and getattr(body, name) is not None
    }
    if "base_url" in body.model_fields_set:
        changes["base_url"] = body.base_url  # None: the provider's public API (Anthropic)
    if body.api_key is not None:
        changes["api_key"] = body.api_key.get_secret_value()
    if body.clear_key:
        changes["clear_key"] = True
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        row = await service.update_provider(
            session, ctx, resources.models.keyring, provider_id, changes
        )
        out = _provider_out(provider_info(row))
    resources.models.invalidate(ctx.tenant_id)
    return out


@admin_router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(ctx: AdminContext, request: Request, provider_id: str) -> Response:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        await service.delete_provider(session, ctx, provider_id)
    resources.models.invalidate(ctx.tenant_id)
    return Response(status_code=204)


@admin_router.post("/providers/{provider_id}/test", dependencies=[rate_limit("agent_test")])
async def probe_provider(
    ctx: AdminContext,
    request: Request,
    provider_id: str,
    body: Annotated[TestIn | None, Body()] = None,
) -> TestOut:
    """One tiny request to the provider (at most 10 s). The error is plain words, a status
    code and the provider's error code: never its response body, never the key."""
    resources = resources_of(request)
    resolver = resources.models
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        target = await service.gather_probe(
            session,
            ctx,
            settings_of(request),
            resolver.system_catalog,
            resolver.keyring,
            provider_id,
            body.model if body else None,
        )
    result = await service.probe(target, transport=resolver.probe_transport)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action="model_provider.tested",
            resource_type="model_catalog",
            resource_id=provider_id,
            details={"model": result.model, "ok": result.ok, "latency_ms": result.latency_ms},
        )
    return TestOut(
        ok=result.ok, latency_ms=result.latency_ms, error=result.error, model=result.model
    )


@admin_router.post("/catalog", status_code=201)
async def create_model(ctx: AdminContext, request: Request, body: ModelIn) -> ModelOut:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        row = await service.create_model(
            session,
            ctx,
            provider_id=body.provider_id,
            name=body.name,
            label=body.label,
            input_usd_per_mtok=body.input_usd_per_mtok,
            output_usd_per_mtok=body.output_usd_per_mtok,
            enabled=body.enabled,
        )
        provider = await session.get(ModelProvider, row.provider_id)
        assert provider is not None  # noqa: S101  (checked by create_model)
        out = _model_out(model_info(row, provider_info(provider)))
    resources.models.invalidate(ctx.tenant_id)
    return out


@admin_router.patch("/catalog/{model_id:path}")
async def update_model(
    ctx: AdminContext, request: Request, model_id: str, body: ModelPatch
) -> ModelOut:
    resources = resources_of(request)
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    if changes.get("label", "") is None or ("enabled" in changes and changes["enabled"] is None):
        raise ApiError(422, "invalid_input", "label and enabled cannot be null")
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        row = await service.update_model(session, ctx, model_id, changes)
        provider = await session.get(ModelProvider, row.provider_id)
        assert provider is not None  # noqa: S101
        out = _model_out(model_info(row, provider_info(provider)))
    resources.models.invalidate(ctx.tenant_id)
    return out


@admin_router.delete("/catalog/{model_id:path}", status_code=204)
async def delete_model(ctx: AdminContext, request: Request, model_id: str) -> Response:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        await service.delete_model(session, ctx, model_id)
    resources.models.invalidate(ctx.tenant_id)
    return Response(status_code=204)


@admin_router.put("/roles")
async def put_roles(ctx: AdminContext, request: Request, body: RolesIn) -> CatalogOut:
    resources = resources_of(request)
    roles: dict[Role, str | None] = {role: getattr(body, role) for role in ROLES}
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        await service.set_roles(session, ctx, resources.models.system_catalog, roles)
    resources.models.invalidate(ctx.tenant_id)
    return await _current(resources, ctx)


@admin_router.put("/policy")
async def put_policy(ctx: AdminContext, request: Request, body: PolicyIn) -> CatalogOut:
    resources = resources_of(request)
    async with tenant_transaction(resources.db, ctx.tenant_id) as session:
        await service.set_policy(
            session,
            ctx,
            resources.models.system_catalog,
            max_external_sensitivity=body.max_external_sensitivity,
            local_model=body.local_model,
        )
    resources.models.invalidate(ctx.tenant_id)
    return await _current(resources, ctx)

"""Administration of a tenant's models (docs/models.md): providers, catalog, roles, policy.

Every change runs in the admin's tenant transaction and, in that transaction, locks the
tenant's settings row (so changes and in-use checks cannot interleave), checks its guard
rails, bumps the settings version (other processes drop their cached resolution) and writes an
audit event. Audit events never contain a key or its hint, only whether it changed.
"""

import asyncio
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

import anthropic
import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.core.config import Settings
from needwise.core.context import RequestContext
from needwise.core.errors import ApiError
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.model_catalog.catalog import (
    ROLES,
    SYSTEM,
    SYSTEM_LOCAL,
    Catalog,
    ProviderInfo,
    SystemCatalog,
)
from needwise.modules.model_catalog.models import CatalogModel, ModelProvider, ModelSettings
from needwise.modules.model_catalog.policy import Sensitivity
from needwise.modules.model_catalog.references import references
from needwise.modules.model_catalog.resolver import scrub
from needwise.modules.model_catalog.secrets import KeyRing
from needwise.modules.model_catalog.store import key_context, key_hint, load_config
from needwise.modules.model_catalog.urls import UnsafeUrlError, check_base_url_resolved
from needwise.providers.factory import secret
from needwise.providers.llm import Role, tenant_client

TEST_TIMEOUT_S = 10.0
TEST_PROMPT = "Reply with the word OK."
_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def not_found(kind: str) -> ApiError:
    return ApiError(404, "not_found", f"{kind} not found")


def read_only() -> ApiError:
    return ApiError(
        409,
        "read_only",
        "The deployment's models are read-only",
        "They come from the server's settings (LLM_* and LOCAL_LLM_*).",
    )


def in_use(what: str, uses: list[str]) -> ApiError:
    return ApiError(
        409,
        "model_in_use",
        f"{what} is in use",
        f"It is used by {', '.join(uses)}. Choose another model there first.",
    )


async def _audit(
    session: AsyncSession, ctx: RequestContext, action: str, resource_id: str, details: Any
) -> None:
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor=Actor(type="user", id=ctx.user_id),
        action=action,
        resource_type="model_catalog",
        resource_id=resource_id,
        details=details,
    )


async def _settings(session: AsyncSession, ctx: RequestContext) -> ModelSettings:
    """The tenant's settings row, created if missing, locked for this transaction, with its
    version already bumped: every caller changes something."""
    await session.execute(
        insert(ModelSettings)
        .values(tenant_id=ctx.tenant_id)
        .on_conflict_do_nothing(index_elements=["tenant_id"])
    )
    row = await session.scalar(select(ModelSettings).with_for_update())
    assert row is not None  # noqa: S101  (just inserted, same tenant)
    row.version += 1
    row.updated_by = ctx.user_id
    return row


async def _uses(session: AsyncSession, row: ModelSettings, model_ids: set[str]) -> list[str]:
    found = [f"the {role} role" for role in ROLES if row.roles.get(role) in model_ids]
    if row.policy.get("local_model") in model_ids:
        found.append("the data policy's local model")
    return found + await references(session, frozenset(model_ids))


def _ids_of(provider: ModelProvider, models: list[CatalogModel]) -> set[str]:
    return {f"{provider.id}:{m.name}" for m in models}


def _parse_model_id(model_id: str) -> tuple[uuid.UUID, str]:
    provider, _, name = model_id.partition(":")
    if provider in (SYSTEM, SYSTEM_LOCAL):
        raise read_only()
    try:
        return uuid.UUID(provider), name
    except ValueError as exc:
        raise not_found("Model") from exc


def _parse_provider_id(provider_id: str) -> uuid.UUID:
    if provider_id in (SYSTEM, SYSTEM_LOCAL):
        raise read_only()
    try:
        return uuid.UUID(provider_id)
    except ValueError as exc:
        raise not_found("Provider") from exc


async def checked_base_url(kind: str, base_url: str | None) -> str | None:
    if base_url is None or not base_url.strip():
        if kind == "openai_compatible":
            raise ApiError(422, "base_url_required", "An OpenAI-compatible provider needs a URL")
        return None
    try:
        return await check_base_url_resolved(base_url)
    except UnsafeUrlError as exc:
        raise ApiError(422, "unsafe_base_url", "This base URL is not allowed", str(exc)) from exc


def _seal(keyring: KeyRing, ctx: RequestContext, row: ModelProvider, api_key: str) -> None:
    row.api_key = keyring.seal(
        api_key, context=key_context(ctx.tenant_id, row.id, row.kind, row.base_url)
    )
    row.key_hint = key_hint(api_key)


# --- Providers ------------------------------------------------------------------------------


async def create_provider(
    session: AsyncSession,
    ctx: RequestContext,
    keyring: KeyRing,
    *,
    name: str,
    kind: str,
    base_url: str | None,
    api_key: str | None,
    local: bool,
    enabled: bool,
) -> ModelProvider:
    if kind == "anthropic" and local:
        raise ApiError(422, "not_local", "Anthropic's API is never local")
    url = await checked_base_url(kind, base_url)
    await _settings(session, ctx)
    if await session.scalar(select(ModelProvider.id).where(ModelProvider.name == name)):
        raise ApiError(409, "provider_exists", "A provider with this name already exists")
    row = ModelProvider(
        tenant_id=ctx.tenant_id, name=name, kind=kind, base_url=url, local=local, enabled=enabled
    )
    session.add(row)
    await session.flush()  # the id is part of what the key is bound to
    if api_key:
        _seal(keyring, ctx, row, api_key)
        await session.flush()
    await _audit(
        session,
        ctx,
        "model_provider.created",
        str(row.id),
        {
            "name": name,
            "kind": kind,
            "base_url": url,
            "local": local,
            "enabled": enabled,
            "key": "set" if api_key else "none",
        },
    )
    return row


async def update_provider(
    session: AsyncSession,
    ctx: RequestContext,
    keyring: KeyRing,
    provider_id: str,
    changes: Mapping[str, Any],
) -> ModelProvider:
    """`changes` holds only the fields the admin sent: name, base_url, api_key, clear_key,
    local, enabled. Without api_key the key is kept; a new base URL needs the key again."""
    pid = _parse_provider_id(provider_id)
    settings_row = await _settings(session, ctx)
    row = await session.get(ModelProvider, pid)
    if row is None:
        raise not_found("Provider")
    details: dict[str, Any] = {}
    key = "unchanged"
    if "name" in changes and changes["name"] != row.name:
        clash = await session.scalar(
            select(ModelProvider.id).where(
                ModelProvider.name == changes["name"], ModelProvider.id != pid
            )
        )
        if clash:
            raise ApiError(409, "provider_exists", "A provider with this name already exists")
        row.name = details["name"] = changes["name"]
    if "base_url" in changes:
        url = await checked_base_url(row.kind, changes["base_url"])
        if url != row.base_url:
            if (
                row.api_key is not None
                and not changes.get("api_key")
                and not changes.get("clear_key")
            ):
                raise ApiError(
                    422,
                    "key_required",
                    "Enter the API key again to change the URL",
                    "A key is only ever sent to the URL it was saved for.",
                )
            row.base_url = details["base_url"] = url
    if "local" in changes and changes["local"] != row.local:
        if row.kind == "anthropic" and changes["local"]:
            raise ApiError(422, "not_local", "Anthropic's API is never local")
        if not changes["local"]:
            ids = _ids_of(row, list(await _models_of(session, pid)))
            if settings_row.policy.get("local_model") in ids:
                raise ApiError(
                    409,
                    "model_in_use",
                    "This provider serves the data policy's local model",
                    "Choose another local model in the data policy first.",
                )
        row.local = details["local"] = changes["local"]
    if "enabled" in changes and changes["enabled"] != row.enabled:
        if not changes["enabled"]:
            uses = await _uses(
                session, settings_row, _ids_of(row, list(await _models_of(session, pid)))
            )
            if uses:
                raise in_use("This provider", uses)
        row.enabled = details["enabled"] = changes["enabled"]
    if changes.get("api_key"):
        _seal(keyring, ctx, row, changes["api_key"])  # bound to the (new) URL
        key = "replaced"
    elif row.api_key is not None and (changes.get("clear_key") or "base_url" in details):
        row.api_key = row.key_hint = None
        key = "cleared"
    if key != "unchanged":
        details["key"] = key
    await session.flush()
    await _audit(session, ctx, "model_provider.updated", str(row.id), details)
    return row


async def _models_of(session: AsyncSession, provider_id: uuid.UUID) -> list[CatalogModel]:
    return list(
        (
            await session.scalars(
                select(CatalogModel).where(CatalogModel.provider_id == provider_id)
            )
        ).all()
    )


async def delete_provider(session: AsyncSession, ctx: RequestContext, provider_id: str) -> None:
    pid = _parse_provider_id(provider_id)
    settings_row = await _settings(session, ctx)
    row = await session.get(ModelProvider, pid)
    if row is None:
        raise not_found("Provider")
    models = await _models_of(session, pid)
    uses = await _uses(session, settings_row, _ids_of(row, models))
    if uses:
        raise in_use("A model of this provider", uses)
    await session.delete(row)  # its models go with it (ON DELETE CASCADE)
    await _audit(
        session,
        ctx,
        "model_provider.deleted",
        str(pid),
        {"name": row.name, "models": sorted(m.name for m in models)},
    )


# --- Catalog --------------------------------------------------------------------------------


async def create_model(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    provider_id: str,
    name: str,
    label: str | None,
    input_usd_per_mtok: float | None,
    output_usd_per_mtok: float | None,
    enabled: bool,
) -> CatalogModel:
    pid = _parse_provider_id(provider_id)
    await _settings(session, ctx)
    if await session.get(ModelProvider, pid) is None:
        raise not_found("Provider")
    exists = await session.scalar(
        select(CatalogModel.id).where(CatalogModel.provider_id == pid, CatalogModel.name == name)
    )
    if exists:
        raise ApiError(409, "model_exists", "This provider already has a model with this name")
    row = CatalogModel(
        tenant_id=ctx.tenant_id,
        provider_id=pid,
        name=name,
        label=label or name,
        input_usd_per_mtok=input_usd_per_mtok,
        output_usd_per_mtok=output_usd_per_mtok,
        enabled=enabled,
    )
    session.add(row)
    await session.flush()
    await _audit(session, ctx, "model_catalog.created", f"{pid}:{name}", _model_details(row))
    return row


def _model_details(row: CatalogModel) -> dict[str, Any]:
    return {
        "provider_id": str(row.provider_id),
        "name": row.name,
        "label": row.label,
        "input_usd_per_mtok": row.input_usd_per_mtok,
        "output_usd_per_mtok": row.output_usd_per_mtok,
        "enabled": row.enabled,
    }


async def _model_row(session: AsyncSession, model_id: str) -> CatalogModel:
    pid, name = _parse_model_id(model_id)
    row = await session.scalar(
        select(CatalogModel).where(CatalogModel.provider_id == pid, CatalogModel.name == name)
    )
    if row is None:
        raise not_found("Model")
    return row


async def update_model(
    session: AsyncSession, ctx: RequestContext, model_id: str, changes: Mapping[str, Any]
) -> CatalogModel:
    """`changes` holds only what the admin sent: label, prices (null clears one), enabled."""
    settings_row = await _settings(session, ctx)
    row = await _model_row(session, model_id)
    if changes.get("enabled") is False and row.enabled:
        uses = await _uses(session, settings_row, {model_id})
        if uses:
            raise in_use("This model", uses)
    for key in ("label", "input_usd_per_mtok", "output_usd_per_mtok", "enabled"):
        if key in changes:
            setattr(row, key, changes[key])
    await session.flush()
    await _audit(session, ctx, "model_catalog.updated", model_id, _model_details(row))
    return row


async def delete_model(session: AsyncSession, ctx: RequestContext, model_id: str) -> None:
    settings_row = await _settings(session, ctx)
    row = await _model_row(session, model_id)
    uses = await _uses(session, settings_row, {model_id})
    if uses:
        raise in_use("This model", uses)
    await session.delete(row)
    await _audit(session, ctx, "model_catalog.deleted", model_id, _model_details(row))


# --- Roles and policy -------------------------------------------------------------------------


async def set_roles(
    session: AsyncSession,
    ctx: RequestContext,
    system: SystemCatalog,
    roles: Mapping[Role, str | None],
) -> None:
    """None for a role means the deployment's default."""
    row = await _settings(session, ctx)
    catalog = Catalog(system, await load_config(session, ctx.tenant_id))
    for role, model_id in roles.items():
        if model_id is not None and not catalog.usable(model_id):
            raise ApiError(
                422,
                "unknown_model",
                f"The model for the {role} role is not in the catalog or is disabled",
                model_id,
            )
    row.roles = {role: model_id for role, model_id in roles.items() if model_id is not None}
    await _audit(session, ctx, "model_roles.updated", "roles", {"roles": row.roles})


async def set_policy(
    session: AsyncSession,
    ctx: RequestContext,
    system: SystemCatalog,
    *,
    max_external_sensitivity: Sensitivity,
    local_model: str | None,
) -> None:
    row = await _settings(session, ctx)
    catalog = Catalog(system, await load_config(session, ctx.tenant_id))
    if local_model is not None:
        if not catalog.usable(local_model):
            raise ApiError(422, "unknown_model", "This model is not in the catalog or is disabled")
        if not catalog.is_local(local_model):
            raise ApiError(
                422,
                "not_local",
                "The data policy needs a local model",
                "Its provider must be marked as local: requests stay on your infrastructure.",
            )
    row.policy = {
        "max_external_sensitivity": max_external_sensitivity,
        "local_model": local_model,
    }
    await _audit(session, ctx, "model_policy.updated", "policy", dict(row.policy))


# --- Testing a provider -----------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    latency_ms: int | None
    error: str | None
    model: str | None


@dataclass(frozen=True)
class Probe:
    """Everything one test request needs, gathered before any network call."""

    provider: ProviderInfo
    model: str | None
    base_url: str | None
    api_key: str | None = field(default=None, repr=False)
    problem: str | None = None  # why the test cannot run at all


def _status_error(status: int, code: str | None) -> str:
    words = {
        400: "The server rejected the request (HTTP 400): check the model name.",
        401: "The server refused the API key (HTTP 401).",
        403: "The server refused the API key (HTTP 403).",
        404: "Not found (HTTP 404): check the base URL and the model name.",
        429: "The server is rate limiting requests (HTTP 429).",
    }
    message = words.get(status, f"The server answered HTTP {status}.")
    return f"{message} Error code: {code}." if code else message


def _error_code(body: object) -> str | None:
    """A provider's error type or code ("authentication_error"), if it is a short slug.
    Never the message: servers echo keys and internal details there."""
    if not isinstance(body, dict):
        return None
    error = cast(dict[str, object], body).get("error")
    if not isinstance(error, dict):
        return None
    for name in ("type", "code"):
        value = cast(dict[str, object], error).get(name)
        if isinstance(value, str) and _ERROR_CODE.match(value):
            return value
    return None


async def probe(target: Probe, *, transport: httpx.AsyncBaseTransport | None = None) -> ProbeResult:
    """One tiny request (at most 10 s). The result's error is plain words, a status code and
    at most a provider's error code: never a response body, never the key."""
    if target.problem is not None:
        return ProbeResult(False, None, target.problem, target.model)
    if target.model is None:
        return ProbeResult(
            False, None, "Add a model to this provider first, or name one to test.", None
        )
    if target.base_url and not target.provider.system:
        try:  # names are resolved again now: they may point elsewhere than when saved
            await check_base_url_resolved(target.base_url)
        except UnsafeUrlError as exc:
            return ProbeResult(False, None, f"The base URL is refused: {exc}.", target.model)
    started = time.perf_counter()
    try:
        async with asyncio.timeout(TEST_TIMEOUT_S + 0.5):
            if target.provider.kind == "anthropic":
                error = await _probe_anthropic(target)
            else:
                error = await _probe_openai(target, transport)
    except TimeoutError:
        error = f"No answer within {TEST_TIMEOUT_S:.0f} seconds."
    except Exception as exc:  # e.g. the SDK finds no credentials: the type, never the message
        error = f"The request failed ({type(exc).__name__})."
    latency = round((time.perf_counter() - started) * 1000)
    if error is not None:
        return ProbeResult(False, latency, scrub(error, target.api_key), target.model)
    return ProbeResult(True, latency, None, target.model)


async def _probe_anthropic(target: Probe) -> str | None:
    if target.provider.system:  # the deployment's own configuration, as it runs
        client = anthropic.AsyncAnthropic(
            api_key=target.api_key, max_retries=0, timeout=TEST_TIMEOUT_S
        )
    elif not target.api_key:
        return "This provider has no API key."
    else:
        client = tenant_client(
            api_key=target.api_key,
            base_url=target.base_url,
            timeout=TEST_TIMEOUT_S,
            max_retries=0,
        )
    try:
        await client.messages.create(
            model=target.model or "",
            max_tokens=16,
            messages=[{"role": "user", "content": TEST_PROMPT}],
        )
    except anthropic.APIStatusError as exc:
        return _status_error(exc.status_code, _error_code(exc.body))
    except anthropic.APITimeoutError:
        return f"No answer within {TEST_TIMEOUT_S:.0f} seconds."
    except anthropic.APIConnectionError:
        return "Could not connect to the server."
    except anthropic.AnthropicError as exc:
        return f"The request failed ({type(exc).__name__})."
    finally:
        await client.close()
    return None


async def _probe_openai(target: Probe, transport: httpx.AsyncBaseTransport | None) -> str | None:
    if not target.base_url:
        return "This provider has no base URL."
    headers = {"Authorization": f"Bearer {target.api_key}"} if target.api_key else {}
    body = {
        "model": target.model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": TEST_PROMPT}],
    }
    async with httpx.AsyncClient(
        transport=transport, timeout=TEST_TIMEOUT_S, follow_redirects=False
    ) as http:
        try:
            response = await http.post(
                target.base_url.rstrip("/") + "/chat/completions", json=body, headers=headers
            )
        except httpx.TimeoutException:
            return f"No answer within {TEST_TIMEOUT_S:.0f} seconds."
        except httpx.HTTPError:
            return "Could not connect to the server."
    if response.is_redirect:
        return f"The server redirected the request (HTTP {response.status_code}); not followed."
    if response.is_error:
        try:
            parsed: object = response.json()
        except ValueError:
            parsed = None
        return _status_error(response.status_code, _error_code(parsed))
    try:
        reply: Any = response.json()
        reply["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        return "The server answered, but not with a chat completion."
    return None


async def gather_probe(
    session: AsyncSession,
    ctx: RequestContext,
    settings: Settings,
    system: SystemCatalog,
    keyring: KeyRing,
    provider_id: str,
    model: str | None,
) -> Probe:
    """The provider, the model to try (the one named, else its first enabled model), its URL
    and key, read in the admin's tenant transaction."""
    if provider_id == SYSTEM:
        anthropic_kind = settings.llm_provider == "anthropic"
        return Probe(
            system.providers[0],
            model or system.role_models["router"],
            None if anthropic_kind else settings.llm_base_url,
            secret(settings.anthropic_api_key if anthropic_kind else settings.llm_api_key),
        )
    if provider_id == SYSTEM_LOCAL:
        provider = next((p for p in system.providers if p.id == SYSTEM_LOCAL), None)
        if provider is None:
            raise not_found("Provider")
        return Probe(
            provider,
            model or settings.local_llm_model,
            settings.local_llm_base_url,
            secret(settings.local_llm_api_key),
        )
    pid = _parse_provider_id(provider_id)
    config = await load_config(session, ctx.tenant_id, keyring)
    provider = config.providers.get(str(pid))
    if provider is None:
        raise not_found("Provider")
    if model is None:
        model = next(
            (m.name for m in config.models.values() if m.provider_id == provider.id and m.enabled),
            None,
        )
    problem = None
    if provider.id in config.key_errors:
        problem = "The stored API key cannot be decrypted. Enter it again."
    return Probe(provider, model, provider.base_url, config.keys.get(provider.id), problem)

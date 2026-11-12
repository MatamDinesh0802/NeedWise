"""Which model serves a request, per tenant (docs/models.md). Every model call goes through here.

    llm = await resources.models.for_tenant(ctx.tenant_id)   # a TenantLLM: an LLMProvider
    await llm.complete("router", request)                    # the tenant's model for the role
    agent_llm = llm.select(agent.model)                      # an agent's own model, or None
    route(passages, policy=llm.max_external_sensitivity, llm=llm, local_llm=llm.local)

A tenant that configured nothing gets the deployment's providers exactly as before: every role
goes to `system` (the deployment's LLMProvider, `resources.llm`) with the role unchanged.

Resolution is cached per tenant. After `ttl_s`, one small query compares the tenant's settings
version (bumped by every change in Admin > Models) and its `restricted_data` setting; a change
reloads. So a change made in one process (the API) reaches the others (more API processes,
Celery workers) within `ttl_s`; the process that made it drops its cache at once.

A tenant key is decrypted when the tenant's configuration loads, kept in memory, given only to
that provider's client, and scrubbed from any error the provider returns.

Usage accounting happens here too, at the one place every model call passes (docs/usage.md):
before a call, the meter checks the budgets that bind it (and may refuse it with
BudgetExceededError); after it, finished, failed or cancelled, the meter records one usage event
with what the provider reported, attributed from `workmind.modules.usage.calls`.
"""

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import NoReturn

import anthropic
import httpx
import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.config import Settings
from workmind.core.tenancy import tenant_transaction
from workmind.modules.model_catalog.catalog import (
    SYSTEM,
    SYSTEM_LOCAL,
    Catalog,
    ProviderInfo,
    SystemCatalog,
    TenantConfig,
    catalog_id,
    system_catalog,
)
from workmind.modules.model_catalog.policy import Sensitivity
from workmind.modules.model_catalog.secrets import KeyRing
from workmind.modules.model_catalog.store import load_config, read_stamp
from workmind.modules.model_catalog.urls import UnsafeUrlError, check_base_url
from workmind.modules.usage.calls import Attribution, CallMeter, ModelCall, current
from workmind.providers.factory import single_model_roles
from workmind.providers.llm import (
    AnthropicLLM,
    Done,
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMUsage,
    OpenAICompatibleLLM,
    PinnedModel,
    Role,
    StreamEvent,
    ToolExecutor,
    ToolLoopResult,
    ToolSpec,
    UsageProbe,
    tenant_client,
)

log = structlog.get_logger("workmind.models")

DEFAULT_TTL_S = 5.0
REDACTED = "[redacted]"


def scrub(text: str, secret: str | None) -> str:
    """`text` with the key, and its first and last 8 characters, cut out (servers sometimes
    echo part of a key in an error)."""
    if not secret:
        return text
    text = text.replace(secret, REDACTED)
    if len(secret) >= 16:
        text = text.replace(secret[:8], REDACTED).replace(secret[-8:], REDACTED)
    return text


class BrokenLLM:
    """Stands in for a provider that cannot be used (no key, a key that does not decrypt, an
    unsafe URL): every call fails with a message an admin can act on."""

    external = True

    def __init__(self, message: str) -> None:
        self.message = message

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        raise LLMError(self.message)

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        raise LLMError(self.message)
        yield  # pragma: no cover  (makes this an async generator)

    async def structured[T: BaseModel](
        self, role: Role, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMUsage]:
        raise LLMError(self.message)

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult:
        raise LLMError(self.message)


# Builds the client for one model of a tenant provider: (provider, model name, key) -> client.
ClientFactory = Callable[[ProviderInfo, str, str | None], LLMProvider]


def tenant_llm_factory(
    http: httpx.AsyncClient, *, fallbacks: bool = True, timeout: float = 300, max_retries: int = 3
) -> ClientFactory:
    """Real clients. One Anthropic client per provider and key (they hold connection pools)."""
    anthropic_clients: dict[tuple[str, str, str | None], anthropic.AsyncAnthropic] = {}

    def build(provider: ProviderInfo, model: str, api_key: str | None) -> LLMProvider:
        try:
            base_url = check_base_url(provider.base_url) if provider.base_url else None
        except UnsafeUrlError as exc:
            return BrokenLLM(f"The base URL of the provider {provider.name!r} is refused: {exc}.")
        roles = single_model_roles(model)
        if provider.kind == "anthropic":
            if not api_key:
                return BrokenLLM(f"The provider {provider.name!r} has no API key.")
            cache_key = (provider.id, api_key, base_url)
            if cache_key not in anthropic_clients:
                anthropic_clients[cache_key] = tenant_client(
                    api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries
                )
            return AnthropicLLM(
                roles, api_key=api_key, fallbacks=fallbacks, client=anthropic_clients[cache_key]
            )
        if base_url is None:
            return BrokenLLM(f"The provider {provider.name!r} has no base URL.")
        return OpenAICompatibleLLM(
            http,
            roles,
            base_url=base_url,
            api_key=api_key,
            external=provider.external,
            timeout=timeout,
        )

    return build


@dataclass(frozen=True)
class Target:
    """One catalog model, ready to call for a role."""

    model_id: str
    client: LLMProvider
    external: bool
    price: tuple[float, float] | None = None  # the tenant's catalog price
    secret: str | None = field(default=None, repr=False)  # scrubbed from provider errors
    name: str = ""  # the model name the provider expects
    provider: str = ""  # the provider's kind: "anthropic" or "openai_compatible"

    def stamp(self, usage: LLMUsage) -> LLMUsage:
        return replace(usage, catalog_model=self.model_id, price_per_mtok=self.price)

    def error(self, exc: LLMError) -> LLMError:
        return type(exc)(scrub(str(exc), self.secret))

    def raise_from(self, exc: BaseException) -> NoReturn:
        """Re-raises `exc`, with the tenant's key cut out of a provider error."""
        if isinstance(exc, LLMError) and self.secret is not None:
            raise self.error(exc) from None
        raise exc


@dataclass
class _Call:
    """One metered call in progress."""

    target: Target
    method: str
    role: Role
    request: LLMRequest  # with the probe the adapter reports usage to
    probe: UsageProbe
    attribution: Attribution | None  # read when the call started
    started: float


def _status(error: BaseException | None) -> str:
    if error is None:
        return "ok"
    if isinstance(error, asyncio.CancelledError | GeneratorExit):
        return "cancelled"
    return "failed"


class TenantLLM:
    """An LLMProvider for one tenant: each role goes to the catalog model the tenant chose (or
    the deployment's default). Also carries the tenant's data policy for routing by
    sensitivity: `max_external_sensitivity` and `local`, the model sensitive sources may go to.

    `external` is True when any role's model is external, so routing that looks at it is
    cautious when roles mix local and external models.

    Its four methods are the one place every model call passes through: each asks the usage
    meter first (budgets) and records one usage event at the end (docs/usage.md)."""

    def __init__(
        self,
        targets: Mapping[Role, Target],
        *,
        max_external_sensitivity: Sensitivity,
        local: "TenantLLM | None",
        override: Callable[[str], "TenantLLM | None"] | None = None,
        tenant_id: uuid.UUID | None = None,
        meter: CallMeter | None = None,
    ) -> None:
        self.targets = dict(targets)
        self.external = any(t.external for t in self.targets.values())
        self.max_external_sensitivity: Sensitivity = max_external_sensitivity
        self.local = local
        self.tenant_id = tenant_id
        self._override = override
        self._meter = meter

    def model_for(self, role: Role) -> str:
        """The catalog id of the model that serves `role`."""
        return self.targets[role].model_id

    def using(self, model_id: str | None) -> "TenantLLM":
        """This tenant's resolution with every role on `model_id` (an agent's own model).
        None, or a model that is not usable (deleted, disabled), keeps the role defaults."""
        if not model_id:
            return self
        chosen = self.select(model_id)
        if chosen is None:
            log.warning("model_override_unavailable", model=model_id)
            return self
        return chosen

    def select(self, model_id: str) -> "TenantLLM | None":
        """Every role on `model_id`, or None when it is not a usable model of the catalog."""
        return self._override(model_id) if self._override is not None else None

    # --- Metering ------------------------------------------------------------------------------

    async def _start(self, role: Role, method: str, request: LLMRequest) -> _Call:
        """Reads the attribution and lets the meter refuse the call (BudgetExceededError) before
        anything reaches a model."""
        attribution = current()
        if self._meter is not None and self.tenant_id is not None:
            await self._meter.admit(self.tenant_id, attribution)
        probe = UsageProbe()
        return _Call(
            self.targets[role],
            method,
            role,
            replace(request, probe=probe),
            probe,
            attribution,
            time.perf_counter(),
        )

    async def _end(
        self,
        call: _Call,
        *,
        usages: list[LLMUsage] | None = None,
        error: BaseException | None = None,
    ) -> None:
        """One usage event for the call: what it returned, else what the provider reported
        before it failed or was stopped."""
        if self._meter is None or self.tenant_id is None:
            return
        target = call.target
        seen = usages if usages is not None else [target.stamp(u) for u in call.probe.seen]
        await self._meter.record(
            self.tenant_id,
            call.attribution,
            ModelCall(
                method=call.method,
                role=call.role,
                model_id=target.model_id,
                model=next((u.model for u in reversed(seen) if u.model), target.name),
                provider=target.provider,
                local=not target.external,
                status=_status(error),  # pyright: ignore[reportArgumentType]
                latency_ms=round((time.perf_counter() - call.started) * 1000),
                usages=seen,
                price_per_mtok=target.price,
            ),
        )

    # --- LLMProvider --------------------------------------------------------------------------

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        call = await self._start(role, "complete", request)
        target = call.target
        try:
            text, usage = await target.client.complete(role, call.request)
        except BaseException as exc:
            await self._end(call, error=exc)
            target.raise_from(exc)
        usage = target.stamp(usage)
        await self._end(call, usages=[usage])
        return text, usage

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        """Recorded when the stream ends: finished, failed, or stopped by the consumer (then
        with what the provider had reported so far)."""
        call = await self._start(role, "stream", request)
        target = call.target
        inner = target.client.stream(role, call.request)
        final: LLMUsage | None = None
        try:
            async for event in inner:
                if isinstance(event, Done):
                    final = target.stamp(event.usage)
                    yield Done(final)
                else:
                    yield event
        except BaseException as exc:
            close = getattr(inner, "aclose", None)
            if close is not None and final is None:
                with contextlib.suppress(Exception):  # already failing: closing is best effort
                    await close()
            if final is not None:  # the consumer stopped after the last event: it finished
                await self._end(call, usages=[final])
            else:
                await self._end(call, error=exc)
            target.raise_from(exc)
        await self._end(call, usages=[final] if final is not None else None)

    async def structured[T: BaseModel](
        self, role: Role, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMUsage]:
        call = await self._start(role, "structured", request)
        target = call.target
        try:
            value, usage = await target.client.structured(role, call.request, schema)
        except BaseException as exc:  # a reply that fails validation was still billed
            await self._end(call, error=exc)
            target.raise_from(exc)
        usage = target.stamp(usage)
        await self._end(call, usages=[usage])
        return value, usage

    async def run_tools(
        self,
        role: Role,
        request: LLMRequest,
        tools: Sequence[ToolSpec],
        execute: ToolExecutor,
        *,
        max_steps: int = 6,
    ) -> ToolLoopResult:
        """One event for the whole loop, with the usage of every model turn."""
        call = await self._start(role, "run_tools", request)
        target = call.target
        try:
            result = await target.client.run_tools(
                role, call.request, tools, execute, max_steps=max_steps
            )
        except BaseException as exc:
            await self._end(call, error=exc)
            target.raise_from(exc)
        result.usage = [target.stamp(u) for u in result.usage]
        await self._end(call, usages=result.usage)
        return result


def with_model(llm: LLMProvider, model_id: str | None) -> LLMProvider:
    """`llm` with every role on catalog model `model_id`, when `llm` is a tenant's resolution;
    any other provider (a test's scripted model) is returned unchanged."""
    if model_id and isinstance(llm, TenantLLM):
        return llm.using(model_id)
    return llm


@dataclass
class _Entry:
    config: TenantConfig
    checked_at: float
    clients: dict[str, LLMProvider] = field(default_factory=dict[str, LLMProvider])


class LLMResolver:
    """Per-tenant model resolution with a short-lived cache (see the module docstring)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        http: httpx.AsyncClient,
        keyring: KeyRing,
        *,
        system: LLMProvider,
        system_local: LLMProvider | None,
        ttl_s: float = DEFAULT_TTL_S,
        build: ClientFactory | None = None,
    ) -> None:
        # The deployment's providers. Assignable (tests swap in scripted models).
        self.system = system
        self.system_local = system_local
        self.keyring = keyring
        self.settings = settings
        self.ttl_s = ttl_s
        # How provider tests (Admin > Models) reach the network; tests put a mock here.
        self.probe_transport: httpx.AsyncBaseTransport | None = None
        self._sessions = sessions
        self._catalog: SystemCatalog = system_catalog(settings)
        # Builds tenant providers' clients (assignable).
        self.build = build or tenant_llm_factory(http, fallbacks=settings.llm_fallbacks)
        # Usage accounting and budgets for every call (docs/usage.md). None: not metered.
        self.meter: CallMeter | None = None
        self._cache: dict[uuid.UUID, _Entry] = {}

    @property
    def system_catalog(self) -> SystemCatalog:
        return self._catalog

    def invalidate(self, tenant_id: uuid.UUID) -> None:
        self._cache.pop(tenant_id, None)

    async def catalog(self, tenant_id: uuid.UUID) -> Catalog:
        """The tenant's catalog, freshly loaded (for administration)."""
        return Catalog(self._catalog, await self._load(tenant_id))

    async def for_tenant(self, tenant_id: uuid.UUID) -> TenantLLM:
        entry = await self._entry(tenant_id)
        return self._view(tenant_id, Catalog(self._catalog, entry.config), entry)

    # --- Loading ------------------------------------------------------------------------------

    async def _entry(self, tenant_id: uuid.UUID) -> _Entry:
        now = time.monotonic()
        entry = self._cache.get(tenant_id)
        if entry is not None and now - entry.checked_at < self.ttl_s:
            return entry
        if entry is not None and await self._stamp(tenant_id) == entry.config.stamp:
            entry.checked_at = now
            return entry
        entry = _Entry(await self._load(tenant_id), now)
        self._cache[tenant_id] = entry
        return entry

    async def _stamp(self, tenant_id: uuid.UUID) -> tuple[int, str | None]:
        async with tenant_transaction(self._sessions, tenant_id) as session:
            return await read_stamp(session, tenant_id)

    async def _load(self, tenant_id: uuid.UUID) -> TenantConfig:
        async with tenant_transaction(self._sessions, tenant_id) as session:
            return await load_config(session, tenant_id, self.keyring)

    # --- Resolution ---------------------------------------------------------------------------

    def _view(
        self,
        tenant_id: uuid.UUID,
        catalog: Catalog,
        entry: _Entry,
        *,
        model_id: str | None = None,
    ) -> TenantLLM:
        roles = catalog.roles
        targets: dict[Role, Target] = {
            role: self._target(catalog, entry, model_id or roles[role], role) for role in roles
        }
        local: TenantLLM | None = None
        local_id = catalog.local_model
        if local_id is None and "local_model" not in catalog.tenant.policy:
            local_id = self._default_local_id()
        if local_id is not None and (catalog.usable(local_id) or local_id.startswith(SYSTEM_LOCAL)):
            local_targets: dict[Role, Target] = {
                role: self._target(catalog, entry, local_id, role) for role in roles
            }
            if not any(t.external for t in local_targets.values()):
                local = TenantLLM(
                    local_targets,
                    max_external_sensitivity=catalog.max_external_sensitivity,
                    local=None,
                    tenant_id=tenant_id,
                    meter=self.meter,
                )

        def override(chosen: str) -> TenantLLM | None:
            if not catalog.usable(chosen):
                return None
            return self._view(tenant_id, catalog, entry, model_id=chosen)

        return TenantLLM(
            targets,
            max_external_sensitivity=catalog.max_external_sensitivity,
            local=local,
            override=override,
            tenant_id=tenant_id,
            meter=self.meter,
        )

    def _default_local_id(self) -> str | None:
        """The deployment's local model, also when it was handed in without settings (tests)."""
        if self.system_local is None:
            return None
        return self._catalog.local_model or catalog_id(SYSTEM_LOCAL, "local")

    def _target(self, catalog: Catalog, entry: _Entry, model_id: str, role: Role) -> Target:
        model = catalog.model(model_id)
        provider_id = model.provider_id if model else model_id.split(":", 1)[0]
        unlisted = model_id.split(":", 1)[-1]
        if provider_id == SYSTEM_LOCAL:
            client = self.system_local or BrokenLLM("No local model is configured.")
            return Target(
                model_id,
                client,
                client.external,
                name=model.name if model else unlisted,
                provider="openai_compatible",
            )
        system_kind = self.settings.llm_provider
        if provider_id == SYSTEM:
            if model is None:  # not reachable through Catalog.roles
                return Target(
                    model_id,
                    BrokenLLM("This model is not in the catalog."),
                    True,
                    name=unlisted,
                    provider=system_kind,
                )
            name = model.name
            if name == self._catalog.role_models[role]:
                client = self.system  # exactly what the deployment does for this role
            else:  # another model of the deployment's provider: the same client, that model
                client = PinnedModel(self.system, name)
            return Target(model_id, client, client.external, name=name, provider=system_kind)
        provider = catalog.provider(provider_id)
        if model is None or provider is None:  # not reachable through Catalog.roles
            return Target(
                model_id,
                BrokenLLM("This model is not in the catalog."),
                True,
                name=unlisted,
                provider=provider.kind if provider else "",
            )
        if model_id not in entry.clients:
            if provider.id in entry.config.key_errors:
                entry.clients[model_id] = BrokenLLM(
                    f"The API key of the provider {provider.name!r} cannot be decrypted "
                    f"({entry.config.key_errors[provider.id]}). Enter it again in Admin > Models."
                )
            else:
                entry.clients[model_id] = self.build(
                    provider, model.name, entry.config.keys.get(provider.id)
                )
        client = entry.clients[model_id]
        return Target(
            model_id,
            client,
            provider.external or client.external,
            price=model.price,
            secret=entry.config.keys.get(provider.id),
            name=model.name,
            provider=provider.kind,
        )

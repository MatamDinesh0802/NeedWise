"""The model catalog a tenant sees: the deployment's read-only "system" provider and models,
then the tenant's own; the role mapping and the data policy, with the deployment's defaults
for anything the tenant did not choose (docs/models.md).

Catalog ids are "<provider id>:<model name>": "system:claude-sonnet-5-5" for the deployment's
models, "system-local:<name>" for its local model (LOCAL_LLM_*), "<provider uuid>:<name>" for
a tenant's. A model's name never changes, so its id is stable; agents store it.
"""

from dataclasses import dataclass, field
from typing import Any, get_args

from needwise.core.config import Settings
from needwise.modules.model_catalog.policy import LEVELS, Sensitivity, default_threshold
from needwise.providers.factory import ROLE_EFFORT, role_configs, secret
from needwise.providers.llm import Role
from needwise.providers.pricing import PRICES

SYSTEM = "system"
SYSTEM_LOCAL = "system-local"
ROLES: tuple[Role, ...] = get_args(Role)
assert set(ROLES) == set(ROLE_EFFORT)  # noqa: S101  (one list of roles)


def catalog_id(provider_id: str, name: str) -> str:
    return f"{provider_id}:{name}"


def provider_of(model_id: str) -> str:
    return model_id.split(":", 1)[0]


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    kind: str  # "anthropic" | "openai_compatible"
    base_url: str | None
    local: bool
    has_key: bool
    key_hint: str | None
    enabled: bool
    system: bool

    @property
    def external(self) -> bool:
        """Whether requests leave infrastructure the organization controls."""
        return self.kind == "anthropic" or not self.local


@dataclass(frozen=True)
class ModelInfo:
    id: str
    provider_id: str
    name: str
    label: str
    local: bool
    input_usd_per_mtok: float | None
    output_usd_per_mtok: float | None
    enabled: bool

    @property
    def price(self) -> tuple[float, float] | None:
        if self.input_usd_per_mtok is None or self.output_usd_per_mtok is None:
            return None
        return (self.input_usd_per_mtok, self.output_usd_per_mtok)


@dataclass(frozen=True)
class SystemCatalog:
    providers: list[ProviderInfo]
    models: list[ModelInfo]
    role_models: dict[Role, str]  # role -> model name the deployment configures for it
    local_model: str | None  # catalog id of the deployment's local model, if any


def system_catalog(settings: Settings) -> SystemCatalog:
    """The deployment's provider and models, from its settings. Read-only for every tenant.
    Listed: the models configured for a role, and for Anthropic every model with a known
    price, so a tenant can choose e.g. Opus for one role."""
    roles: dict[Role, str] = {role: config.model for role, config in role_configs(settings).items()}
    anthropic = settings.llm_provider == "anthropic"
    local = not anthropic and not settings.llm_external
    key = settings.anthropic_api_key if anthropic else settings.llm_api_key
    providers = [
        ProviderInfo(
            id=SYSTEM,
            name="Deployment default",
            kind=settings.llm_provider,
            base_url=None if anthropic else settings.llm_base_url,
            local=local,
            has_key=secret(key) is not None,
            key_hint=None,  # the deployment's key is not the tenant's business, not even a hint
            enabled=True,
            system=True,
        )
    ]
    names = list(dict.fromkeys([*roles.values(), *(PRICES if anthropic else ())]))
    models = [
        ModelInfo(
            id=catalog_id(SYSTEM, name),
            provider_id=SYSTEM,
            name=name,
            label=name,
            local=local,
            input_usd_per_mtok=PRICES[name][0] if name in PRICES else None,
            output_usd_per_mtok=PRICES[name][1] if name in PRICES else None,
            enabled=True,
        )
        for name in names
    ]
    local_model: str | None = None
    if settings.local_llm_base_url and settings.local_llm_model:
        providers.append(
            ProviderInfo(
                id=SYSTEM_LOCAL,
                name="Deployment local model",
                kind="openai_compatible",
                base_url=settings.local_llm_base_url,
                local=True,
                has_key=secret(settings.local_llm_api_key) is not None,
                key_hint=None,
                enabled=True,
                system=True,
            )
        )
        local_model = catalog_id(SYSTEM_LOCAL, settings.local_llm_model)
        models.append(
            ModelInfo(
                id=local_model,
                provider_id=SYSTEM_LOCAL,
                name=settings.local_llm_model,
                label=settings.local_llm_model,
                local=True,
                input_usd_per_mtok=None,
                output_usd_per_mtok=None,
                enabled=True,
            )
        )
    return SystemCatalog(providers, models, roles, local_model)


@dataclass
class TenantConfig:
    """What one tenant stored, as loaded (keys decrypted in memory, never serialized)."""

    stamp: tuple[int, str | None] = (0, None)  # (settings version, restricted_data)
    providers: dict[str, ProviderInfo] = field(default_factory=dict[str, ProviderInfo])
    models: dict[str, ModelInfo] = field(default_factory=dict[str, ModelInfo])
    keys: dict[str, str] = field(default_factory=dict[str, str])  # provider id -> key
    key_errors: dict[str, str] = field(default_factory=dict[str, str])  # provider id -> why
    roles: dict[str, Any] = field(default_factory=dict[str, Any])  # as stored (explicit only)
    policy: dict[str, Any] = field(default_factory=dict[str, Any])  # as stored (explicit only)
    tenant_settings: dict[str, Any] = field(default_factory=dict[str, Any])


class Catalog:
    """The deployment's catalog and one tenant's, merged, with effective roles and policy."""

    def __init__(self, system: SystemCatalog, tenant: TenantConfig) -> None:
        self.system = system
        self.tenant = tenant
        self._providers = {p.id: p for p in system.providers} | tenant.providers
        self._models = {m.id: m for m in system.models} | tenant.models

    @property
    def providers(self) -> list[ProviderInfo]:
        return list(self._providers.values())

    @property
    def models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def provider(self, provider_id: str) -> ProviderInfo | None:
        return self._providers.get(provider_id)

    def model(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def usable(self, model_id: str | None) -> bool:
        """The model exists and it and its provider are enabled."""
        model = self._models.get(model_id or "")
        provider = self._providers.get(model.provider_id) if model else None
        return bool(model and model.enabled and provider and provider.enabled)

    def is_local(self, model_id: str | None) -> bool:
        model = self._models.get(model_id or "")
        provider = self._providers.get(model.provider_id) if model else None
        return bool(provider and not provider.external)

    @property
    def role_defaults(self) -> dict[Role, str]:
        return {role: catalog_id(SYSTEM, name) for role, name in self.system.role_models.items()}

    @property
    def roles(self) -> dict[Role, str]:
        """Each role's model: the tenant's choice while it is usable, else the default."""
        chosen = self.tenant.roles
        return {
            role: chosen[role] if self.usable(chosen.get(role)) else default
            for role, default in self.role_defaults.items()
        }

    @property
    def policy_defaults(self) -> dict[str, Any]:
        return {
            "max_external_sensitivity": default_threshold(self.tenant.tenant_settings),
            "local_model": self.system.local_model,
        }

    @property
    def max_external_sensitivity(self) -> Sensitivity:
        value = self.tenant.policy.get("max_external_sensitivity")
        if value in LEVELS:
            return value
        return default_threshold(self.tenant.tenant_settings)

    @property
    def local_model(self) -> str | None:
        """The model sensitive sources may go to: the tenant's choice (which may be "none"),
        else the deployment's local model. Never one that is external or unusable."""
        if "local_model" in self.tenant.policy:
            chosen = self.tenant.policy["local_model"]
            ok = isinstance(chosen, str) and self.usable(chosen) and self.is_local(chosen)
            return chosen if ok else None
        return self.system.local_model

    @property
    def policy(self) -> dict[str, Any]:
        return {
            "max_external_sensitivity": self.max_external_sensitivity,
            "local_model": self.local_model,
        }

    def uses(self, model_ids: set[str]) -> list[str]:
        """Where the tenant's roles and policy refer to these models, in words."""
        found = [f"the {role} role" for role in ROLES if self.tenant.roles.get(role) in model_ids]
        if self.tenant.policy.get("local_model") in model_ids:
            found.append("the data policy's local model")
        return found

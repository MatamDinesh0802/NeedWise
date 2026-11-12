"""Which model an agent's calls use (docs/agents.md, "Model").

An agent definition stores a *model catalog id* or nothing. Nothing means the model the tenant
maps to the agent's role (`agent`, Admin > Models), or the deployment's default for it. A
catalog id is `<provider id>:<model name>` (docs/models.md): `system:<model>` is a model of the
deployment's own provider, `system-local:<model>` its local model, and a UUID names a provider a
tenant administrator added (bring-your-own key or a local server).

Ids are checked against the tenant's catalog when an agent is saved, and again on every run:
a model that was disabled or deleted since then fails that agent's run ("model unavailable")
rather than quietly sending the question to a different model than the one configured.
"""

import re
import uuid
from typing import Protocol

from workmind.modules.model_catalog.resolver import TenantLLM
from workmind.providers.llm import LLMError, LLMProvider, PinnedModel

SYSTEM_PROVIDER = "system"
SYSTEM_LOCAL_PROVIDER = "system-local"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# The model name is what the provider expects ("claude-sonnet-5-5", "llama3.3:70b", ...).
MODEL_ID_PATTERN = rf"^(?:{SYSTEM_PROVIDER}|{SYSTEM_LOCAL_PROVIDER}|{_UUID}):\S{{1,200}}$"
_MODEL_ID = re.compile(MODEL_ID_PATTERN)


def is_model_id(value: str) -> bool:
    return bool(_MODEL_ID.match(value))


def split_model_id(model_id: str) -> tuple[str, str]:
    """("system", "claude-sonnet-5-5") from "system:claude-sonnet-5-5"."""
    provider, _, name = model_id.partition(":")
    return provider, name


class ModelResolver(Protocol):
    async def agent_llm(
        self, tenant_id: uuid.UUID, model_id: str | None, base: LLMProvider
    ) -> LLMProvider:
        """The provider to run an agent configured with `model_id` on. `base` is the provider
        the rest of the question uses. Raises LLMError when the model cannot be used."""
        ...


class CatalogModels:
    """Agent models from the tenant's catalog. When `base` is a tenant's resolution (every
    request path: `resources.models.for_tenant`), the agent runs on that catalog model, and an
    id that is not usable fails. Any other `base` (a scripted model in tests, a bare
    deployment provider) can only run `system:<model>`, pinned on that provider."""

    async def agent_llm(
        self, tenant_id: uuid.UUID, model_id: str | None, base: LLMProvider
    ) -> LLMProvider:
        if model_id is None:
            return base
        if isinstance(base, TenantLLM):
            chosen = base.select(model_id)
            if chosen is None:
                raise LLMError(
                    f"model {model_id!r} is not an enabled model in this organization's catalog"
                )
            return chosen
        provider, name = split_model_id(model_id)
        if provider == SYSTEM_PROVIDER and name:
            return PinnedModel(base, name)
        raise LLMError(f"model {model_id!r} is not in this deployment's model catalog")

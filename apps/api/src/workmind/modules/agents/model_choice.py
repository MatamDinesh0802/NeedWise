"""Which model an agent's calls use (docs/agents.md, "Model").

An agent definition stores a *model catalog id* or nothing. Nothing means the model the
deployment maps to the agent's role (`agent`, see providers/factory.py). A catalog id is
`<provider id>:<model name>`: `system:<model>` is a model of the deployment's own provider; a
UUID names a provider a tenant administrator added (bring-your-own key or a local server).

Tenant providers come from the model catalog (GET /api/v1/admin/models). Until a resolver for
that catalog is passed in, only `system:` ids can run, and any other id fails the agent's run
rather than quietly sending the question to a different provider than the one configured.
"""

import re
import uuid
from typing import Protocol

from workmind.providers.llm import LLMError, LLMProvider, PinnedModel

SYSTEM_PROVIDER = "system"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# The model name is what the provider expects ("claude-sonnet-5-5", "llama3.3:70b", ...).
MODEL_ID_PATTERN = rf"^(?:{SYSTEM_PROVIDER}|{_UUID}):\S{{1,200}}$"
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
        """The provider to run an agent configured with `model_id` on. `base` is the
        deployment's provider. Raises LLMError when the model cannot be used."""
        ...


class SystemModels:
    """The resolver used until the tenant model catalog is wired in: `system:<model>` runs on
    the deployment's provider with that model; other providers are not known here."""

    async def agent_llm(
        self, tenant_id: uuid.UUID, model_id: str | None, base: LLMProvider
    ) -> LLMProvider:
        if model_id is None:
            return base
        provider, name = split_model_id(model_id)
        if provider == SYSTEM_PROVIDER and name:
            return PinnedModel(base, name)
        # INTEGRATION (models branch): look the id up in the tenant's catalog (enabled
        # provider and model, sensitivity policy) and return that provider pinned to the model.
        raise LLMError(f"model {model_id!r} is not in this deployment's model catalog")

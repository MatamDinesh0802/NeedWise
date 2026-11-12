"""The data policy for external models (ARCHITECTURE 11.6, docs/models.md).

A tenant sets the highest sensitivity that may be sent to an external model (a hosted API).
Sources above it go to the tenant's local model, or, without one, are left out of the request
and the person is told. The older tenant setting `restricted_data` maps onto it:
`local_only` (the default) is "confidential", `allow` is "restricted".
"""

from typing import Literal

Sensitivity = Literal["public", "internal", "confidential", "restricted"]
LegacyPolicy = Literal["allow", "local_only"]
RoutingPolicy = Sensitivity | LegacyPolicy

LEVELS: tuple[Sensitivity, ...] = ("public", "internal", "confidential", "restricted")
_RANK = {level: rank for rank, level in enumerate(LEVELS)}


def rank(sensitivity: str) -> int:
    """Unknown labels rank as the highest level: an unlabelled source is never sent by mistake."""
    return _RANK.get(sensitivity, len(LEVELS) - 1)


def threshold(policy: RoutingPolicy) -> Sensitivity:
    """The highest level external models may receive under `policy`."""
    if policy == "allow":
        return "restricted"
    if policy == "local_only":
        return "confidential"
    return policy


def above(sensitivity: str, policy: RoutingPolicy) -> bool:
    return rank(sensitivity) > rank(threshold(policy))


def default_threshold(tenant_settings: dict[str, object]) -> Sensitivity:
    """What a tenant without a model policy gets: today's `restricted_data` behaviour."""
    return "restricted" if tenant_settings.get("restricted_data") == "allow" else "confidential"

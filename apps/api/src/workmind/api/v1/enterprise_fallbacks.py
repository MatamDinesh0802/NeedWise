"""Stand-ins for Enterprise routes (ADR 0011, docs/enterprise.md).

The core knows these paths so a Community deployment answers them clearly: sign-in first
(401), then 402 "enterprise_feature" naming the feature and how to enable it. This router is
mounted after the Enterprise routers (workmind.main), so when workmind_ee registers the real
route at the same path, the real route answers and these never run. A licensed route whose
license expires answers 402 by itself (require_feature on its router).
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter

from workmind.api.deps import CurrentContext
from workmind.core.edition import Feature, enterprise_feature_error

router = APIRouter(tags=["enterprise"], include_in_schema=False)

# (method, path, feature): every Enterprise route the core's web app calls.
ROUTES: tuple[tuple[str, str, Feature], ...] = (
    ("GET", "/admin/retention", "retention_policies"),
    ("PUT", "/admin/retention", "retention_policies"),
    ("POST", "/admin/retention/preview", "retention_policies"),
    ("POST", "/admin/retention/run", "retention_policies"),
    ("GET", "/admin/audit-events/export.jsonl", "advanced_audit_export"),
    ("GET", "/admin/legal-holds", "legal_hold"),
    ("POST", "/admin/legal-holds", "legal_hold"),
    ("POST", "/admin/legal-holds/{hold_id}/release", "legal_hold"),
)


def _stand_in(feature: Feature) -> Callable[[CurrentContext], Coroutine[Any, Any, None]]:
    async def not_licensed(ctx: CurrentContext) -> None:
        del ctx  # signed in: the 401 comes first
        raise enterprise_feature_error(feature)

    return not_licensed


for _method, _path, _feature in ROUTES:
    router.add_api_route(
        _path, _stand_in(_feature), methods=[_method], name=f"enterprise:{_feature}:{_method}"
    )

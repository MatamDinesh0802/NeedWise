"""WorkMind AI Enterprise Edition. Commercial license: ee/LICENSE, not Apache-2.0.

The core loads this package through the "workmind.ee" entry point (workmind.core.extensions),
and only when a valid license is installed. Nothing here runs in Community.

Everything is registered in `setup`, and every registration names the licensed feature it
belongs to; the core checks that feature on every request, task run and hook call.
"""

import structlog
from workmind.core.edition import Edition
from workmind.core.extensions import ExtensionRegistry
from workmind.modules.usage.extensions import CHARGEBACK_REPORT, DEPARTMENT_BUDGETS

from workmind_ee import audit_export, document_retention, legal_hold, retention
from workmind_ee.budgets import department_budgets
from workmind_ee.chargeback import chargeback_csv

__version__ = "0.0.1"

log = structlog.get_logger("workmind_ee")

# The features this package implements: all five in workmind.core.edition.FEATURES
# (docs/enterprise.md).
IMPLEMENTED: tuple[str, ...] = (
    "department_budgets",
    "chargeback_reports",
    retention.FEATURE,
    audit_export.FEATURE,
    legal_hold.FEATURE,
)


def setup(registry: ExtensionRegistry, edition: Edition) -> None:
    """Called once per process at startup, with a valid license, before any request."""
    # Department budgets: stored, shown and enforced by the core's usage module through this.
    registry.add_hook(DEPARTMENT_BUDGETS, department_budgets, feature="department_budgets")
    # GET /api/v1/usage/export.csv (the core's route, behind require_feature) calls this.
    registry.add_hook(CHARGEBACK_REPORT, chargeback_csv, feature="chargeback_reports")
    # Retention: the policy API and the daily task that applies every tenant's policy.
    registry.add_router(retention.router, feature="retention_policies")
    registry.add_router(document_retention.router, feature="retention_policies")
    registry.add_scheduled_task(
        retention.TASK_NAME,
        retention.apply_all,
        every_s=retention.DAILY_S,
        feature="retention_policies",
    )
    # The complete, verifiable audit chain as JSON lines, for a SIEM.
    registry.add_router(audit_export.router, feature="advanced_audit_export")
    # Placing and releasing legal holds; the core enforces them on every deletion path.
    registry.add_router(legal_hold.router, feature="legal_hold")
    log.info(
        "workmind_ee_loaded",
        version=__version__,
        licensed=edition.features,
        implemented=list(IMPLEMENTED),
    )

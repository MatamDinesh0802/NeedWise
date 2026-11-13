"""WorkMind AI Enterprise Edition. Commercial license: ee/LICENSE, not Apache-2.0.

The core loads this package through the "workmind.ee" entry point (workmind.core.extensions),
and only when a valid license is installed. Nothing here runs in Community.

Everything is registered in `setup`, and every registration names the licensed feature it
belongs to; the core checks that feature on every request, task run and hook call.
"""

from importlib.metadata import PackageNotFoundError, version

import structlog
from workmind.core.edition import Edition
from workmind.core.extensions import ExtensionRegistry
from workmind.modules.connectors.extensions import PERMISSION_SYNC
from workmind.modules.usage.extensions import CHARGEBACK_REPORT, DEPARTMENT_BUDGETS

from workmind_ee import audit_export, document_retention, legal_hold, permission_sync, retention
from workmind_ee.budgets import department_budgets
from workmind_ee.chargeback import chargeback_csv

# From the installed package's metadata (ee/python/pyproject.toml), like the core's version, so
# a release bumps one place.
try:
    __version__ = version("workmind-ee")
except PackageNotFoundError:  # running from a source tree without an installed package
    __version__ = "0.0.0+unknown"

log = structlog.get_logger("workmind_ee")

# The features this package implements: every one in workmind.core.edition.FEATURES
# (docs/enterprise.md).
IMPLEMENTED: tuple[str, ...] = (
    "department_budgets",
    "chargeback_reports",
    retention.FEATURE,
    audit_export.FEATURE,
    permission_sync.FEATURE,
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
    # Permission sync: source ACLs as document access lists, read during every sync, and
    # the Drive changes feed every minute for revocations (docs/permission-sync.md).
    registry.add_hook(PERMISSION_SYNC, permission_sync.permission_sync, feature="permission_sync")
    registry.add_scheduled_task(
        permission_sync.TASK_NAME,
        permission_sync.apply_drive_changes,
        every_s=permission_sync.CHANGES_EVERY_S,
        feature="permission_sync",
    )
    # Placing and releasing legal holds; the core enforces them on every deletion path.
    registry.add_router(legal_hold.router, feature="legal_hold")
    log.info(
        "workmind_ee_loaded",
        version=__version__,
        licensed=edition.features,
        implemented=list(IMPLEMENTED),
    )

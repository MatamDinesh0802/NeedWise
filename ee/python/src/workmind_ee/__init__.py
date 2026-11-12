"""WorkMind AI Enterprise Edition. Commercial license: ee/LICENSE, not Apache-2.0.

The core loads this package through the "workmind.ee" entry point (workmind.core.extensions),
and only when a valid license is installed. Nothing here runs in Community.

Everything is registered in `setup`, and every registration names the licensed feature it
belongs to; the core checks that feature on every request, task run and hook call.
"""

import structlog
from workmind.core.edition import Edition
from workmind.core.extensions import ExtensionRegistry

__version__ = "0.0.1"

log = structlog.get_logger("workmind_ee")

# The features this package implements. Empty for now: Phase 11 adds retention policies,
# department budgets, chargeback reports and advanced audit export here.
IMPLEMENTED: tuple[str, ...] = ()


def setup(registry: ExtensionRegistry, edition: Edition) -> None:
    """Called once per process at startup, with a valid license, before any request."""
    del registry  # nothing to register yet
    log.info(
        "workmind_ee_loaded",
        version=__version__,
        licensed=edition.features,
        implemented=list(IMPLEMENTED),
    )

"""Imports every ORM model module so `Base.metadata` is complete (used by Alembic)."""

from workmind.modules.governance import models as governance_models
from workmind.modules.identity import models as identity_models

__all__ = ["governance_models", "identity_models"]

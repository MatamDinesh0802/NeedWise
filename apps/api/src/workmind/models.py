"""Imports every ORM model module so `Base.metadata` is complete (used by Alembic)."""

from workmind.modules.agents import models as agent_models
from workmind.modules.analytics import models as analytics_models
from workmind.modules.authz import models as authz_models
from workmind.modules.connectors import models as connector_models
from workmind.modules.conversations import models as conversation_models
from workmind.modules.governance import models as governance_models
from workmind.modules.identity import models as identity_models
from workmind.modules.knowledge import models as knowledge_models
from workmind.modules.meetings import models as meeting_models
from workmind.modules.work import models as work_models

__all__ = [
    "agent_models",
    "analytics_models",
    "authz_models",
    "connector_models",
    "conversation_models",
    "governance_models",
    "identity_models",
    "knowledge_models",
    "meeting_models",
    "work_models",
]

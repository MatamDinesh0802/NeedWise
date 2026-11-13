"""Imports every ORM model module so `Base.metadata` is complete (used by Alembic)."""

from needwise.modules.agents import models as agent_models
from needwise.modules.analytics import models as analytics_models
from needwise.modules.authz import models as authz_models
from needwise.modules.connectors import models as connector_models
from needwise.modules.conversations import models as conversation_models
from needwise.modules.governance import models as governance_models
from needwise.modules.identity import models as identity_models
from needwise.modules.knowledge import models as knowledge_models
from needwise.modules.meetings import models as meeting_models
from needwise.modules.model_catalog import models as model_catalog_models
from needwise.modules.usage import models as usage_models
from needwise.modules.work import models as work_models

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
    "model_catalog_models",
    "usage_models",
    "work_models",
]

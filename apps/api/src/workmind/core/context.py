"""The identity that every request carries through the system (ARCHITECTURE section 6.4).

Every service function receives it explicitly. Agents, tools and retrieval act as this user,
never with more rights (principle P1).
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal

AuthMethod = Literal["session", "pat", "service"]


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    org_role: str
    # Authorization subjects: "user:<id>", "org:<id>", "dept:<id>", "group:<id>".
    principals: frozenset[str]
    auth_method: AuthMethod
    scopes: frozenset[str] = field(default_factory=frozenset[str])

    def has_role(self, *roles: str) -> bool:
        return self.org_role in roles

    def has_scope(self, scope: str) -> bool:
        """Browser sessions carry every scope ("*"); access tokens only those granted."""
        return "*" in self.scopes or scope in self.scopes

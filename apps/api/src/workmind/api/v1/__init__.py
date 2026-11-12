"""Version 1 of the public API, mounted at /api/v1."""

from fastapi import APIRouter

from workmind.api.ops import system
from workmind.api.v1 import (
    admin,
    agents,
    analytics,
    assistant,
    conversations,
    knowledge,
    me,
    meetings,
    sources,
    tokens,
    work,
)

router = APIRouter()
router.include_router(system)
router.include_router(tokens.router)  # before /me so /me/tokens matches here
router.include_router(me.router)
router.include_router(admin.router)
router.include_router(knowledge.router)
router.include_router(knowledge.admin_router)
router.include_router(assistant.router)
router.include_router(conversations.router)
router.include_router(agents.router)
router.include_router(agents.tools_router)
router.include_router(agents.members_router)
router.include_router(sources.router)
router.include_router(sources.oauth_router)
router.include_router(work.router)
router.include_router(work.admin_router)
router.include_router(meetings.router)
router.include_router(analytics.router)
router.include_router(analytics.admin_router)

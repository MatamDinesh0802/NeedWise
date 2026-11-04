"""Version 1 of the public API, mounted at /api/v1."""

from fastapi import APIRouter

from workmind.api.ops import system

router = APIRouter()
router.include_router(system)

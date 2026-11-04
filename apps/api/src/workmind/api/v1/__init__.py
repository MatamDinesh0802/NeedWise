"""Version 1 of the public API, mounted at /api/v1."""

from fastapi import APIRouter

from workmind.api.ops import system
from workmind.api.v1 import admin, me

router = APIRouter()
router.include_router(system)
router.include_router(me.router)
router.include_router(admin.router)

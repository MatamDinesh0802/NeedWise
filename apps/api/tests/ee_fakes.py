"""Stand-in Enterprise packages, loaded through real entry points in test_extensions.py."""

from fastapi import APIRouter

from needwise.core.edition import Edition
from needwise.core.extensions import ExtensionRegistry

router = APIRouter()


@router.get("/ee-test/ping")
async def ping() -> dict[str, str]:
    return {"pong": "enterprise"}


calls: list[str] = []


def good_setup(registry: ExtensionRegistry, edition: Edition) -> None:
    calls.append(f"good:{edition.name}")
    registry.add_router(router, feature="retention_policies")


def broken_setup(registry: ExtensionRegistry, edition: Edition) -> None:
    calls.append(f"broken:{edition.name}")
    registry.add_router(router, feature="retention_policies")  # registered, then a failure
    raise RuntimeError("enterprise setup bug")


not_callable = "setup"

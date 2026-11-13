"""Which edition this deployment runs (ADR 0011): Community, or Enterprise with its features."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from needwise.api.deps import CurrentContext, resources_of

router = APIRouter(tags=["edition"])


class EditionOut(BaseModel):
    edition: Literal["community", "enterprise"]
    # Licensed Enterprise features, e.g. "retention_policies". Empty in Community.
    features: list[str]
    licensee: str | None
    expires_at: datetime | None


@router.get("/edition")
async def get_edition(_ctx: CurrentContext, request: Request) -> EditionOut:
    edition = resources_of(request).edition
    license_ = edition.license
    return EditionOut(
        edition=edition.name,
        features=edition.features,
        licensee=license_.licensee if license_ else None,
        expires_at=license_.expires_at if license_ else None,
    )

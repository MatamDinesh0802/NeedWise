"""Loads the generated corpus (seed/<company>/corpus/manifest.yaml) into its collections,
through the same upload and ingestion code a person uploading in the Knowledge Hub uses."""

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from workmind.core.context import RequestContext
from workmind.core.tenancy import tenant_transaction
from workmind.modules.identity.models import User
from workmind.modules.knowledge import service
from workmind.modules.knowledge.models import Collection
from workmind.modules.knowledge.pipeline import IngestionDeps, PermanentIngestionError, ingest


@dataclass(frozen=True)
class CorpusResult:
    indexed: int
    unchanged: int
    failed: list[str]
    chunks: int


async def load_corpus(
    deps: IngestionDeps, tenant_id: uuid.UUID, corpus_dir: Path, *, uploader_email: str
) -> CorpusResult:
    manifest: dict[str, Any] = yaml.safe_load((corpus_dir / "manifest.yaml").read_text())
    async with tenant_transaction(deps.sessions, tenant_id) as session:
        uploader = (await session.scalars(select(User).where(User.email == uploader_email))).one()
        collections = {c.key: c for c in await session.scalars(select(Collection))}
    ctx = RequestContext(
        request_id="seed",
        tenant_id=tenant_id,
        user_id=uploader.id,
        org_role=uploader.org_role,
        principals=frozenset({f"user:{uploader.id}"}),
        auth_method="service",
    )
    indexed = unchanged = chunks = 0
    failed: list[str] = []
    for entry in manifest["documents"]:
        path = corpus_dir / entry["file"]
        collection = collections[Path(entry["file"]).parts[0]]
        async with tenant_transaction(deps.sessions, tenant_id) as session:
            result = await service.upload(
                session,
                ctx,
                deps.storage,
                collection,
                filename=path.name,
                declared_mime=None,
                data=path.read_bytes(),
                trust=entry.get("trust"),
                sensitivity=entry.get("sensitivity"),
                source="seed",
            )
        if result.unchanged:
            unchanged += 1
            continue
        try:
            outcome = await ingest(deps, tenant_id, result.document.id, result.version.id)
        except PermanentIngestionError as exc:
            failed.append(f"{entry['file']}: {exc}")
            continue
        indexed += 1
        chunks += outcome.chunks
    return CorpusResult(indexed=indexed, unchanged=unchanged, failed=failed, chunks=chunks)

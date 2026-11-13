"""Loads the generated corpus (seed/<company>/corpus/manifest.yaml) into its collections,
through the same upload and ingestion code a person uploading in the Knowledge Hub uses.

An entry may name `viewers` (same syntax as company.yaml: user:<email>, group:<key>,
department:<key>, department-manager:<key>): the document then gets its own access list, which
narrows its collection's (docs/permission-sync.md), as a Drive file shared with a few people.
Re-seeding applies a changed list."""

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.core.context import RequestContext
from needwise.core.tenancy import tenant_transaction
from needwise.modules.governance.audit import Actor
from needwise.modules.identity.models import Department, Group, User
from needwise.modules.knowledge import document_acl, service
from needwise.modules.knowledge.models import Collection, Document
from needwise.modules.knowledge.pipeline import IngestionDeps, PermanentIngestionError, ingest


@dataclass(frozen=True)
class CorpusResult:
    indexed: int
    unchanged: int
    failed: list[str]
    chunks: int


async def document_viewers(
    session: AsyncSession, entries: list[str] | None
) -> list[document_acl.AclEntry] | None:
    """A manifest entry's `viewers` as an own access list (None: the collection's access)."""
    if entries is None:
        return None
    principals: set[str] = set()
    for entry in entries:
        kind, _, key = entry.partition(":")
        key = key.lower()
        found: uuid.UUID | None
        if kind == "user":
            found = await session.scalar(select(User.id).where(User.email == key))
        elif kind == "group":
            found = await session.scalar(select(Group.id).where(Group.slug == key))
        elif kind in ("department", "department-manager"):
            found = await session.scalar(select(Department.id).where(Department.slug == key))
        else:
            raise ValueError(f"unknown viewer {entry!r}")
        if found is None:
            raise ValueError(f"viewer {entry!r} is not in the company")
        prefix = {"department": "dept", "department-manager": "dept-manager"}.get(kind, kind)
        principals.add(f"{prefix}:{found}")
    return [document_acl.AclEntry(p) for p in sorted(principals)]


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
            acl = await document_viewers(session, entry.get("viewers"))
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
                acl=acl,
                acl_origin="manual",
            )
            # A document seeded before: its list follows the manifest (a no-op when it does).
            document = await session.get(Document, result.document.id)
            assert document is not None  # noqa: S101
            changed = await document_acl.set_acl(
                session, tenant_id, document, acl, origin="manual", actor=Actor.system()
            )
            payload = await document_acl.payload_of(session, document)
        if result.unchanged:
            if changed:
                await document_acl.refresh_payload(
                    deps.qdrant, deps.collection_name, tenant_id, document.id, payload
                )
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

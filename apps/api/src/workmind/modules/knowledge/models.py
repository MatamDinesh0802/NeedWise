"""Knowledge: collections, documents, versions and chunks (ARCHITECTURE sections 7.2, 9, 10).

Embeddings live only in Qdrant; PostgreSQL holds the text, structure and provenance that
citations need.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key

# Synced documents reference sources: register that table whenever documents are used.
from workmind.modules.connectors import models as _sources
from workmind.modules.identity.models import PRINCIPAL_PATTERN, SLUG_PATTERN

_ = _sources  # documents reference sources: keep the table registered

SCOPES = ("personal", "department", "enterprise", "restricted")
TRUST_LEVELS = ("official", "reference", "discussion")
SENSITIVITIES = ("internal", "confidential", "restricted")
# Where a document's own access list came from (docs/permission-sync.md): its source, through
# permission sync, or an editor.
ACL_ORIGINS = ("source", "manual")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Collection(Base, TenantScoped, Timestamps):
    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "key"),
        ForeignKeyConstraint(
            ["tenant_id", "department_id"], ["departments.tenant_id", "departments.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        CheckConstraint(f"key ~ '{SLUG_PATTERN}'", name="key_format"),
        CheckConstraint(_in("scope", SCOPES), name="scope"),
        CheckConstraint(_in("default_trust", TRUST_LEVELS), name="default_trust"),
        CheckConstraint(_in("default_sensitivity", SENSITIVITIES), name="default_sensitivity"),
        CheckConstraint(
            "review_interval_days IS NULL OR review_interval_days BETWEEN 1 AND 3650",
            name="review_interval_days",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    scope: Mapped[str] = mapped_column(String(16))
    department_id: Mapped[uuid.UUID | None]
    owner_id: Mapped[uuid.UUID | None]
    default_trust: Mapped[str] = mapped_column(String(16), server_default="reference")
    default_sensitivity: Mapped[str] = mapped_column(String(16), server_default="internal")
    # Documents are due for review this many days after their last review (None: never).
    review_interval_days: Mapped[int | None] = mapped_column(Integer)


class CollectionGrant(Base, TenantScoped):
    """Who may view or edit a collection. `principal` uses the same strings as
    RequestContext.principals: user:<id>, group:<id>, dept:<id>, dept-manager:<id>, org:<id>."""

    __tablename__ = "collection_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["collections.tenant_id", "collections.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("collection_id", "principal", "relation"),
        CheckConstraint("relation IN ('viewer', 'editor')", name="relation"),
        CheckConstraint(f"principal ~ '{PRINCIPAL_PATTERN}'", name="principal_format"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    collection_id: Mapped[uuid.UUID]
    principal: Mapped[str] = mapped_column(Text)
    relation: Mapped[str] = mapped_column(String(16))


class Document(Base, TenantScoped, Timestamps):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["collections.tenant_id", "collections.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("trust", TRUST_LEVELS), name="trust"),
        CheckConstraint(_in("sensitivity", SENSITIVITIES), name="sensitivity"),
        CheckConstraint("status IN ('pending', 'processing', 'ready', 'failed')", name="status"),
        Index("ix_documents_collection_id", "tenant_id", "collection_id"),
        # A synced item is one document, whatever its title or file name becomes.
        Index(
            "uq_documents_source_item",
            "tenant_id",
            "source_id",
            "external_id",
            unique=True,
            postgresql_where=text("source_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.id"],
            ondelete="SET NULL (source_id)",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (owner_id)",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewed_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (reviewed_by)",
        ),
        # The daily review reminder looks up documents whose review date has passed.
        Index(
            "ix_documents_next_review",
            "tenant_id",
            "next_review_at",
            postgresql_where=text("next_review_at IS NOT NULL"),
        ),
        CheckConstraint(
            f"(own_acl AND {_in('acl_origin', ACL_ORIGINS)})"
            " OR (NOT own_acl AND acl_origin IS NULL)",
            name="acl_origin",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    collection_id: Mapped[uuid.UUID]
    title: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), server_default="upload")
    source_id: Mapped[uuid.UUID | None]  # set for synced documents
    external_id: Mapped[str | None] = mapped_column(Text)  # the item's id in the source
    url: Mapped[str | None] = mapped_column(Text)  # where people can open the original
    # The source's version marker (etag, revision): unchanged items are not fetched again.
    source_version: Mapped[str | None] = mapped_column(Text)
    trust: Mapped[str] = mapped_column(String(16))
    sensitivity: Mapped[str] = mapped_column(String(16))
    # True once an editor sets the label: re-indexing then keeps it instead of re-scanning.
    sensitivity_manual: Mapped[bool] = mapped_column(server_default=sa_false())
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    current_version_id: Mapped[uuid.UUID | None]
    created_by: Mapped[uuid.UUID | None]
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Governance (docs/governance.md): who answers for the content, and its review cycle.
    owner_id: Mapped[uuid.UUID | None]
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None]
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The open reminder task of the current review cycle; cleared when the document is reviewed.
    review_task_id: Mapped[uuid.UUID | None]
    # True: the document has its own access list (document_grants), which narrows the
    # collection's: only principals on it who can also view the collection see the document.
    # An own list without a mapped principal on it means nobody (ADR 0012).
    own_acl: Mapped[bool] = mapped_column(server_default=sa_false())
    acl_origin: Mapped[str | None] = mapped_column(String(16))  # "source" | "manual"
    acl_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentGrant(Base, TenantScoped):
    """One entry of a document's own access list (ADR 0012, docs/permission-sync.md).

    `principal` uses RequestContext's principal strings (user:, group:, dept:, dept-manager:,
    org:<id>). `external` is how the source named it (`email:ana@acme.test`,
    `github-team:acme/platform`): permission sync records every entry the source lists. An entry
    it could not map to anyone in WorkMind has no principal, so it grants nothing (fail closed)
    and is listed for administrators. Entries an editor sets have no `external`."""

    __tablename__ = "document_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_id", "principal", "external", postgresql_nulls_not_distinct=True
        ),
        Index("ix_document_grants_external", "tenant_id", "external"),
        CheckConstraint(
            f"principal IS NULL OR principal ~ '{PRINCIPAL_PATTERN}'", name="principal_format"
        ),
        CheckConstraint("principal IS NOT NULL OR external IS NOT NULL", name="named"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    document_id: Mapped[uuid.UUID]
    principal: Mapped[str | None] = mapped_column(Text)
    external: Mapped[str | None] = mapped_column(Text)


class DocumentVersion(Base, TenantScoped):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("document_id", "version_no"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    document_id: Mapped[uuid.UUID]
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 hex of the original bytes
    storage_key: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, server_default="0")
    embedding_model: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Chunk(Base, TenantScoped):
    """One retrievable passage. Its id is also the point id in Qdrant."""

    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("version_id", "ordinal"),
        Index("ix_chunks_document_id", "tenant_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    document_id: Mapped[uuid.UUID]
    version_id: Mapped[uuid.UUID]
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text))
    page: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)

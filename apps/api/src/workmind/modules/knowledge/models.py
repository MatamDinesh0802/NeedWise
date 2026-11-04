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
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key
from workmind.modules.identity.models import SLUG_PATTERN

SCOPES = ("personal", "department", "enterprise", "restricted")
TRUST_LEVELS = ("official", "reference", "discussion")
SENSITIVITIES = ("internal", "confidential", "restricted")


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
        CheckConstraint(
            "principal ~ '^(user|group|dept|dept-manager|org):[0-9a-f-]{36}$'",
            name="principal_format",
        ),
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
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    collection_id: Mapped[uuid.UUID]
    title: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), server_default="upload")
    trust: Mapped[str] = mapped_column(String(16))
    sensitivity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    current_version_id: Mapped[uuid.UUID | None]
    created_by: Mapped[uuid.UUID | None]
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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

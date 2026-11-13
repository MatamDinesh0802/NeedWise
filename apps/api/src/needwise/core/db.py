"""Database engine, sessions and the declarative base for ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, func, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names keep Alembic autogenerate diffs stable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Fetch server-generated values (ids, timestamps) with RETURNING on every write. Without it,
    # reading e.g. updated_at after an UPDATE would lazily query the database, which async
    # sessions cannot do implicitly.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantScoped:
    """Every tenant-owned table has this column, Row-Level Security, and composite foreign
    keys that include it (see migrations and ADR 0005)."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))


def uuid_primary_key() -> Mapped[uuid.UUID]:
    # uuidv7() is built into PostgreSQL 18: time-ordered, so inserts stay index-friendly.
    return mapped_column(primary_key=True, server_default=text("uuidv7()"))


def create_engine(url: str, *, pool_size: int = 10, max_overflow: int = 10) -> AsyncEngine:
    """`pool_size` connections stay open, up to `max_overflow` more under load (DB_POOL_SIZE,
    DB_MAX_OVERFLOW)."""
    return create_async_engine(
        url, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)

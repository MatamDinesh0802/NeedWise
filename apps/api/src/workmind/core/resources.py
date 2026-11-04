"""Long-lived clients shared by the whole process, created at startup and closed at shutdown."""

from dataclasses import dataclass
from typing import Self

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from workmind.core.config import Settings
from workmind.core.db import create_engine, create_session_factory
from workmind.modules.authz.openfga import OpenFgaClient
from workmind.modules.conversations.events import RunEvents
from workmind.modules.conversations.registry import RunRegistry
from workmind.modules.identity.oidc import OidcClient
from workmind.modules.identity.sessions import SessionStore
from workmind.modules.knowledge.dispatch import CeleryDispatcher, Dispatcher, InlineDispatcher
from workmind.modules.knowledge.pipeline import IngestionDeps
from workmind.modules.knowledge.storage import LocalObjectStorage, ObjectStorage
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.modules.retrieval.service import Retriever
from workmind.providers.embeddings import EmbeddingProvider
from workmind.providers.factory import create_embeddings, create_llm, create_local_llm
from workmind.providers.llm import LLMProvider


@dataclass
class Resources:
    engine: AsyncEngine
    db: async_sessionmaker[AsyncSession]
    valkey: Redis
    http: httpx.AsyncClient
    oidc: OidcClient
    sessions: SessionStore
    fga: OpenFgaClient
    storage: ObjectStorage
    qdrant: QdrantClient
    embeddings: EmbeddingProvider
    llm: LLMProvider
    local_llm: LLMProvider | None
    ingestion: Dispatcher
    collection_name: str
    retriever: Retriever
    runs: RunRegistry

    @classmethod
    def create(cls, settings: Settings) -> Self:
        # Every client connects lazily, so startup never blocks on infrastructure.
        engine = create_engine(settings.database_url)
        valkey = Redis.from_url(settings.valkey_url)  # pyright: ignore[reportUnknownMemberType] (untyped **kwargs upstream)
        http = httpx.AsyncClient(timeout=10)
        db = create_session_factory(engine)
        storage = LocalObjectStorage(settings.object_storage_path)
        qdrant = QdrantClient(http, settings.qdrant_url)
        embeddings = create_embeddings(settings, http)
        deps = IngestionDeps(db, storage, embeddings, qdrant, settings.qdrant_collection)
        fga = OpenFgaClient(
            http, settings.openfga_api_url, settings.openfga_api_key.get_secret_value()
        )
        return cls(
            engine=engine,
            db=db,
            valkey=valkey,
            http=http,
            oidc=OidcClient(
                http,
                discovery_url=settings.oidc_discovery_url,
                client_id=settings.oidc_client_id,
                client_secret=settings.oidc_client_secret.get_secret_value(),
                redirect_uri=settings.oidc_redirect_uri,
            ),
            sessions=SessionStore(
                valkey,
                idle_timeout_s=settings.session_idle_timeout_s,
                absolute_timeout_s=settings.session_absolute_timeout_s,
            ),
            fga=fga,
            storage=storage,
            qdrant=qdrant,
            embeddings=embeddings,
            llm=create_llm(settings, http),
            local_llm=create_local_llm(settings, http),
            ingestion=InlineDispatcher(deps)
            if settings.ingestion_mode == "inline"
            else CeleryDispatcher(),
            collection_name=settings.qdrant_collection,
            retriever=Retriever(
                qdrant=qdrant,
                embeddings=embeddings,
                fga=fga,
                sessions=db,
                collection_name=settings.qdrant_collection,
                sparse_weight=settings.retrieval_sparse_weight,
                rrf_k=settings.retrieval_rrf_k,
            ),
            runs=RunRegistry(RunEvents(valkey)),
        )

    async def aclose(self) -> None:
        await self.runs.shutdown()
        await self.http.aclose()
        await self.valkey.aclose()
        await self.engine.dispose()

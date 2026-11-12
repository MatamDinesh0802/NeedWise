"""Long-lived clients shared by the whole process, created at startup and closed at shutdown."""

from dataclasses import dataclass
from typing import Self

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from workmind.core.config import Settings
from workmind.core.db import create_engine, create_session_factory
from workmind.modules.agents.planning import FastRouter
from workmind.modules.analytics.service import Analytics
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
from workmind.modules.tools.builtin import draft_email_tool, search_knowledge_tool, web_search_tool
from workmind.modules.tools.mcp_client import RemoteServer, RemoteServerConfig, import_tools
from workmind.modules.tools.registry import ToolRegistry
from workmind.modules.work.tools import create_task_tool, send_email_tool
from workmind.providers.email import SmtpMailer
from workmind.providers.embeddings import EmbeddingProvider
from workmind.providers.factory import (
    create_embeddings,
    create_llm,
    create_local_llm,
    create_transcriber,
    secret,
)
from workmind.providers.llm import LLMProvider
from workmind.providers.transcription import Transcriber

log = structlog.get_logger("workmind.resources")


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
    agent_router: FastRouter | None
    tools: ToolRegistry
    transcriber: Transcriber
    remote_servers: list[RemoteServer]
    assistant_mode: str
    analytics: Analytics

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
        retriever = Retriever(
            qdrant=qdrant,
            embeddings=embeddings,
            fga=fga,
            sessions=db,
            collection_name=settings.qdrant_collection,
            sparse_weight=settings.retrieval_sparse_weight,
            rrf_k=settings.retrieval_rrf_k,
            min_similarity=settings.retrieval_min_similarity,
        )
        tools = ToolRegistry()
        tools.register(search_knowledge_tool(retriever))
        tools.register(draft_email_tool())
        mailer = (
            SmtpMailer(
                settings.smtp_host,
                settings.smtp_port,
                sender=settings.smtp_from,
                username=settings.smtp_username,
                password=secret(settings.smtp_password),
                starttls=settings.smtp_starttls,
            )
            if settings.smtp_host
            else None
        )
        tools.register(send_email_tool(mailer, db))
        tools.register(create_task_tool(db))
        if settings.searxng_url:
            tools.register(web_search_tool(http, settings.searxng_url))
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
            retriever=retriever,
            runs=RunRegistry(RunEvents(valkey)),
            agent_router=FastRouter(
                embeddings,
                min_score=settings.agent_fast_path_min_score,
                min_margin=settings.agent_fast_path_min_margin,
            )
            if settings.agent_fast_path
            else None,
            assistant_mode=settings.assistant_mode,
            tools=tools,
            transcriber=create_transcriber(settings, http),
            remote_servers=[
                RemoteServer(RemoteServerConfig.model_validate(c)) for c in settings.mcp_servers
            ],
            analytics=Analytics(
                db,
                settings.analytics_path,
                max_rows=settings.analytics_max_rows,
                timeout_s=settings.analytics_timeout_s,
            ),
        )

    async def load_remote_tools(self) -> None:
        """Imports every configured MCP server's tools. A server that is down is skipped (and
        logged): WorkMind starts without it rather than not at all."""
        for server in self.remote_servers:
            try:
                await import_tools(self.tools, server)
            except Exception as exc:
                log.warning(
                    "mcp_server_unavailable", server=server.config.key, error=str(exc)[:200]
                )

    async def aclose(self) -> None:
        await self.runs.shutdown()
        await self.http.aclose()
        await self.valkey.aclose()
        await self.engine.dispose()

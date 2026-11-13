"""Long-lived clients shared by the whole process, created at startup and closed at shutdown."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from workmind.core.config import Settings
from workmind.core.db import create_engine, create_session_factory
from workmind.core.edition import Edition
from workmind.core.extensions import ExtensionRegistry, load_extensions
from workmind.core.ratelimit import RateLimiter, merge_limits
from workmind.modules.agents.planning import FastRouter
from workmind.modules.analytics.engine import QueryPool
from workmind.modules.analytics.service import Analytics
from workmind.modules.analytics.slots import ValkeySlots
from workmind.modules.analytics.tools import describe_data_tool, query_data_tool
from workmind.modules.authz.openfga import OpenFgaClient
from workmind.modules.conversations.events import RunEvents
from workmind.modules.conversations.inflight import InFlight
from workmind.modules.conversations.registry import RunRegistry
from workmind.modules.identity.oidc import OidcClient
from workmind.modules.identity.sessions import SessionStore
from workmind.modules.knowledge.dispatch import CeleryDispatcher, Dispatcher, InlineDispatcher
from workmind.modules.knowledge.pipeline import IngestionDeps
from workmind.modules.knowledge.storage import LocalObjectStorage, ObjectStorage
from workmind.modules.model_catalog.resolver import LLMResolver
from workmind.modules.model_catalog.secrets import KeyRing
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.modules.retrieval.service import Retriever
from workmind.modules.tools.builtin import draft_email_tool, search_knowledge_tool, web_search_tool
from workmind.modules.tools.mcp_client import RemoteServer, RemoteServerConfig, import_tools
from workmind.modules.tools.registry import ToolRegistry
from workmind.modules.usage.meter import UsageMeter
from workmind.modules.work.tools import create_task_tool, send_email_tool
from workmind.modules.work.workflows import WorkflowDeps, start_queued
from workmind.providers.diarization import Diarizer
from workmind.providers.email import SmtpMailer
from workmind.providers.embeddings import EmbeddingProvider
from workmind.providers.factory import (
    create_diarizer,
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
    # Every model call resolves through here, per tenant (docs/models.md).
    models: LLMResolver
    ingestion: Dispatcher
    collection_name: str
    retriever: Retriever
    runs: RunRegistry
    agent_router: FastRouter | None
    tools: ToolRegistry
    transcriber: Transcriber
    diarizer: Diarizer
    remote_servers: list[RemoteServer]
    assistant_mode: str
    analytics: Analytics
    # Community or Enterprise (ADR 0011); `edition.has(feature)` gates Enterprise features.
    edition: Edition
    extensions: ExtensionRegistry
    # Usage events and budgets for every model call (docs/usage.md); also on `models.meter`.
    usage: UsageMeter
    # Request rate limits per person, tenant and address (core/ratelimit.py).
    limits: RateLimiter
    # What these were created from (Enterprise tasks that sync sources need it).
    settings: Settings | None = None
    # Outgoing email (SMTP_HOST), for send_email and notification emails; None: not configured.
    mailer: SmtpMailer | None = None
    # Slack notifications (modules/work/slack.py).
    slack_api_url: str = "https://slack.com/api"
    slack_webhook_prefix: str = "https://hooks.slack.com/"
    public_url: str = "http://localhost:8480"

    @property
    def llm(self) -> LLMProvider:
        """The deployment's own provider. Call sites resolve per tenant instead
        (`await resources.models.for_tenant(tenant_id)`), which uses this unless the tenant
        chose otherwise. Assignable: tests put a scripted model here."""
        return self.models.system

    @llm.setter
    def llm(self, value: LLMProvider) -> None:
        self.models.system = value

    @property
    def local_llm(self) -> LLMProvider | None:
        """The deployment's local model (LOCAL_LLM_*), if any."""
        return self.models.system_local

    @local_llm.setter
    def local_llm(self, value: LLMProvider | None) -> None:
        self.models.system_local = value

    @classmethod
    def create(cls, settings: Settings, *, extensions: ExtensionRegistry | None = None) -> Self:
        """`extensions`: what the process loaded at startup; loaded here when not given."""
        if extensions is None:
            extensions = load_extensions(settings)
        # Every client connects lazily, so startup never blocks on infrastructure.
        engine = create_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
        valkey = Redis.from_url(settings.valkey_url)  # pyright: ignore[reportUnknownMemberType] (untyped **kwargs upstream)
        http = httpx.AsyncClient(timeout=10)
        db = create_session_factory(engine)
        storage = LocalObjectStorage(settings.object_storage_path)
        qdrant = QdrantClient(http, settings.qdrant_url, secret(settings.qdrant_api_key))
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
        keyring = KeyRing.from_settings(settings)
        analytics = Analytics(
            db,
            settings.analytics_path,
            max_rows=settings.analytics_max_rows,
            timeout_s=settings.analytics_timeout_s,
            # PostgreSQL and MySQL sources: passwords sealed like provider keys, private hosts
            # only in the networks the operator allows, a connection budget per tenant.
            keyring=keyring,
            source_networks=settings.source_networks,
            source_connections=settings.analytics_source_connections,
            connect_timeout_s=settings.analytics_source_connect_timeout_s,
            # ANALYTICS_WORKERS is per container: each of its uvicorn processes gets a share,
            # and one person's running queries are counted in Valkey, across every process.
            pool=QueryPool(
                workers=max(1, settings.analytics_workers // settings.web_concurrency),
                memory_mb=settings.analytics_worker_memory_mb,
                per_owner=settings.analytics_queries_per_user,
                slots=ValkeySlots(valkey),
            ),
        )
        tools = ToolRegistry()
        tools.register(search_knowledge_tool(retriever))
        tools.register(draft_email_tool())
        tools.register(describe_data_tool(analytics))  # offered only to people with access
        tools.register(query_data_tool(analytics))
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
        if settings.searxng_url:
            tools.register(web_search_tool(http, settings.searxng_url))
        usage = UsageMeter(db, valkey, extensions)
        models = LLMResolver(
            db,
            settings,
            http,
            keyring,
            system=create_llm(settings, http),
            system_local=create_local_llm(settings, http),
        )
        models.meter = usage
        # A task without an assignee or department is routed, with the tenant's router model.
        tools.register(create_task_tool(db, models.for_tenant))
        resources = cls(
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
            models=models,
            ingestion=InlineDispatcher(deps)
            if settings.ingestion_mode == "inline"
            else CeleryDispatcher(),
            collection_name=settings.qdrant_collection,
            retriever=retriever,
            runs=RunRegistry(RunEvents(valkey), InFlight(valkey, settings.max_inflight_per_user)),
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
            diarizer=create_diarizer(settings),
            remote_servers=[
                RemoteServer(RemoteServerConfig.model_validate(c)) for c in settings.mcp_servers
            ],
            analytics=analytics,
            edition=extensions.edition,
            extensions=extensions,
            usage=usage,
            limits=RateLimiter(
                valkey, merge_limits(settings.rate_limits), enabled=settings.rate_limits_enabled
            ),
            settings=settings,
            mailer=mailer,
            slack_api_url=settings.slack_api_url,
            slack_webhook_prefix=settings.slack_webhook_prefix,
            public_url=settings.public_url,
        )
        if isinstance(resources.ingestion, InlineDispatcher):
            resources.ingestion.start_runs = resources.start_workflow_runs
        return resources

    async def start_workflow_runs(self, tenant_id: uuid.UUID, run_ids: Sequence[uuid.UUID]) -> None:
        """Starts runs an event queued, in the background (with the tenant's models and the tools
        of now)."""
        deps = WorkflowDeps(self.db, self.llm, self.tools, self.models)

        async def start() -> None:
            for run_id in run_ids:
                try:
                    await start_queued(deps, tenant_id, run_id)
                except Exception:  # one broken run must not keep the others from starting
                    log.exception("workflow_run_failed", run_id=str(run_id))

        self.runs.start(start())

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
        self.analytics.close()
        await self.runs.shutdown()
        await self.http.aclose()
        await self.valkey.aclose()
        await self.engine.dispose()

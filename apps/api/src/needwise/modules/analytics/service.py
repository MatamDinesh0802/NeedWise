"""The analytics service: the one interface the BI agent and the API use (ARCHITECTURE 16).

    analytics = Analytics(sessions, root=Path(settings.object_storage_path) / "analytics")
    info = await analytics.access(ctx)                # may this user query? which row policies?
    model = await analytics.describe(ctx)             # tables, metrics, dimensions (GET /model)
    result = await analytics.run_semantic(ctx, SemanticQuery(metrics=["revenue"], ...))
    result = await analytics.run_sql(ctx, "SELECT ...")
    run_id = await analytics.start_run(ctx, question)  # the agent's questions and answers
    await analytics.finish_run(ctx, run_id, status="done", answer={...})
    # admins (org owner or admin):
    await analytics.set_model(ctx, yaml_text)
    await analytics.put_table(ctx, "customers", csv_bytes, "text/csv")
    await analytics.delete_table(ctx, "customers")

Datasets: the uploaded files are the dataset "uploads" (the default everywhere), and each
PostgreSQL or MySQL source an admin connected is a dataset of its own, named by its key, with its
own semantic model, access list and row policies (sources.py, docs/analytics-sql-sources.md):

    await analytics.datasets(ctx)                       # what this person may ask about
    await analytics.run_sql(ctx, "SELECT ...", dataset="shop")
    await analytics.create_source(ctx, SourceIn(...))   # admins: connect, introspect, model
    await analytics.source_catalog(ctx, "shop")
    await analytics.set_source_model(ctx, "shop", yaml_text)

Permissions are decided here, in code, never by a model:
- Who may query: the model's `access` principals, checked against the request's principals.
- Which rows: the row policies that apply to the user become filters the SQL guard injects
  into every table reference. {user.email} is read from the users table, never the request.
- Every query that reaches the access check is audited (`analytics.query`): a hash of the SQL,
  the tables, the policies applied, the row count and the outcome. Never the rows.

Errors are AnalyticsError (an ApiError: the API returns them as problem+json as they are, and
the agent can show `detail` to the model so it can correct its query). See docs/analytics.md.
"""

import asyncio
import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlglot import exp

from needwise.core.context import RequestContext
from needwise.core.errors import ApiError
from needwise.core.tenancy import tenant_key, tenant_transaction
from needwise.modules.analytics import dataset, engine, remote, sources
from needwise.modules.analytics.compiler import (
    CompileError,
    SemanticQuery,
    compile_query,
    duplicate_keys_sql,
    orphan_keys_sql,
)
from needwise.modules.analytics.guard import (
    DIALECT,
    Dialect,
    GuardedQuery,
    QueryRejectedError,
    check,
)
from needwise.modules.analytics.models import AnalyticsModel, AnalyticsRun, AnalyticsSource
from needwise.modules.analytics.semantic import (
    GRAINS,
    Format,
    Join,
    ModelError,
    Resolved,
    RowPolicy,
    SemanticModel,
    applicable_policies,
    can_query,
    parse,
    resolve,
    row_filters,
)
from needwise.modules.analytics.sources import (
    UPLOADS,
    CatalogTable,
    SourceHostError,
    SourceIn,
    SourcePatch,
    password_context,
)
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.identity.models import User
from needwise.modules.model_catalog.secrets import KeyRing, SecretsError

SessionFactory = async_sessionmaker[AsyncSession]
DatasetKind = Literal["files", "postgres", "mysql"]
# Progress of one query, for the BI agent's live steps: ("query_compiled", {"sql", ...}), then
# ("executing", {...}).
StepCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
JOIN_CHECK_TTL_S = 300.0  # a database's data changes: its join checks are redone after this
MANAGER_ROLES = ("owner", "admin")
MAX_QUESTION_CHARS = 2000
RunStatus = Literal["running", "done", "failed"]  # models.RUN_STATUSES
NUMERIC_TYPES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
)


class AnalyticsError(ApiError):
    """A refusal or failure with a reason safe to show to the user (and to the agent's model)."""


def _not_configured(
    detail: str = "No data model has been set up for analytics yet.",
) -> AnalyticsError:
    return AnalyticsError(404, "analytics_not_configured", "Analytics is not set up", detail)


def _forbidden() -> AnalyticsError:
    return AnalyticsError(
        403,
        "analytics_forbidden",
        "No access to analytics data",
        "Your role does not give you access to the analytics data model.",
    )


def _not_manager() -> AnalyticsError:
    return AnalyticsError(
        403, "forbidden", "Only organization admins can change the analytics model"
    )


def _no_source(key: str) -> AnalyticsError:
    return AnalyticsError(404, "not_found", "Data source not found", f"No data source {key!r}.")


def _unavailable(name: str) -> AnalyticsError:
    return AnalyticsError(
        503,
        "source_unavailable",
        "The data source is unavailable",
        f"The data source {name!r} cannot be reached right now. Try again later; if it keeps "
        "failing, an admin can see why in Admin > Analytics.",
    )


# --- Results ---------------------------------------------------------------------------------


class ResultColumn(BaseModel):
    name: str
    type: str  # DuckDB type
    format: Format  # eur | number | percent | text | date: how to display the values


class QueryResult(BaseModel):
    columns: list[ResultColumn]
    rows: list[list[engine.JsonValue]]
    row_count: int  # rows returned (at most the row cap)
    truncated: bool  # the query had more rows than the cap
    elapsed_ms: int
    sql: str  # the query as checked, before row policies: what to show as "the SQL"
    # What actually ran: this user's row-policy fences (CREATE TEMP TABLE ...;), then the query
    # reading them, with the row cap.
    executed_sql: str
    tables: list[str]
    row_policies: list[str]  # names of the row policies that restricted this result


class AccessInfo(BaseModel):
    configured: bool  # a semantic model exists
    has_data: bool  # a dataset is loaded
    can_query: bool
    can_manage: bool  # org owner or admin: may change the model and the data
    # Plain-language row policies restricting this user ("" when they see every row).
    row_policies: list[str]
    restricted_tables: list[str]
    summary: str


class DatasetInfo(BaseModel):
    """A dataset this organization has: the uploaded files, or a database source."""

    key: str  # "uploads", or the source's key
    name: str  # the model's name
    kind: DatasetKind
    dialect: Dialect
    access: "AccessInfo"


class ColumnDescription(BaseModel):
    name: str
    type: str
    description: str
    format: Format | None


class TableDescription(BaseModel):
    name: str
    description: str
    time_column: str | None
    columns: list[ColumnDescription]
    # From the loaded dataset; None when the table is not loaded, or when a row policy
    # restricts it for this person (the total would tell them how many rows they cannot see).
    rows: int | None
    restricted: bool = False  # a row policy limits the rows this person sees
    source_table: str | None = None  # a database source's table (schema.table)


class FieldDescription(BaseModel):
    name: str
    description: str
    format: Format
    table: str | None = None  # metrics: the fact table
    tables: list[str] = []  # dimensions: the tables they read
    # The SQL behind it, for the agent writing its own SQL: "sum(amount_eur) WHERE status <>
    # 'void'" for a metric, "customers.region" for a dimension.
    definition: str | None = None


class ModelDescription(BaseModel):
    name: str
    description: str
    data_through: date | None
    tables: list[TableDescription]
    joins: list[str]
    dimensions: list[FieldDescription]
    metrics: list[FieldDescription]
    grains: list[str]
    access: AccessInfo
    dataset_version: str | None
    updated_at: datetime
    examples: list[str] = []  # example questions, from the model
    dataset: str = UPLOADS  # which dataset: "uploads", or a database source's key
    kind: DatasetKind = "files"
    dialect: Dialect = DIALECT  # the SQL run_sql takes for this dataset
    # Admins only:
    yaml: str | None = None
    problems: list[str] = []


class DatasetTable(BaseModel):
    name: str
    rows: int
    columns: list[dict[str, str]]  # [{"name", "type"}]


class AdminState(BaseModel):
    """What an admin manages: the model's YAML (as stored, even if it no longer validates)
    and the loaded dataset."""

    yaml: str | None
    dataset_version: str | None
    tables: list[DatasetTable]


class SourceOut(BaseModel):
    """A database source as admins see it: never the password."""

    key: str
    name: str
    kind: remote.SourceKind
    host: str
    port: int
    database: str
    username: str
    password_set: bool
    tls_mode: remote.TlsMode
    tls_ca: str | None
    schemas: list[str]
    enabled: bool
    yaml: str | None
    checked_at: datetime | None
    server_version: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class SourceCheck(BaseModel):
    ok: bool
    server_version: str | None
    elapsed_ms: int
    error: str | None


class SourceCatalog(BaseModel):
    tables: list[CatalogTable]
    truncated: bool  # more columns than the catalog lists


class RunSummary(BaseModel):
    id: uuid.UUID
    question: str
    status: RunStatus
    created_at: datetime
    # From the stored answer, when the agent stored one in this shape (agent.AnalyticsAnswer):
    outcome: str | None = None  # answered | no_access | failed
    verified: int = 0  # numbers in the narrative backed by a query result
    total: int = 0  # numbers in the narrative


class RunOut(RunSummary):
    answer: dict[str, Any]


# --- Loading ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRef:
    """A database source as one query needs it (the password still sealed)."""

    id: uuid.UUID
    key: str
    name: str
    kind: remote.SourceKind
    host: str
    port: int
    database: str
    username: str
    password: bytes | None
    tls_mode: remote.TlsMode
    tls_ca: str | None
    schemas: tuple[str, ...]
    enabled: bool

    @classmethod
    def of(cls, row: AnalyticsSource) -> "SourceRef":
        return cls(
            id=row.id,
            key=row.key,
            name=row.name,
            kind=cast(remote.SourceKind, row.kind),
            host=row.host,
            port=row.port,
            database=row.database,
            username=row.username,
            password=row.password,
            tls_mode=cast(remote.TlsMode, row.tls_mode),
            tls_ca=row.tls_ca,
            schemas=tuple(row.schemas or ()),
            enabled=row.enabled,
        )


@dataclass(frozen=True)
class LoadedModel:
    id: uuid.UUID
    yaml: str | None
    model: SemanticModel | None
    error: str | None  # the stored YAML no longer validates (e.g. after an upgrade)
    dataset_version: str | None
    dataset_tables: dict[str, Any]
    updated_at: datetime
    dataset: str = UPLOADS
    source: SourceRef | None = None  # a database source; None: the uploaded files

    def dataset_path(self, root: Path, tenant_id: uuid.UUID) -> Path | None:
        if self.source is not None or not self.dataset_version:
            return None
        return dataset.dataset_path(root, tenant_id, self.dataset_version)

    @property
    def has_data(self) -> bool:
        if self.source is not None:
            return self.source.enabled and self.source.password is not None
        return self.dataset_version is not None

    @property
    def kind(self) -> DatasetKind:
        return self.source.kind if self.source is not None else "files"


async def load_dataset(
    session: AsyncSession, tenant_id: uuid.UUID, key: str | None
) -> LoadedModel | None:
    """The uploaded files' model (key None or "uploads"), or a database source's (its key);
    None when there is no such dataset. Must run in the tenant's transaction."""
    if key is None or key == UPLOADS:
        return await load_model(session, tenant_id)
    row = await session.scalar(select(AnalyticsSource).where(AnalyticsSource.key == key))
    if row is None:
        return None
    return _source_model(row)


def _source_model(row: AnalyticsSource) -> LoadedModel:
    model, error = None, None
    if row.yaml:
        try:
            model = parse(row.yaml, cast(Dialect, row.kind))
        except ModelError as exc:
            error = str(exc)
    return LoadedModel(
        id=row.id,
        yaml=row.yaml,
        model=model,
        error=error,
        dataset_version=None,
        dataset_tables={},
        updated_at=row.updated_at,
        dataset=row.key,
        source=SourceRef.of(row),
    )


async def load_model(session: AsyncSession, tenant_id: uuid.UUID) -> LoadedModel | None:
    """The tenant's analytics model, or None. Must run in the tenant's transaction."""
    row = await session.scalar(select(AnalyticsModel).where(AnalyticsModel.tenant_id == tenant_id))
    if row is None:
        return None
    model, error = None, None
    if row.yaml:
        try:
            model = parse(row.yaml)
        except ModelError as exc:
            error = str(exc)
    return LoadedModel(
        id=row.id,
        yaml=row.yaml,
        model=model,
        error=error,
        dataset_version=row.dataset_version,
        dataset_tables=dict(row.dataset_tables or {}),
        updated_at=row.updated_at,
    )


@dataclass(frozen=True)
class _Prepared:
    loaded: LoadedModel
    model: SemanticModel
    resolved: Resolved
    email: str
    allowed: bool
    policies: list[RowPolicy] = field(default_factory=list[RowPolicy])
    filters: dict[str, exp.Expr] = field(default_factory=dict[str, exp.Expr])


# --- The service -----------------------------------------------------------------------------


class Analytics:
    def __init__(
        self,
        sessions: SessionFactory,
        root: Path,
        *,
        max_rows: int = 1000,
        timeout_s: float = engine.DEFAULT_TIMEOUT_S,
        pool: engine.QueryPool | None = None,
        keyring: KeyRing | None = None,
        source_networks: Sequence[sources.Network] = (),
        source_connections: int = 4,
        connect_timeout_s: float = 5.0,
    ) -> None:
        self.sessions = sessions
        self.root = root
        self.max_rows = max_rows
        self.timeout_s = timeout_s
        # Database sources: their passwords' keys, where they may be (private networks the
        # operator allowed), connections a tenant may have open at once, the connect timeout.
        self.keyring = keyring
        self.source_networks = tuple(source_networks)
        self.source_connections = source_connections
        self.connect_timeout_s = connect_timeout_s
        # (source, join) -> (when checked, repeated keys): a database's data changes.
        self._source_duplicates: dict[tuple[uuid.UUID, str], tuple[float, int]] = {}
        # Queries run in worker processes (memory and time limits that hold, a bounded number
        # at once and per person); a pool starts its workers on first use.
        self.pool = pool or engine.shared_pool()
        self._owns_pool = pool is not None
        # (dataset file, join) -> how many one-side key values repeat; a version never changes.
        self._duplicates: dict[tuple[str, str], int] = {}

    def close(self) -> None:
        """Stop the pool's workers, unless it is the process's shared pool."""
        if self._owns_pool:
            self.pool.close()

    # --- Reading ---

    async def access(self, ctx: RequestContext, dataset: str | None = None) -> AccessInfo:
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            loaded = await load_dataset(session, ctx.tenant_id, dataset)
            if loaded is None or loaded.model is None:
                return _access_info(ctx, loaded, None, [])
            resolved = await resolve(session, loaded.model, ctx.tenant_id)
        allowed = can_query(resolved, ctx)
        policies = applicable_policies(resolved, ctx) if allowed else []
        return _access_info(ctx, loaded, resolved, policies)

    async def datasets(self, ctx: RequestContext) -> list[DatasetInfo]:
        """Every dataset with a valid model: the uploaded files first, then the enabled
        database sources by key; each with what this person may do with it."""
        found: list[DatasetInfo] = []
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            loaded = [await load_model(session, ctx.tenant_id)]
            rows = await session.scalars(
                select(AnalyticsSource)
                .where(AnalyticsSource.enabled.is_(True))
                .order_by(AnalyticsSource.key)
            )
            loaded += [_source_model(row) for row in rows]
            for item in loaded:
                if item is None or item.model is None:
                    continue
                resolved = await resolve(session, item.model, ctx.tenant_id)
                allowed = can_query(resolved, ctx)
                policies = applicable_policies(resolved, ctx) if allowed else []
                found.append(
                    DatasetInfo(
                        key=item.dataset,
                        name=item.model.name,
                        kind=item.kind,
                        dialect=item.model.dialect,
                        access=_access_info(ctx, item, resolved, policies),
                    )
                )
        return found

    async def describe(self, ctx: RequestContext, dataset: str | None = None) -> ModelDescription:
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            loaded = await load_dataset(session, ctx.tenant_id, dataset)
            manager = ctx.has_role(*MANAGER_ROLES)
            if loaded is not None and loaded.error and manager:
                raise AnalyticsError(
                    409, "analytics_model_invalid", "The analytics model is invalid", loaded.error
                )
            if loaded is None or loaded.model is None:
                raise _not_configured()
            resolved = await resolve(session, loaded.model, ctx.tenant_id)
        allowed = can_query(resolved, ctx)
        if not (allowed or manager):
            raise _forbidden()
        policies = applicable_policies(resolved, ctx) if allowed else []
        description = _describe(loaded, loaded.model, _access_info(ctx, loaded, resolved, policies))
        if manager:
            description.yaml = loaded.yaml
            if loaded.source is None:  # a source's model is checked against it when saved
                description.problems = await self._problems(loaded, resolved, ctx.tenant_id)
        return description

    async def run_sql(
        self,
        ctx: RequestContext,
        sql: str,
        *,
        max_rows: int | None = None,
        dataset: str | None = None,
        on_step: StepCallback | None = None,
    ) -> QueryResult:
        """Run SQL written by a person or the agent, under this user's permissions, on the
        uploaded files or (`dataset`) a database source, in that dataset's SQL dialect."""
        cap = min(max_rows or self.max_rows, self.max_rows)
        return await self._run(
            ctx, kind="sql", cap=cap, sql=sql, query=None, dataset=dataset, on_step=on_step
        )

    async def run_semantic(
        self,
        ctx: RequestContext,
        query: SemanticQuery,
        *,
        dataset: str | None = None,
        on_step: StepCallback | None = None,
    ) -> QueryResult:
        """Compile a semantic query (metrics by dimensions) and run it like any other SQL."""
        cap = min(query.limit, self.max_rows)
        return await self._run(
            ctx,
            kind="semantic",
            cap=cap,
            sql=None,
            query=query,
            dataset=dataset,
            on_step=on_step,
        )

    # --- Administration (org owner or admin) ---

    async def set_model(self, ctx: RequestContext, yaml_text: str) -> ModelDescription:
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        await self.store_model(
            ctx.tenant_id, yaml_text, actor=Actor(type="user", id=ctx.user_id), user_id=ctx.user_id
        )
        return await self.describe(ctx)

    async def put_table(
        self,
        ctx: RequestContext,
        name: str,
        data: bytes,
        content_type: str,
        *,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Load (or replace) one table from CSV or XLSX. Returns its row count and columns."""
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        source = dataset.TableSource(
            name=name, data=data, content_type=content_type, filename=filename
        )
        tables = await self.replace_tables(
            ctx.tenant_id,
            put=[source],
            actor=Actor(type="user", id=ctx.user_id),
            user_id=ctx.user_id,
        )
        return tables[name]

    async def delete_table(self, ctx: RequestContext, name: str) -> None:
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        await self.replace_tables(
            ctx.tenant_id,
            drop=[name],
            actor=Actor(type="user", id=ctx.user_id),
            user_id=ctx.user_id,
        )

    # --- The BI agent's questions and answers (each person sees their own) ---

    async def start_run(
        self, ctx: RequestContext, question: str, *, created_at: datetime | None = None
    ) -> uuid.UUID:
        """Record a question before answering it. The answer is stored by finish_run.
        `created_at` lets the caller put the same time in the answer it builds."""
        text = question.strip()
        if not text or len(text) > MAX_QUESTION_CHARS:
            raise AnalyticsError(
                422, "invalid_question", f"Ask a question of 1 to {MAX_QUESTION_CHARS} characters"
            )
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            run = AnalyticsRun(tenant_id=ctx.tenant_id, user_id=ctx.user_id, question=text)
            if created_at is not None:
                run.created_at = created_at
            session.add(run)
            await session.flush()
            return run.id

    async def finish_run(
        self,
        ctx: RequestContext,
        run_id: uuid.UUID,
        *,
        status: Literal["done", "failed"],
        answer: dict[str, Any],
    ) -> None:
        """Store the answer (chart spec, narrative, SQL, data, or the error) as JSON."""
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            run = await _own_run(session, ctx, run_id)
            run.status = status
            run.answer = answer

    async def runs(
        self, ctx: RequestContext, *, limit: int = 20, finished_only: bool = False
    ) -> list[RunSummary]:
        """This user's runs, newest first, without the answers (only their outcome and
        verification counts, read from the stored JSON)."""
        answer = AnalyticsRun.answer
        query = (
            select(
                AnalyticsRun.id,
                AnalyticsRun.question,
                AnalyticsRun.status,
                AnalyticsRun.created_at,
                answer["status"].astext,
                answer[("verification", "verified")].astext,
                answer[("verification", "total")].astext,
            )
            .where(AnalyticsRun.user_id == ctx.user_id)
            .order_by(AnalyticsRun.created_at.desc(), AnalyticsRun.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        if finished_only:
            query = query.where(AnalyticsRun.status != "running")
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            rows = (await session.execute(query)).all()
        return [
            RunSummary(
                id=run_id,
                question=question,
                status=cast(RunStatus, status),
                created_at=created_at,
                outcome=outcome,
                verified=_count(verified),
                total=_count(total),
            )
            for run_id, question, status, created_at, outcome, verified, total in rows
        ]

    async def run(self, ctx: RequestContext, run_id: uuid.UUID) -> RunOut:
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            run = await _own_run(session, ctx, run_id)
            return RunOut.model_validate(run, from_attributes=True)

    async def audit_denied(self, ctx: RequestContext, kind: str) -> None:
        """Audit a refused request that never reached a query (e.g. a question to the agent
        from someone without access), like a refused query."""
        await self._audit(ctx, _Audit(kind=kind, status="denied"))

    async def admin_state(self, ctx: RequestContext) -> AdminState:
        """The stored YAML and the loaded tables, for org owners and admins. Works when no
        model is set, or when the stored one no longer validates."""
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            loaded = await load_model(session, ctx.tenant_id)
        if loaded is None:
            return AdminState(yaml=None, dataset_version=None, tables=[])
        return AdminState(
            yaml=loaded.yaml,
            dataset_version=loaded.dataset_version,
            tables=[
                DatasetTable(
                    name=name,
                    rows=_rows(loaded.dataset_tables, name) or 0,
                    columns=_columns(loaded.dataset_tables, name),
                )
                for name in sorted(loaded.dataset_tables)
            ],
        )

    # --- Without a request (seeding, admin internals) ---

    async def store_model(
        self,
        tenant_id: uuid.UUID,
        yaml_text: str,
        *,
        actor: Actor,
        user_id: uuid.UUID | None = None,
    ) -> bool:
        """Validate and store a semantic model. Returns False when it was already stored."""
        try:
            model = parse(yaml_text)
        except ModelError as exc:
            raise AnalyticsError(
                422, "invalid_model", "The semantic model is not valid", str(exc)
            ) from exc
        async with tenant_transaction(self.sessions, tenant_id) as session:
            resolved = await resolve(session, model, tenant_id)
            if resolved.unresolved:
                raise AnalyticsError(
                    422,
                    "invalid_model",
                    "The semantic model is not valid",
                    "These principals match nobody in this organization: "
                    + ", ".join(resolved.unresolved),
                )
            row = await _locked_row(session, tenant_id)
            if row.yaml == yaml_text:
                return False
            row.yaml = yaml_text
            row.updated_by = user_id
            await record_event(
                session,
                tenant_id=tenant_id,
                actor=actor,
                action="analytics.model.updated",
                resource_type="analytics_model",
                resource_id=str(row.id),
                details={
                    "name": model.name,
                    "tables": sorted(model.tables),
                    "metrics": len(model.metrics),
                    "dimensions": len(model.dimensions),
                    "row_policies": [p.name for p in model.row_policies],
                    "sha256": hashlib.sha256(yaml_text.encode()).hexdigest(),
                },
            )
        return True

    async def replace_tables(
        self,
        tenant_id: uuid.UUID,
        *,
        put: Sequence[dataset.TableSource] = (),
        drop: Sequence[str] = (),
        actor: Actor,
        user_id: uuid.UUID | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Build a new dataset version with these changes and switch to it atomically. Changes
        are serialized per tenant (the model row is locked while the new file is built)."""
        old_path: Path | None = None
        built: dataset.BuiltDataset | None = None
        try:
            async with tenant_transaction(self.sessions, tenant_id) as session:
                row = await _locked_row(session, tenant_id)
                current = dict(row.dataset_tables or {})
                missing = [name for name in drop if name not in current]
                if missing:
                    raise AnalyticsError(404, "not_found", "Table not found", ", ".join(missing))
                if row.dataset_version:
                    old_path = dataset.dataset_path(self.root, tenant_id, row.dataset_version)
                    if not old_path.is_file():
                        old_path = None  # lost: rebuild from the uploads only
                try:
                    built = await asyncio.to_thread(
                        dataset.build,
                        self.root,
                        tenant_id,
                        base=old_path,
                        base_tables=current if old_path else {},
                        put=put,
                        drop=drop,
                    )
                except dataset.DatasetError as exc:
                    raise AnalyticsError(
                        422, "invalid_table", "The table could not be loaded", str(exc)
                    ) from exc
                row.dataset_version = built.version
                row.dataset_tables = built.tables
                row.updated_by = user_id
                await record_event(
                    session,
                    tenant_id=tenant_id,
                    actor=actor,
                    action="analytics.dataset.updated",
                    resource_type="analytics_model",
                    resource_id=str(row.id),
                    details={
                        "version": built.version,
                        "loaded": {s.name: built.tables[s.name]["rows"] for s in put},
                        "dropped": list(drop),
                        "tables": sorted(built.tables),
                    },
                )
        except BaseException:
            if built is not None:  # never switched to: remove the new file
                dataset.remove(built.path)
            raise
        if old_path is not None and old_path != built.path:
            dataset.remove(old_path)  # after the switch: no new reader can pick it
        return built.tables

    # --- Database sources (org owner or admin) ---

    async def sources(self, ctx: RequestContext) -> list[SourceOut]:
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            rows = await session.scalars(select(AnalyticsSource).order_by(AnalyticsSource.key))
            return [_source_out(row) for row in rows]

    async def source(self, ctx: RequestContext, key: str) -> SourceOut:
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            return _source_out(await _source_row(session, key))

    async def create_source(self, ctx: RequestContext, body: SourceIn) -> SourceOut:
        """Store a connection (the password sealed). It is not tried here: test_source and
        source_catalog connect, set_source_model checks the model against the database."""
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        keyring = self._keyring()
        await self._check_new_host(body.host, body.port or sources.DEFAULT_PORTS[body.kind])
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            taken = await session.scalar(
                select(AnalyticsSource.id).where(AnalyticsSource.key == body.key)
            )
            if taken is not None:
                raise AnalyticsError(
                    409, "conflict", "Key already used", f"A data source {body.key!r} exists."
                )
            row = AnalyticsSource(
                tenant_id=ctx.tenant_id,
                key=body.key,
                name=body.name.strip(),
                kind=body.kind,
                host=body.host,
                port=body.port or sources.DEFAULT_PORTS[body.kind],
                database=body.database,
                username=body.username,
                tls_mode=body.tls_mode,
                tls_ca=body.tls_ca,
                schemas=list(body.schemas),
                enabled=body.enabled,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
            session.add(row)
            await session.flush()
            row.password = keyring.seal(
                body.password.get_secret_value(), context=_password_context(ctx.tenant_id, row)
            )
            await self._audit_source(session, ctx, row, "analytics.source.created")
            await session.flush()
            await session.refresh(row)
            return _source_out(row)

    async def update_source(self, ctx: RequestContext, key: str, body: SourcePatch) -> SourceOut:
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        changes = body.model_dump(exclude_unset=True)
        if "host" in changes or "port" in changes:
            row_now = await self.source(ctx, key)
            await self._check_new_host(
                changes.get("host") or row_now.host, changes.get("port") or row_now.port
            )
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            row = await _source_row(session, key, lock=True)
            place = _password_context(ctx.tenant_id, row)
            tls_before = (row.tls_mode, row.tls_ca)
            for name in ("name", "host", "port", "database", "username", "tls_mode", "enabled"):
                if changes.get(name) is not None:
                    setattr(row, name, changes[name])
            if "tls_ca" in changes:
                row.tls_ca = body.tls_ca
            if body.schemas is not None:
                row.schemas = list(body.schemas)
            # Weaker TLS (a lower mode, or another CA to trust) could send the stored password
            # to whoever answers for the host: it takes the password again, like a move.
            moved = _password_context(ctx.tenant_id, row) != place or _tls_weakened(
                tls_before, (row.tls_mode, row.tls_ca)
            )
            if body.password is not None:
                row.password = self._keyring().seal(
                    body.password.get_secret_value(),
                    context=_password_context(ctx.tenant_id, row),
                )
            elif moved and row.password is not None:
                raise AnalyticsError(
                    422,
                    "password_required",
                    "Enter the password again",
                    "The stored password only works for the host, port, database, user and "
                    "TLS settings it was entered for (a stronger TLS mode keeps it). Enter it "
                    "again with the new connection settings.",
                )
            row.updated_by = ctx.user_id
            await self._audit_source(
                session, ctx, row, "analytics.source.updated", changed=sorted(changes)
            )
            await session.flush()
            await session.refresh(row)
            return _source_out(row)

    async def delete_source(self, ctx: RequestContext, key: str) -> None:
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            row = await _source_row(session, key, lock=True)
            await self._audit_source(session, ctx, row, "analytics.source.deleted")
            await session.delete(row)

    async def test_source(self, ctx: RequestContext, key: str) -> SourceCheck:
        """Connect and ask the server's version, in the same read-only session queries get."""
        ref = await self._source_ref(ctx, key)
        started = time.perf_counter()
        try:
            result = await self._run_trusted(ctx, ref, "SELECT version()", max_rows=1)
        except AnalyticsError as exc:
            return SourceCheck(
                ok=False,
                server_version=None,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                error=exc.detail or exc.title,
            )
        version = str(result.rows[0][0])[:200] if result.rows else None
        await self._record_check(ctx.tenant_id, ref.id, version=version)
        return SourceCheck(
            ok=True,
            server_version=version,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            error=None,
        )

    async def source_catalog(self, ctx: RequestContext, key: str) -> SourceCatalog:
        """The tables and columns of the source's schemas that its user may read (the
        database's information_schema lists only those), for an admin to pick from."""
        ref = await self._source_ref(ctx, key)
        result = await self._run_trusted(
            ctx,
            ref,
            sources.catalog_sql(ref.kind, ref.schemas),
            max_rows=sources.MAX_CATALOG_COLUMNS,
        )
        await self._record_check(ctx.tenant_id, ref.id)
        return SourceCatalog(tables=sources.catalog_tables(result.rows), truncated=result.truncated)

    async def set_source_model(self, ctx: RequestContext, key: str, yaml_text: str) -> SourceOut:
        """Validate a source's semantic model (in its SQL dialect), check it against the
        database (every table in the source's schemas, every column there), and store it."""
        ref = await self._source_ref(ctx, key)
        try:
            model = parse(yaml_text, ref.kind)
        except ModelError as exc:
            raise AnalyticsError(
                422, "invalid_model", "The semantic model is not valid", str(exc)
            ) from exc
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            resolved = await resolve(session, model, ctx.tenant_id)
        problems = [f"{key} matches nobody in this organization" for key in resolved.unresolved]
        outside = sorted(
            f"{name} ({schema})"
            for name, (schema, _) in model.relations().items()
            if schema not in ref.schemas
        )
        if outside:
            problems.append(
                f"Tables outside the source's schemas ({', '.join(ref.schemas)}): "
                + ", ".join(outside)
            )
        if not problems:
            catalog = await self.source_catalog(ctx, key)
            problems = _catalog_problems(model, catalog.tables)
        if problems:
            raise AnalyticsError(
                422, "invalid_model", "The semantic model is not valid", "; ".join(problems)
            )
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            row = await _source_row(session, key, lock=True)
            if row.yaml != yaml_text:
                row.yaml = yaml_text
                row.updated_by = ctx.user_id
                await self._audit_source(
                    session,
                    ctx,
                    row,
                    "analytics.source.model.updated",
                    model={
                        "name": model.name,
                        "tables": sorted(model.tables),
                        "metrics": len(model.metrics),
                        "row_policies": [p.name for p in model.row_policies],
                        "sha256": hashlib.sha256(yaml_text.encode()).hexdigest(),
                    },
                )
            await session.flush()
            await session.refresh(row)
            return _source_out(row)

    def _keyring(self) -> KeyRing:
        if self.keyring is None:
            raise AnalyticsError(
                503,
                "secrets_unavailable",
                "No encryption key",
                "Data source passwords cannot be stored: no encryption key is configured.",
            )
        return self.keyring

    async def _check_new_host(self, host: str, port: int) -> None:
        """A host an admin enters: refused when it is, or resolves to, an address NeedWise may
        not connect to. A name that does not resolve yet is accepted (it is checked again
        before every connection)."""
        try:
            sources.check_host(host, self.source_networks)
            await sources.resolve_host(host, port, self.source_networks)
        except SourceHostError as exc:
            if "does not resolve" in str(exc):
                return
            raise AnalyticsError(
                422, "invalid_source", "This host cannot be used", f"{host}: {exc}"
            ) from exc

    async def _source_ref(self, ctx: RequestContext, key: str) -> SourceRef:
        if not ctx.has_role(*MANAGER_ROLES):
            raise _not_manager()
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            return SourceRef.of(await _source_row(session, key))

    async def _run_trusted(
        self, ctx: RequestContext, ref: SourceRef, sql: str, *, max_rows: int
    ) -> engine.EngineResult:
        """SQL NeedWise wrote (a version check, the catalog), in the same read-only session
        and limits as any query; for admins, who may see why a connection failed."""
        try:
            target = await self._target(ctx.tenant_id, ref)
            return await self.pool.execute_remote(
                target.to_json(),
                sql,
                max_rows=max_rows,
                timeout_s=self.timeout_s,
                connect_timeout_s=self.connect_timeout_s,
                owner=tenant_key(ctx.tenant_id, "analytics", "running", ctx.user_id),
                connections=(
                    tenant_key(ctx.tenant_id, "analytics", "connections"),
                    self.source_connections,
                ),
            )
        except engine.SourceUnavailableError as exc:
            await self._record_check(ctx.tenant_id, ref.id, error=exc.message)
            raise AnalyticsError(
                503, "source_unavailable", "The data source is unavailable", exc.message
            ) from exc
        except engine.QueryTimeoutError as exc:
            raise AnalyticsError(
                408, "query_timeout", "The query could not run", exc.message
            ) from exc
        except engine.QueryBusyError as exc:
            raise AnalyticsError(
                429, "analytics_busy", "Too many queries at once", exc.message
            ) from exc
        except engine.EngineError as exc:
            await self._record_check(ctx.tenant_id, ref.id, error=exc.message)
            raise AnalyticsError(
                400, "query_failed", "The query could not run", exc.message
            ) from exc

    async def _record_check(
        self,
        tenant_id: uuid.UUID,
        source_id: uuid.UUID,
        *,
        error: str | None = None,
        version: str | None = None,
    ) -> None:
        """The source's status for admins: when it was last reached, or why it was not."""
        async with tenant_transaction(self.sessions, tenant_id) as session:
            row = await session.get(AnalyticsSource, source_id)
            if row is None:
                return
            row.checked_at = datetime.now(UTC)
            row.last_error = error[:500] if error else None
            if version is not None:
                row.server_version = version

    async def _audit_source(
        self,
        session: AsyncSession,
        ctx: RequestContext,
        row: AnalyticsSource,
        action: str,
        **extra: Any,
    ) -> None:
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor=Actor(type="user", id=ctx.user_id),
            action=action,
            resource_type="analytics_source",
            resource_id=str(row.id),
            details={
                "key": row.key,
                "kind": row.kind,
                "host": row.host,
                "port": row.port,
                "database": row.database,
                "username": row.username,
                "tls_mode": row.tls_mode,
                "schemas": list(row.schemas or []),
                "enabled": row.enabled,
                **extra,
            },
        )

    # --- Internals ---

    async def _prepare(self, ctx: RequestContext, dataset: str | None = None) -> _Prepared:
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            loaded = await load_dataset(session, ctx.tenant_id, dataset)
            if loaded is None and dataset not in (None, UPLOADS):
                raise AnalyticsError(
                    404,
                    "unknown_dataset",
                    "No such dataset",
                    f"There is no dataset {dataset!r}. Use one of the datasets listed.",
                )
            if loaded is None or loaded.model is None:
                raise _not_configured()
            if loaded.source is not None and not loaded.source.enabled:
                raise _not_configured(f"The data source {loaded.source.name!r} is turned off.")
            resolved = await resolve(session, loaded.model, ctx.tenant_id)
            email = await session.scalar(select(User.email).where(User.id == ctx.user_id))
        if email is None:
            raise _forbidden()
        allowed = can_query(resolved, ctx)
        if not allowed:
            return _Prepared(loaded, loaded.model, resolved, email, allowed=False)
        return _Prepared(
            loaded,
            loaded.model,
            resolved,
            email,
            allowed=True,
            policies=applicable_policies(resolved, ctx),
            filters=row_filters(resolved, ctx, email),
        )

    async def _run(
        self,
        ctx: RequestContext,
        *,
        kind: Literal["sql", "semantic"],
        cap: int,
        sql: str | None,
        query: SemanticQuery | None,
        dataset: str | None = None,
        on_step: StepCallback | None = None,
    ) -> QueryResult:
        prepared = await self._prepare(ctx, dataset)
        audit = _Audit(kind=kind, dataset=prepared.loaded.dataset)
        if not prepared.allowed:
            audit.status = "denied"
            await self._audit(ctx, audit)
            raise _forbidden()

        formats: dict[str, Format] = {}
        if query is not None:
            try:
                compiled = compile_query(prepared.model, query)
                await self._check_joins(ctx, prepared, compiled.joins)
            except CompileError as exc:
                audit.status, audit.reason = "invalid", str(exc)[:300]
                await self._audit(ctx, audit)
                raise AnalyticsError(
                    400, "invalid_query", "The query does not fit the data model", str(exc)
                ) from exc
            sql = compiled.sql
            formats = {c.name: c.format for c in compiled.columns}
        if sql is None:
            raise ValueError("run needs sql or a semantic query")
        audit.sql_sha256 = hashlib.sha256(sql.encode()).hexdigest()

        try:
            guarded = check(
                sql,
                tables=prepared.model.tables.keys(),
                row_filters=prepared.filters,
                max_rows=cap,
                dialect=prepared.model.dialect,
                relations=prepared.model.relations() if prepared.loaded.source else None,
            )
        except QueryRejectedError as exc:
            audit.status, audit.reason = "rejected", exc.reason[:300]
            await self._audit(ctx, audit)
            raise AnalyticsError(
                400, "query_rejected", "The query was refused", exc.reason
            ) from exc
        audit.tables = list(guarded.tables)
        audit.row_policies = [
            p.name for p in prepared.policies if set(p.filters) & set(guarded.filtered)
        ]
        if on_step is not None:
            step = {"dataset": prepared.loaded.dataset, "dialect": prepared.model.dialect}
            await on_step("query_compiled", {**step, "sql": guarded.query})
            await on_step("executing", step)

        try:
            result = await self._execute(ctx, prepared, guarded, cap)
        except engine.EngineError as exc:
            audit.status = "timeout" if isinstance(exc, engine.QueryTimeoutError) else "failed"
            audit.reason = type(exc).__name__
            await self._audit(ctx, audit)
            if isinstance(exc, engine.DatasetMissingError):
                raise _not_configured(exc.message) from exc
            if isinstance(exc, engine.SourceUnavailableError):
                source = prepared.loaded.source
                if source is not None:
                    await self._record_check(ctx.tenant_id, source.id, error=exc.message)
                raise _unavailable(source.name if source else "uploads") from exc
            if isinstance(exc, engine.QueryBusyError):
                raise AnalyticsError(
                    429, "analytics_busy", "Too many queries at once", exc.message
                ) from exc
            status, code = (
                (408, "query_timeout")
                if isinstance(exc, engine.QueryTimeoutError)
                else (400, "query_failed")
            )
            raise AnalyticsError(status, code, "The query could not run", exc.message) from exc

        audit.status = "ok"
        audit.row_count, audit.truncated, audit.elapsed_ms = (
            len(result.rows),
            result.truncated,
            result.elapsed_ms,
        )
        audit.executed_sha256 = hashlib.sha256(guarded.statements.encode()).hexdigest()
        await self._audit(ctx, audit)
        return QueryResult(
            columns=[
                ResultColumn(
                    name=c.name,
                    type=c.type,
                    format=formats.get(c.name) or _format(prepared.model, c.name, c.type),
                )
                for c in result.columns
            ],
            rows=result.rows,
            row_count=len(result.rows),
            truncated=result.truncated,
            elapsed_ms=result.elapsed_ms,
            sql=guarded.query,
            executed_sql=guarded.statements,
            tables=list(guarded.tables),
            row_policies=audit.row_policies,
        )

    async def _check_joins(
        self, ctx: RequestContext, prepared: _Prepared, joins: Sequence[Join]
    ) -> None:
        """Refuse a semantic query whose many-to-one join is not many-to-one in the loaded data:
        a repeated key on the one side would count fact rows twice (silently: the numbers would
        still be in the results, so verification would back them)."""
        if prepared.loaded.source is not None:
            await self._check_source_joins(ctx, prepared, joins)
            return
        path = prepared.loaded.dataset_path(self.root, ctx.tenant_id)
        if path is None:
            return  # nothing loaded: the query itself reports it
        for join in joins:
            key = (str(path), join.right)
            if key not in self._duplicates:
                try:
                    result = await self.pool.execute(
                        path, duplicate_keys_sql(join), max_rows=1, timeout_s=self.timeout_s
                    )
                except engine.EngineError:
                    return  # the query itself will report a missing table or column
                count = result.rows[0][0] if result.rows else 0
                if len(self._duplicates) > 1000:
                    self._duplicates.clear()
                self._duplicates[key] = count if isinstance(count, int) else 0
            if self._duplicates[key]:
                raise CompileError(
                    f"The join {join.left} = {join.right} is declared many-to-one, but "
                    f"{join.right} repeats {self._duplicates[key]} time(s) in the loaded data, "
                    "so this breakdown would count rows twice. An admin needs to fix the "
                    f"{join.right.split('.')[0]} table."
                )

    async def _check_source_joins(
        self, ctx: RequestContext, prepared: _Prepared, joins: Sequence[Join]
    ) -> None:
        """_check_joins on a database: the same count, through the guard (no row policies:
        whether a key repeats is about the table, and the person sees only the count), redone
        after JOIN_CHECK_TTL_S since the data changes."""
        source, model = prepared.loaded.source, prepared.model
        if source is None:
            return
        now = time.monotonic()
        for join in joins:
            key = (source.id, join.right)
            cached = self._source_duplicates.get(key)
            if cached is None or now - cached[0] > JOIN_CHECK_TTL_S:
                try:
                    guarded = check(
                        duplicate_keys_sql(join, model.dialect),
                        tables=model.tables.keys(),
                        max_rows=1,
                        dialect=model.dialect,
                        relations=model.relations(),
                    )
                    result = await self._execute_source(ctx, source, guarded, 1)
                except (QueryRejectedError, engine.EngineError, AnalyticsError):
                    return  # the query itself will report what is wrong
                count = result.rows[0][0] if result.rows else 0
                if len(self._source_duplicates) > 1000:
                    self._source_duplicates.clear()
                cached = (now, int(count) if isinstance(count, int | float) else 0)
                self._source_duplicates[key] = cached
            if cached[1]:
                raise CompileError(
                    f"The join {join.left} = {join.right} is declared many-to-one, but "
                    f"{join.right} repeats {cached[1]} time(s) in the database, so this "
                    "breakdown would count rows twice. An admin needs to fix the model or the "
                    f"{join.right.split('.')[0]} table."
                )

    async def _target(self, tenant_id: uuid.UUID, source: SourceRef) -> remote.Target:
        """Where to connect, checked now, with the password opened. Raises
        SourceUnavailableError with the reason (for admins)."""
        if source.password is None:
            raise engine.SourceUnavailableError("No password is set for this source.")
        if self.keyring is None:
            raise engine.SourceUnavailableError("No encryption key is configured.")
        try:
            addresses = await sources.resolve_host(source.host, source.port, self.source_networks)
        except SourceHostError as exc:
            raise engine.SourceUnavailableError(f"{source.host}: {exc}") from exc
        context = password_context(
            tenant_id,
            source.id,
            source.kind,
            source.host,
            source.port,
            source.database,
            source.username,
        )
        try:
            password = self.keyring.open(source.password, context=context)
        except SecretsError as exc:
            raise engine.SourceUnavailableError(
                f"The stored password cannot be used ({exc}); enter it again."
            ) from exc
        return remote.Target(
            kind=source.kind,
            # verify-full checks the certificate against the name: connect by name. Otherwise
            # connect to the address just checked (a name that resolves elsewhere later, DNS
            # rebinding, is not followed).
            address=source.host if source.tls_mode == "verify-full" else addresses[0],
            host=source.host,
            port=source.port,
            database=source.database,
            user=source.username,
            password=password,
            tls_mode=source.tls_mode,
            tls_ca=source.tls_ca,
            connect_timeout_s=self.connect_timeout_s,
        )

    async def _execute_source(
        self,
        ctx: RequestContext,
        source: SourceRef,
        guarded: GuardedQuery,
        cap: int,
        *,
        sql: str | None = None,
    ) -> engine.EngineResult:
        target = await self._target(ctx.tenant_id, source)
        return await self.pool.execute_remote(
            target.to_json(),
            sql or guarded.sql,
            max_rows=cap,
            timeout_s=self.timeout_s,
            connect_timeout_s=self.connect_timeout_s,
            owner=tenant_key(ctx.tenant_id, "analytics", "running", ctx.user_id),
            connections=(
                tenant_key(ctx.tenant_id, "analytics", "connections"),
                self.source_connections,
            ),
        )

    async def _execute(
        self, ctx: RequestContext, prepared: _Prepared, guarded: GuardedQuery, cap: int
    ) -> engine.EngineResult:
        if prepared.loaded.source is not None:
            return await self._execute_source(ctx, prepared.loaded.source, guarded, cap)
        path = prepared.loaded.dataset_path(self.root, ctx.tenant_id)
        if path is None:
            raise engine.DatasetMissingError("No data has been loaded for analytics yet.")
        fences = [(f.name, f.sql) for f in guarded.fences]
        # One person's running queries, counted across every API process (a Valkey key).
        owner = tenant_key(ctx.tenant_id, "analytics", "running", ctx.user_id)
        try:
            return await self.pool.execute(
                path,
                guarded.sql,
                max_rows=cap,
                timeout_s=self.timeout_s,
                fences=fences,
                owner=owner,
            )
        except engine.DatasetMissingError:
            # The dataset was switched to a new version (and the old file removed) between
            # reading the model and opening the file: open the current version once more.
            async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
                fresh = await load_model(session, ctx.tenant_id)
            path = fresh.dataset_path(self.root, ctx.tenant_id) if fresh else None
            if (
                path is None
                or fresh is None
                or fresh.dataset_version == prepared.loaded.dataset_version
            ):
                raise
            return await self.pool.execute(
                path,
                guarded.sql,
                max_rows=cap,
                timeout_s=self.timeout_s,
                fences=fences,
                owner=owner,
            )

    async def _audit(self, ctx: RequestContext, audit: "_Audit") -> None:
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            await record_event(
                session,
                tenant_id=ctx.tenant_id,
                actor=Actor(type="user", id=ctx.user_id),
                action="analytics.query",
                resource_type="analytics_model",
                details=audit.details(),
            )

    async def _problems(
        self, loaded: LoadedModel, resolved: Resolved, tenant_id: uuid.UUID
    ) -> list[str]:
        """What an admin should fix: principals that match nobody, model tables or columns
        that the loaded data lacks, and joins whose keys break many-to-one (repeated keys on the
        one side, keys on the many side that match nothing)."""
        problems = [f"{key} matches nobody in this organization" for key in resolved.unresolved]
        model = loaded.model
        if model is None:
            return problems
        if not loaded.dataset_version:
            return [*problems, "No data is loaded yet."]
        for name, table in model.tables.items():
            info = loaded.dataset_tables.get(name)
            if info is None:
                problems.append(f"Table {name} is in the model but not loaded.")
                continue
            loaded_columns = {c["name"] for c in info.get("columns", [])}
            missing = [c for c in table.columns if c not in loaded_columns]
            if missing:
                problems.append(f"Table {name} lacks the columns {', '.join(missing)}.")
        path = loaded.dataset_path(self.root, tenant_id)
        if problems or path is None:
            return problems
        for join in model.joins:
            try:
                duplicates, orphans = [
                    (await self.pool.execute(path, sql, max_rows=1, timeout_s=self.timeout_s)).rows[
                        0
                    ][0]
                    for sql in (duplicate_keys_sql(join), orphan_keys_sql(join))
                ]
            except engine.EngineError as exc:
                problems.append(f"The join {join.left} = {join.right} cannot run: {exc.message}")
                continue
            if duplicates:
                problems.append(
                    f"{join.right} repeats {duplicates} time(s): the join {join.left} = "
                    f"{join.right} counts those rows twice, so breakdowns by it are refused."
                )
            if orphans:
                problems.append(
                    f"{orphans} row(s) of {join.left.split('.')[0]} have a {join.left} that "
                    f"{join.right.split('.')[0]} lacks: they show as an empty group."
                )
        return problems


@dataclass
class _Audit:
    kind: str
    dataset: str = UPLOADS
    status: str = ""
    reason: str | None = None
    sql_sha256: str | None = None
    executed_sha256: str | None = None
    tables: list[str] = field(default_factory=list[str])
    row_policies: list[str] = field(default_factory=list[str])
    row_count: int | None = None
    truncated: bool | None = None
    elapsed_ms: int | None = None

    def details(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "kind": self.kind,
            "dataset": self.dataset,
            "status": self.status,
            "sql_sha256": self.sql_sha256,
            "executed_sha256": self.executed_sha256,
            "tables": self.tables,
            "row_policies": self.row_policies,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_ms": self.elapsed_ms,
            "reason": self.reason,
        }
        return {k: v for k, v in details.items() if v is not None}


async def _source_row(session: AsyncSession, key: str, *, lock: bool = False) -> AnalyticsSource:
    query = select(AnalyticsSource).where(AnalyticsSource.key == key)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise _no_source(key)
    return row


TLS_STRENGTH: dict[str, int] = {"disable": 0, "require": 1, "verify-full": 2}


def _tls_weakened(before: tuple[str, str | None], after: tuple[str, str | None]) -> bool:
    """A lower TLS mode, or a different CA to trust (which certificates pass verify-full)."""
    (mode_before, ca_before), (mode_after, ca_after) = before, after
    if TLS_STRENGTH.get(mode_after, 0) < TLS_STRENGTH.get(mode_before, 0):
        return True
    return ca_after != ca_before and mode_after != "disable"


def _password_context(tenant_id: uuid.UUID, row: AnalyticsSource) -> bytes:
    return password_context(
        tenant_id, row.id, row.kind, row.host, row.port, row.database, row.username
    )


def _source_out(row: AnalyticsSource) -> SourceOut:
    return SourceOut(
        key=row.key,
        name=row.name,
        kind=cast(remote.SourceKind, row.kind),
        host=row.host,
        port=row.port,
        database=row.database,
        username=row.username,
        password_set=row.password is not None,
        tls_mode=cast(remote.TlsMode, row.tls_mode),
        tls_ca=row.tls_ca,
        schemas=list(row.schemas or []),
        enabled=row.enabled,
        yaml=row.yaml,
        checked_at=row.checked_at,
        server_version=row.server_version,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _catalog_problems(model: SemanticModel, catalog: Sequence[CatalogTable]) -> list[str]:
    """Model tables and columns the database does not have (or the source's user cannot see)."""
    found = {(t.schema_name, t.name): {c.name for c in t.columns} for t in catalog}
    problems: list[str] = []
    for name, table in model.tables.items():
        location = table.location
        if location is None:
            continue
        columns = found.get(location)
        if columns is None:
            problems.append(
                f"Table {name}: {table.source_table} is not in the database, or its user may "
                "not read it"
            )
            continue
        missing = [c for c in table.columns if c not in columns]
        if missing:
            problems.append(
                f"Table {name}: {table.source_table} has no column {', '.join(missing)}"
            )
    return problems


async def _own_run(session: AsyncSession, ctx: RequestContext, run_id: uuid.UUID) -> AnalyticsRun:
    run = await session.get(AnalyticsRun, run_id)
    if run is None or run.user_id != ctx.user_id:  # someone else's run: as if it did not exist
        raise AnalyticsError(404, "not_found", "Run not found")
    return run


async def _locked_row(session: AsyncSession, tenant_id: uuid.UUID) -> AnalyticsModel:
    """The tenant's model row, created if needed, locked until the transaction ends."""
    await session.execute(
        insert(AnalyticsModel)
        .values(tenant_id=tenant_id)
        .on_conflict_do_nothing(index_elements=["tenant_id"])
    )
    row = await session.scalar(
        select(AnalyticsModel)
        .where(AnalyticsModel.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:  # inserted just above, in this transaction
        raise RuntimeError("analytics model row missing")
    return row


def _access_info(
    ctx: RequestContext,
    loaded: LoadedModel | None,
    resolved: Resolved | None,
    policies: list[RowPolicy],
) -> AccessInfo:
    configured = loaded is not None and loaded.model is not None
    has_data = loaded is not None and loaded.has_data
    allowed = resolved is not None and can_query(resolved, ctx)
    restricted = sorted({t for p in policies for t in p.filters})
    texts = [f"{p.name}: {p.description.strip()}" for p in policies]
    if not configured:
        summary = "Analytics is not set up for this organization."
    elif not allowed:
        summary = "You do not have access to the analytics data."
    elif texts:
        summary = "You see only some rows. " + " ".join(texts)
    else:
        summary = "You see all rows of the analytics data."
    return AccessInfo(
        configured=configured,
        has_data=has_data,
        can_query=allowed,
        can_manage=ctx.has_role(*MANAGER_ROLES),
        row_policies=texts,
        restricted_tables=restricted,
        summary=summary,
    )


def _describe(loaded: LoadedModel, model: SemanticModel, access: AccessInfo) -> ModelDescription:
    restricted = set(access.restricted_tables)
    tables = [
        TableDescription(
            name=name,
            description=table.description.strip(),
            time_column=table.time_column,
            columns=[
                ColumnDescription(
                    name=column_name,
                    type=column.type,
                    description=column.description.strip(),
                    format=column.format,
                )
                for column_name, column in table.columns.items()
            ],
            rows=None if name in restricted else _rows(loaded.dataset_tables, name),
            restricted=name in restricted,
            source_table=table.source_table,
        )
        for name, table in model.tables.items()
    ]
    return ModelDescription(
        name=model.name,
        description=model.description.strip(),
        data_through=model.data_through,
        tables=tables,
        joins=[f"{j.left} = {j.right}" for j in model.joins],
        dimensions=[
            FieldDescription(
                name=name,
                description=d.description.strip(),
                format=d.format,
                tables=sorted(model.dimension_tables(name)),
                definition=d.sql.strip(),
            )
            for name, d in model.dimensions.items()
        ],
        metrics=[
            FieldDescription(
                name=name,
                description=m.description.strip(),
                format=m.format,
                table=m.table,
                definition=" ".join(m.sql.split())
                + (f" WHERE {' '.join(m.filter.split())}" if m.filter else ""),
            )
            for name, m in model.metrics.items()
        ],
        grains=list(GRAINS),
        access=access,
        dataset_version=loaded.dataset_version,
        updated_at=loaded.updated_at,
        examples=list(model.examples),
        dataset=loaded.dataset,
        kind=loaded.kind,
        dialect=model.dialect,
    )


def _rows(tables: dict[str, Any], name: str) -> int | None:
    info: Any = tables.get(name)
    if not isinstance(info, dict):
        return None
    rows = cast(dict[str, Any], info).get("rows")
    return rows if isinstance(rows, int) else None


def _columns(tables: dict[str, Any], name: str) -> list[dict[str, str]]:
    info: Any = tables.get(name)
    if not isinstance(info, dict):
        return []
    columns: Any = cast(dict[str, Any], info).get("columns")
    if not isinstance(columns, list):
        return []
    return [
        {"name": str(column.get("name", "")), "type": str(column.get("type", ""))}
        for column in cast(list[Any], columns)
        if isinstance(column, dict)
        for column in [cast(dict[str, Any], column)]
    ]


def _count(value: str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except ValueError:
        return 0


def _format(model: SemanticModel, name: str, duckdb_type: str) -> Format:
    """Display format of a column of a raw SQL result: the model's, if the name is one of its
    metrics, dimensions or columns; otherwise from the type."""
    if name in model.metrics:
        return model.metrics[name].format
    if name in model.dimensions:
        return model.dimensions[name].format
    for table in model.tables.values():
        column = table.columns.get(name)
        if column is not None and column.format:
            return column.format
    base = duckdb_type.split("(")[0].upper()
    if base in ("DATE",) or base.startswith("TIMESTAMP"):
        return "date"
    if base in NUMERIC_TYPES:
        return "number"
    return "text"

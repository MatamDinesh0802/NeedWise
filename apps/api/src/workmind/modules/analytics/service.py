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
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlglot import exp

from workmind.core.context import RequestContext
from workmind.core.errors import ApiError
from workmind.core.tenancy import tenant_transaction
from workmind.modules.analytics import dataset, engine
from workmind.modules.analytics.compiler import (
    CompileError,
    SemanticQuery,
    compile_query,
    duplicate_keys_sql,
    orphan_keys_sql,
)
from workmind.modules.analytics.guard import GuardedQuery, QueryRejectedError, check
from workmind.modules.analytics.models import AnalyticsModel, AnalyticsRun
from workmind.modules.analytics.semantic import (
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
from workmind.modules.governance.audit import Actor, record_event
from workmind.modules.identity.models import User

SessionFactory = async_sessionmaker[AsyncSession]
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
class LoadedModel:
    id: uuid.UUID
    yaml: str | None
    model: SemanticModel | None
    error: str | None  # the stored YAML no longer validates (e.g. after an upgrade)
    dataset_version: str | None
    dataset_tables: dict[str, Any]
    updated_at: datetime

    def dataset_path(self, root: Path, tenant_id: uuid.UUID) -> Path | None:
        if not self.dataset_version:
            return None
        return dataset.dataset_path(root, tenant_id, self.dataset_version)


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
    ) -> None:
        self.sessions = sessions
        self.root = root
        self.max_rows = max_rows
        self.timeout_s = timeout_s
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

    async def access(self, ctx: RequestContext) -> AccessInfo:
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            loaded = await load_model(session, ctx.tenant_id)
            if loaded is None or loaded.model is None:
                return _access_info(ctx, loaded, None, [])
            resolved = await resolve(session, loaded.model, ctx.tenant_id)
        allowed = can_query(resolved, ctx)
        policies = applicable_policies(resolved, ctx) if allowed else []
        return _access_info(ctx, loaded, resolved, policies)

    async def describe(self, ctx: RequestContext) -> ModelDescription:
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            loaded = await load_model(session, ctx.tenant_id)
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
            description.problems = await self._problems(loaded, resolved, ctx.tenant_id)
        return description

    async def run_sql(
        self, ctx: RequestContext, sql: str, *, max_rows: int | None = None
    ) -> QueryResult:
        """Run SQL written by a person or the agent, under this user's permissions."""
        cap = min(max_rows or self.max_rows, self.max_rows)
        return await self._run(ctx, kind="sql", cap=cap, sql=sql, query=None)

    async def run_semantic(self, ctx: RequestContext, query: SemanticQuery) -> QueryResult:
        """Compile a semantic query (metrics by dimensions) and run it like any other SQL."""
        cap = min(query.limit, self.max_rows)
        return await self._run(ctx, kind="semantic", cap=cap, sql=None, query=query)

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

    # --- Internals ---

    async def _prepare(self, ctx: RequestContext) -> _Prepared:
        async with tenant_transaction(self.sessions, ctx.tenant_id) as session:
            loaded = await load_model(session, ctx.tenant_id)
            if loaded is None or loaded.model is None:
                raise _not_configured()
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
    ) -> QueryResult:
        prepared = await self._prepare(ctx)
        audit = _Audit(kind=kind)
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

        try:
            result = await self._execute(ctx, prepared, guarded, cap)
        except engine.EngineError as exc:
            audit.status = "timeout" if isinstance(exc, engine.QueryTimeoutError) else "failed"
            audit.reason = type(exc).__name__
            await self._audit(ctx, audit)
            if isinstance(exc, engine.DatasetMissingError):
                raise _not_configured(exc.message) from exc
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

    async def _execute(
        self, ctx: RequestContext, prepared: _Prepared, guarded: GuardedQuery, cap: int
    ) -> engine.EngineResult:
        path = prepared.loaded.dataset_path(self.root, ctx.tenant_id)
        if path is None:
            raise engine.DatasetMissingError("No data has been loaded for analytics yet.")
        fences = [(f.name, f.sql) for f in guarded.fences]
        owner = f"{ctx.tenant_id}:{ctx.user_id}"
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
    has_data = loaded is not None and loaded.dataset_version is not None
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

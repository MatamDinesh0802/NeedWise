"""Shared helpers for the analytics tests: the Acme semantic model, row filters for a person,
and the Acme dataset built from seed/acme/analytics (no database needed)."""

import csv
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlglot import exp

from workmind.core.context import RequestContext
from workmind.modules.analytics import dataset, engine, guard, semantic, service
from workmind.modules.analytics.compiler import CompileError, SemanticQuery, compile_query
from workmind.modules.analytics.service import (
    AccessInfo,
    AnalyticsError,
    ModelDescription,
    QueryResult,
    ResultColumn,
)

ACME_ANALYTICS = Path(__file__).resolve().parents[3] / "seed" / "acme" / "analytics"
DAN = "dan@acme.test"
DIEGO = "diego.alvarez@acme.test"
SALES, SALES_MANAGER, FINANCE, LEADERSHIP = (
    "dept:sales",
    "dept-manager:sales",
    "dept:finance",
    "group:leadership",
)


def acme_model() -> semantic.SemanticModel:
    return semantic.parse((ACME_ANALYTICS / "semantic.yaml").read_text())


def resolved(model: semantic.SemanticModel | None = None) -> semantic.Resolved:
    """The Acme model with its principal keys resolved to fixed test principals."""
    model = model or acme_model()
    keys = {
        "department:finance": FINANCE,
        "department:sales": SALES,
        "department-manager:sales": SALES_MANAGER,
        "group:leadership": LEADERSHIP,
    }
    return semantic.Resolved(
        model=model,
        access=frozenset(keys[k] for k in model.access),
        policies=tuple(
            semantic.ResolvedPolicy(
                policy=p,
                applies=frozenset(keys[k] for k in p.applies_to),
                unless=frozenset(keys[k] for k in p.unless),
            )
            for p in model.row_policies
        ),
        unresolved=(),
    )


def context(*principals: str, user_id: uuid.UUID | None = None) -> RequestContext:
    return RequestContext(
        request_id="test",
        tenant_id=uuid.UUID(int=1),
        user_id=user_id or uuid.uuid4(),
        org_role="member",
        principals=frozenset(principals),
        auth_method="session",
    )


def dan_filters() -> dict[str, exp.Expr]:
    return semantic.row_filters(resolved(), context(SALES), DAN)


def csv_rows(table: str) -> list[dict[str, str]]:
    with (ACME_ANALYTICS / "data" / f"{table}.csv").open(newline="") as f:
        return list(csv.DictReader(f))


def dan_customer_ids() -> set[str]:
    return {r["customer_id"] for r in csv_rows("customers") if r["account_owner"] == DAN}


def build_acme(root: Path) -> Path:
    sources = [
        dataset.TableSource(name=p.stem, data=p.read_bytes(), content_type="text/csv")
        for p in sorted((ACME_ANALYTICS / "data").glob("*.csv"))
    ]
    return dataset.build(root, uuid.UUID(int=1), base=None, put=sources).path


def run(
    path: Path,
    sql: str,
    filters: dict[str, exp.Expr] | None = None,
    *,
    max_rows: int = 1000,
) -> engine.EngineResult:
    """The guard, then the engine: what the service does for a query."""
    model = acme_model()
    checked = guard.check(sql, tables=model.tables.keys(), row_filters=filters, max_rows=max_rows)
    return engine.execute_sync(path, checked.sql, max_rows=max_rows, fences=fences(checked))


def fences(checked: guard.GuardedQuery) -> list[tuple[str, str]]:
    return [(f.name, f.sql) for f in checked.fences]


DAN_POLICY = (
    "Own accounts only: Account executives see only the customers they own, those customers' "
    "invoice lines, and the deals they own."
)


class LocalAnalytics:
    """The analytics service's query path (compiler, guard, engine) on the Acme dataset file,
    without a database: access is set by flags, runs are kept in memory. For unit tests of the
    agent and the tools; the real service is tested against PostgreSQL."""

    def __init__(
        self,
        path: Path,
        *,
        can_query: bool = True,
        as_dan: bool = False,
        configured: bool = True,
        max_rows: int = 1000,
    ) -> None:
        self.path = path
        self.model = acme_model()
        self.can_query = can_query
        self.as_dan = as_dan
        self.configured = configured
        self.max_rows = max_rows
        self.runs: dict[uuid.UUID, dict[str, Any]] = {}
        self.denied: list[str] = []
        self.executed: list[str] = []  # the SQL that ran, row policies included

    async def access(self, ctx: RequestContext) -> AccessInfo:
        policies = [DAN_POLICY] if self.as_dan and self.can_query else []
        summary = (
            "You do not have access to the analytics data."
            if not self.can_query
            else ("You see only some rows. " + " ".join(policies))
            if policies
            else "You see all rows of the analytics data."
        )
        return AccessInfo(
            configured=self.configured,
            has_data=self.configured,
            can_query=self.configured and self.can_query,
            can_manage=False,
            row_policies=policies,
            restricted_tables=sorted(dan_filters()) if policies else [],
            summary=summary,
        )

    async def describe(self, ctx: RequestContext) -> ModelDescription:
        loaded = service.LoadedModel(
            id=uuid.uuid4(),
            yaml=None,
            model=self.model,
            error=None,
            dataset_version="v1",
            dataset_tables={t: {"rows": len(csv_rows(t))} for t in self.model.tables},
            updated_at=datetime.now(UTC),
        )
        return service._describe(loaded, self.model, await self.access(ctx))

    async def run_sql(
        self, ctx: RequestContext, sql: str, *, max_rows: int | None = None
    ) -> QueryResult:
        return await self._run(sql, {}, min(max_rows or self.max_rows, self.max_rows))

    async def run_semantic(self, ctx: RequestContext, query: SemanticQuery) -> QueryResult:
        if not self.can_query:
            raise service._forbidden()
        try:
            compiled = compile_query(self.model, query)
        except CompileError as exc:
            raise AnalyticsError(
                400, "invalid_query", "The query does not fit the data model", str(exc)
            ) from exc
        formats = {c.name: c.format for c in compiled.columns}
        return await self._run(compiled.sql, formats, min(query.limit, self.max_rows))

    async def _run(self, sql: str, formats: dict[str, Any], cap: int) -> QueryResult:
        if not self.can_query:
            raise service._forbidden()
        filters = dan_filters() if self.as_dan else {}
        try:
            checked = guard.check(
                sql, tables=self.model.tables.keys(), row_filters=filters, max_rows=cap
            )
        except guard.QueryRejectedError as exc:
            raise AnalyticsError(
                400, "query_rejected", "The query was refused", exc.reason
            ) from exc
        try:
            result = await engine.execute(
                self.path, checked.sql, max_rows=cap, fences=fences(checked)
            )
        except engine.EngineError as exc:
            raise AnalyticsError(
                400, "query_failed", "The query could not run", exc.message
            ) from exc
        self.executed.append(checked.statements)
        return QueryResult(
            columns=[
                ResultColumn(
                    name=c.name,
                    type=c.type,
                    format=formats.get(c.name) or service._format(self.model, c.name, c.type),
                )
                for c in result.columns
            ],
            rows=result.rows,
            row_count=len(result.rows),
            truncated=result.truncated,
            elapsed_ms=result.elapsed_ms,
            sql=checked.query,
            executed_sql=checked.statements,
            tables=list(checked.tables),
            row_policies=["Own accounts only"] if checked.filtered else [],
        )

    async def start_run(
        self, ctx: RequestContext, question: str, *, created_at: datetime | None = None
    ) -> uuid.UUID:
        run_id = uuid.uuid4()
        self.runs[run_id] = {"question": question.strip(), "status": "running", "answer": {}}
        return run_id

    async def finish_run(
        self,
        ctx: RequestContext,
        run_id: uuid.UUID,
        *,
        status: Literal["done", "failed"],
        answer: dict[str, Any],
    ) -> None:
        self.runs[run_id].update(status=status, answer=answer)

    async def audit_denied(self, ctx: RequestContext, kind: str) -> None:
        self.denied.append(kind)

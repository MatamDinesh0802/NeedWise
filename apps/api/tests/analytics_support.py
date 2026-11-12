"""Shared helpers for the analytics tests: the Acme semantic model, row filters for a person,
and the Acme dataset built from seed/acme/analytics (no database needed)."""

import csv
import uuid
from pathlib import Path

from sqlglot import exp

from workmind.core.context import RequestContext
from workmind.modules.analytics import dataset, engine, guard, semantic

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
    return engine.execute_sync(path, checked.sql, max_rows=max_rows)

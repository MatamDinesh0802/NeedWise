"""The analytics eval on a seeded Acme tenant, against PostgreSQL, with the real identities: the
dataset is checked without a model (gold SQL through the service as each item's user, access as
the items expect, attack probes refused), then the whole suite runs through Evaluator with a
scripted model that answers with the gold SQL."""

import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import func, select

from tests.analytics_support import ACME_ANALYTICS
from tests.eval_analytics_support import DATASETS, gold_oracle
from tests.integration.conftest import SessionFactory
from workmind.core.resources import Resources
from workmind.core.tenancy import tenant_transaction
from workmind.evals.analytics import Table, match, text
from workmind.evals.datasets import load_analytics
from workmind.evals.runner import Askers, EvalRun, Evaluator
from workmind.modules.analytics.models import AnalyticsRun
from workmind.modules.analytics.service import Analytics, AnalyticsError
from workmind.modules.governance.models import AuditEvent
from workmind.seed.analytics_load import seed_analytics
from workmind.seed.apply import seed_database
from workmind.seed.company import load_company

ITEMS = load_analytics(DATASETS)


async def acme_evaluator(
    owner_sessions: SessionFactory, app_sessions: SessionFactory, root: Path
) -> tuple[Evaluator, Analytics, uuid.UUID]:
    spec = load_company(ACME_ANALYTICS.parent / "company.yaml")
    slug = f"acme-{uuid.uuid4().hex[:8]}"
    company = spec.company.model_copy(update={"slug": slug})
    seeded = await seed_database(spec.model_copy(update={"company": company}), owner_sessions, {})
    assert await seed_analytics(owner_sessions, seeded.tenant_id, ACME_ANALYTICS, root)
    analytics = Analytics(app_sessions, root)
    # What Evaluator uses of Resources: the database, the model and the analytics service.
    resources = cast(
        Resources, SimpleNamespace(db=app_sessions, llm=gold_oracle(ITEMS), analytics=analytics)
    )
    evaluator = Evaluator(resources, Askers(resources, slug), concurrency=4)
    return evaluator, analytics, seeded.tenant_id


async def test_the_dataset_holds_on_the_seeded_tenant(
    owner_sessions: SessionFactory, app_sessions: SessionFactory, tmp_path: Path
) -> None:
    evaluator, analytics, _ = await acme_evaluator(owner_sessions, app_sessions, tmp_path)
    people = {i.user for i in ITEMS} | {i.differs_from for i in ITEMS if i.differs_from}
    contexts = {p: await evaluator.askers.context(p) for p in people}
    access = {p: await analytics.access(ctx) for p, ctx in contexts.items()}

    for item in ITEMS:
        ctx = contexts[item.user]
        if item.expect == "no_access":
            assert not access[item.user].can_query, item.id
            with pytest.raises(AnalyticsError) as refused:
                await analytics.run_sql(ctx, "SELECT count(*) FROM customers")
            assert refused.value.code == "analytics_forbidden"
            continue
        assert access[item.user].can_query, item.id
        if item.probe_sql:
            with pytest.raises(AnalyticsError) as rejected:
                await analytics.run_sql(ctx, item.probe_sql)
            assert rejected.value.code == "query_rejected", item.id
        if item.gold_sql is None:
            continue
        gold = Table.from_result(await analytics.run_sql(ctx, item.gold_sql))
        assert match(item, gold, gold).ok, item.id
        if item.differs_from:
            other = await analytics.run_sql(contexts[item.differs_from], item.gold_sql)
            assert other.rows, item.id
            assert gold.rows != other.rows, item.id
        else:
            assert gold.rows, item.id

    # Dan's row policy, as the real principals resolve it; nobody else in the dataset has one.
    assert {p for p, info in access.items() if info.row_policies} == {"dan"}
    hidden_sql = next(i.hidden_sql for i in ITEMS if i.hidden_sql)
    hidden = await analytics.run_sql(contexts["carol"], hidden_sql)
    dan_sees = await analytics.run_sql(contexts["dan"], hidden_sql)
    assert len(hidden.rows) == 290  # 11 customers, 12 deals, their ids, invoice lines
    assert dan_sees.rows == []
    assert "bluefin freight" in {text(r[0]) for r in hidden.rows}


async def test_the_suite_with_a_perfect_agent_passes_and_leaks_nothing(
    owner_sessions: SessionFactory, app_sessions: SessionFactory, tmp_path: Path
) -> None:
    evaluator, _, tenant_id = await acme_evaluator(owner_sessions, app_sessions, tmp_path)

    done = await evaluator.analytics(
        ITEMS, today=date(2026, 11, 12), reference_users=sorted({i.user for i in ITEMS})
    )

    assert done.problems == []
    assert done.reference == "carol"  # the first, alphabetically, who sees every row
    assert [r.id for r in done.records if not r.passed] == []
    assert EvalRun(analytics=done.records, analytics_problems=done.problems).leaks == []
    refused = [r for r in done.records if r.expect == "no_access"]
    assert {(r.status, r.model_calls) for r in refused} == {("no_access", 0)}
    assert sum(r.rejections for r in done.records) == sum(bool(i.probe_sql) for i in ITEMS)
    # Every refused question and every query was audited; no answer was stored.
    async with tenant_transaction(owner_sessions, tenant_id) as session:
        actions = list(
            await session.scalars(
                select(AuditEvent.action).where(AuditEvent.action == "analytics.query")
            )
        )
        stored = await session.scalar(select(func.count()).select_from(AnalyticsRun))
    assert len(actions) >= len(ITEMS)
    assert stored == 0

# The fixtures come from test_analytics_api; tests take them as parameters by name.
# ruff: noqa: F811
"""Usage events from the BI agent (/analytics/ask) on the seeded Acme data: one event per model
call, for the asker, pointing at the stored answer. Needs TEST_VALKEY_URL."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.integration.conftest import Acme, SessionFactory
from tests.integration.test_analytics_api import (  # noqa: F401  (pytest fixtures)
    acme,
    api,
    ask,
    pytestmark,
    use_model,
)
from workmind.core.tenancy import tenant_transaction
from workmind.modules.analytics.agent import Composed
from workmind.modules.analytics.models import AnalyticsRun
from workmind.modules.usage.models import UsageEvent


async def test_a_bi_answer_records_its_tool_loop_and_its_composition(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    use_model(
        api,
        Composed(narrative="Revenue was €38.9M [Q1].", chart=None, main_query="Q1"),
        turns=[[("query_metrics", {"metrics": ["revenue"], "purpose": "Total revenue"})]],
    )

    answer = ask(api, acme, "carol", "Revenue this year?")

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        recorded = (
            await session.scalars(select(UsageEvent).order_by(UsageEvent.occurred_at))
        ).all()
        run = await session.get(AnalyticsRun, uuid.UUID(answer["id"]))
    assert run is not None
    assert [(e.feature, e.ref_type, e.ref_id) for e in recorded] == [
        ("analytics", "analytics_run", answer["id"]),  # the tool loop
        ("analytics", "analytics_run", answer["id"]),  # composing the answer
    ]
    assert {e.user_id for e in recorded} == {acme.users["carol"]}
    assert sum(e.input_tokens for e in recorded) == answer["usage"]["input_tokens"]

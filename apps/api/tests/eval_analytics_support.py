"""Helpers for testing the analytics eval without a model: a scripted model that answers each
question with its gold SQL, and the Acme people on the local analytics backend (no database)."""

import html
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from needwise.core.context import RequestContext
from needwise.evals.datasets import AnalyticsItem
from needwise.modules.analytics.agent import Composed
from needwise.modules.analytics.compiler import SemanticQuery
from needwise.modules.analytics.service import (
    AccessInfo,
    DatasetInfo,
    ModelDescription,
    QueryResult,
    StepCallback,
)
from needwise.providers.llm import LLMRequest
from tests.analytics_support import (
    FINANCE,
    LEADERSHIP,
    SALES,
    SALES_MANAGER,
    LocalAnalytics,
    context,
)
from tests.fakes import FakeLLM, ToolTurn

DATASETS = Path(__file__).resolve().parents[3] / "evals" / "datasets"
NO_ACCESS = frozenset({"bob", "mallory", "erin"})  # engineer, guest, org admin
PRINCIPALS: Mapping[str, tuple[str, ...]] = {
    "dan": (SALES,),
    "diego.alvarez": (SALES, SALES_MANAGER, LEADERSHIP),
    "carol": (FINANCE,),
    "hannah.schmidt": (FINANCE, LEADERSHIP),
    "priya.raman": (LEADERSHIP,),
}
_QUESTION = re.compile(r"<question>(.*?)</question>", re.DOTALL)


def asked(request: LLMRequest) -> str:
    """The question a tool-loop or compose request is about."""
    content = request.messages[0].content
    found = _QUESTION.search(content)
    return html.unescape(found[1]) if found else content


def scripted(
    sql_for: Callable[[AnalyticsItem], str | None], items: Sequence[AnalyticsItem]
) -> FakeLLM:
    """A model that runs one SQL query per question (`sql_for`), then names it the main query.
    The model does not know who asks: a question asked by several people gets the SQL of the
    first of them that has one (people without access never reach the model)."""
    sql_by_question: dict[str, str | None] = {}
    dataset_of: dict[str, str | None] = {}
    for item in items:
        if not sql_by_question.get(item.question):
            sql_by_question[item.question] = sql_for(item)
            dataset_of[item.question] = item.dataset

    def turns(request: LLMRequest) -> list[ToolTurn]:
        question = asked(request)
        sql = sql_by_question[question]
        arguments: dict[str, object] = {"sql": sql, "purpose": "the answer"}
        if dataset_of.get(question):
            arguments["dataset"] = dataset_of[question]
        return [[("run_sql", arguments)]] if sql else []

    def compose(request: LLMRequest) -> Composed:
        return Composed(narrative="The answer is in [Q1].", chart=None, main_query="Q1")

    return FakeLLM(tool_script=turns, structured_script=compose)


def gold_oracle(items: Sequence[AnalyticsItem]) -> FakeLLM:
    """A perfect agent: the gold SQL for each question, and each attack's probe SQL."""
    return scripted(lambda item: item.gold_sql or item.probe_sql, items)


class People:
    """The Acme people on LocalAnalytics: Dan with his row policy; Bob, Mallory and Erin without
    access; everyone else sees every row. Dispatches on the asker."""

    def __init__(self, path: Path, *, dan: LocalAnalytics | None = None) -> None:
        self.path = path
        self.dan = dan
        self.contexts: dict[str, RequestContext] = {}
        self.backends: dict[uuid.UUID, LocalAnalytics] = {}

    async def context(self, handle: str) -> RequestContext:
        if handle not in self.contexts:
            ctx = context(*PRINCIPALS.get(handle, ()))
            if handle == "dan":
                backend = self.dan or LocalAnalytics(self.path, as_dan=True)
            else:
                backend = LocalAnalytics(self.path, can_query=handle not in NO_ACCESS)
            self.contexts[handle] = ctx
            self.backends[ctx.user_id] = backend
        return self.contexts[handle]

    def _of(self, ctx: RequestContext) -> LocalAnalytics:
        return self.backends[ctx.user_id]

    async def access(self, ctx: RequestContext, dataset: str | None = None) -> AccessInfo:
        return await self._of(ctx).access(ctx)

    async def datasets(self, ctx: RequestContext) -> list[DatasetInfo]:
        return await self._of(ctx).datasets(ctx)

    async def describe(self, ctx: RequestContext, dataset: str | None = None) -> ModelDescription:
        return await self._of(ctx).describe(ctx)

    async def run_sql(
        self,
        ctx: RequestContext,
        sql: str,
        *,
        max_rows: int | None = None,
        dataset: str | None = None,
        on_step: StepCallback | None = None,
    ) -> QueryResult:
        return await self._of(ctx).run_sql(ctx, sql, max_rows=max_rows, on_step=on_step)

    async def run_semantic(
        self,
        ctx: RequestContext,
        query: SemanticQuery,
        *,
        dataset: str | None = None,
        on_step: StepCallback | None = None,
    ) -> QueryResult:
        return await self._of(ctx).run_semantic(ctx, query, on_step=on_step)

    async def start_run(
        self, ctx: RequestContext, question: str, *, created_at: datetime | None = None
    ) -> uuid.UUID:
        return await self._of(ctx).start_run(ctx, question, created_at=created_at)

    async def finish_run(
        self,
        ctx: RequestContext,
        run_id: uuid.UUID,
        *,
        status: Literal["done", "failed"],
        answer: dict[str, Any],
    ) -> None:
        await self._of(ctx).finish_run(ctx, run_id, status=status, answer=answer)

    async def audit_denied(self, ctx: RequestContext, kind: str) -> None:
        await self._of(ctx).audit_denied(ctx, kind)

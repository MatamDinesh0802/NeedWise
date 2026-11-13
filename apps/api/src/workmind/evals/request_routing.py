"""The request routing suite (`wm eval --suites request_routing`, docs/automation.md).

Each request in evals/datasets/request_routing.jsonl goes through the same decision the product
makes (modules/work/routing.py: the tenant's rules, then its router model at its threshold),
and is scored against the queue it belongs in:

    correct      the expected department, or triage for a request a person should route
    wrong queue  a department other than the expected one: the costly error (the request
                 sits with people who don't handle it); triage instead is only slower
    model errors router model calls that failed (an API error, an answer outside the schema):
                 those items went to triage because the model was unavailable, so a triage
                 they count as correct says nothing about the model
    rules        the items the seeded rules must decide alone, and that they never decide the
                 others (no model needed: `--retrieval-only` runs only this part)

Nothing is created: no task, no notification, no audit record.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from workmind.evals.datasets import RequestRoutingItem
from workmind.modules.work.policy import RoutingPolicy
from workmind.modules.work.routing import DepartmentView, RuleView, decide_route
from workmind.providers.llm import LLMProvider


@dataclass
class RequestRoutingRecord:
    id: str
    expected: str
    via: str
    method: str  # rule | model | triage
    routed_to: str  # a department key, or "triage"
    confidence: float | None
    correct: bool
    wrong_queue: bool
    rules_as_expected: bool  # rule items: the rules routed it; others: the rules did not
    model_failed: bool  # the router model was asked and its call failed
    latency_ms: int
    explanation: str


async def evaluate_request_routing(
    items: Sequence[RequestRoutingItem],
    *,
    departments: Sequence[DepartmentView],
    rules: Sequence[RuleView],
    policy: RoutingPolicy,
    llm: LLMProvider | None,
    organization: str,
) -> list[RequestRoutingRecord]:
    """`llm` None: rules only (every item the rules leave goes to triage)."""
    records: list[RequestRoutingRecord] = []
    effective = policy if llm is not None else policy.model_copy(update={"use_model": False})
    for item in items:
        started = time.perf_counter()
        decision = await decide_route(
            title=item.title,
            description=item.description,
            category=item.category,
            departments=departments,
            rules=rules,
            policy=effective,
            llm=llm,
            organization=organization,
        )
        routed = decision.department.key if decision.department else "triage"
        by_rules = decision.method == "rule"
        records.append(
            RequestRoutingRecord(
                id=item.id,
                expected=item.expected,
                via=item.via,
                method=decision.method,
                routed_to=routed,
                confidence=decision.confidence,
                correct=routed == item.expected,
                wrong_queue=routed not in ("triage", item.expected),
                rules_as_expected=by_rules == (item.via == "rule")
                and (not by_rules or routed == item.expected),
                model_failed=decision.model_failed,
                latency_ms=round((time.perf_counter() - started) * 1000),
                explanation=decision.explanation,
            )
        )
    return records


def summarize_request_routing(records: Sequence[RequestRoutingRecord]) -> dict[str, Any]:
    def share(rows: Sequence[RequestRoutingRecord]) -> str:
        return f"{sum(r.correct for r in rows)}/{len(rows)}"

    return {
        "items": len(records),
        "correct": sum(r.correct for r in records),
        "wrong_queue": sum(r.wrong_queue for r in records),
        "triaged": sum(r.routed_to == "triage" for r in records),
        "rules_as_expected": sum(r.rules_as_expected for r in records),
        "model_errors": sum(r.model_failed for r in records),
        "by_via": {
            via: share([r for r in records if r.via == via]) for via in ("rule", "model", "triage")
        },
    }


def request_routing_section(records: Sequence[RequestRoutingRecord]) -> list[str]:
    s = summarize_request_routing(records)
    out = [
        "",
        "## Request routing (department queues)",
        "",
        f"Correct queue: **{s['correct']}/{s['items']}** (rule items {s['by_via']['rule']}, "
        f"model items {s['by_via']['model']}, triage items {s['by_via']['triage']}). "
        f"Sent to a wrong department: **{s['wrong_queue']}**. Sent to triage: {s['triaged']}. "
        f"Rules behaved as the dataset expects on {s['rules_as_expected']}/{s['items']}. "
        f"Router model calls that failed: **{s['model_errors']}**"
        + (
            " (those items went to triage because the model was unavailable: the model and "
            "triage scores above do not measure the model)."
            if s["model_errors"]
            else "."
        ),
        "",
        "| Item | Expected | Routed | By | Confidence |",
        "|---|---|---|---|---|",
    ]
    for r in records:
        mark = "" if r.correct else " ✗"
        mark += " (model error)" if r.model_failed else ""
        confidence = f"{r.confidence:.2f}" if r.confidence is not None else "-"
        out.append(f"| {r.id} | {r.expected} | {r.routed_to}{mark} | {r.method} | {confidence} |")
    return out

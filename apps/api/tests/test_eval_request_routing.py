"""The request routing eval set and suite, without a model or a database: the dataset is well
formed, the seeded rules (seed/acme/company.yaml) route exactly the items marked "rule", and
the scoring counts what it says. The suite with a real model is run by `wm eval`."""

import uuid
from collections import Counter
from pathlib import Path

from tests.fakes import FakeLLM
from workmind.evals.datasets import load_request_routing
from workmind.evals.request_routing import (
    evaluate_request_routing,
    request_routing_section,
    summarize_request_routing,
)
from workmind.modules.work.policy import RoutingPolicy
from workmind.modules.work.routing import DepartmentView, RouteChoice, RuleView
from workmind.providers.llm import LLMRequest
from workmind.seed.company import load_company

REPO = Path(__file__).resolve().parents[3]
DATASETS = REPO / "evals" / "datasets"
ITEMS = load_request_routing(DATASETS)


def seeded() -> tuple[list[DepartmentView], list[RuleView]]:
    spec = load_company(REPO / "seed" / "acme" / "company.yaml")
    departments = [DepartmentView(uuid.uuid4(), d.key, d.name) for d in spec.departments]
    by_key = {d.key: d for d in departments}
    rules = [
        RuleView(
            uuid.uuid4(),
            by_key[r.department],
            tuple(k.lower() for k in r.keywords),
            tuple(r.categories),
        )
        for r in spec.request_routing
    ]
    return departments, rules


def test_the_dataset_is_large_and_varied_enough() -> None:
    departments, rules = seeded()
    keys = {d.key for d in departments}
    assert len(ITEMS) >= 30
    assert len({i.id for i in ITEMS}) == len(ITEMS)
    assert all(i.expected in keys or i.expected == "triage" for i in ITEMS)
    via = Counter(i.via for i in ITEMS)
    assert via["rule"] >= 15
    assert via["model"] >= 8
    assert via["triage"] >= 4
    assert {i.source for i in ITEMS} == {"form", "email", "assistant"}
    routed = {r.department.key for r in rules}
    assert all(sum(i.expected == key for i in ITEMS) >= 2 for key in routed)


async def test_the_seeded_rules_decide_exactly_the_rule_items() -> None:
    departments, rules = seeded()
    records = await evaluate_request_routing(
        ITEMS,
        departments=departments,
        rules=rules,
        policy=RoutingPolicy(),
        llm=None,
        organization="Acme Robotics",
    )
    off = [(r.id, r.method, r.routed_to) for r in records if not r.rules_as_expected]
    assert off == []
    assert all(r.correct for r in records if r.via == "rule")
    assert all(r.routed_to == "triage" for r in records if r.via != "rule")
    assert summarize_request_routing(records)["wrong_queue"] == 0


async def test_scoring_with_a_scripted_router() -> None:
    departments, rules = seeded()
    expected = {i.title: i.expected for i in ITEMS}

    def answer(request: LLMRequest) -> RouteChoice:
        title = request.messages[0].content.split("Title: ", 1)[1].split("\n", 1)[0]
        wanted = expected[title.replace("&lt;", "<").replace("&gt;", ">")]
        if title.startswith("Wi-Fi"):
            return RouteChoice(department="engineering", confidence=0.9)  # a wrong queue
        if wanted == "triage":
            return RouteChoice(department="none", confidence=0.2)
        return RouteChoice(department=wanted, confidence=0.85)

    records = await evaluate_request_routing(
        ITEMS,
        departments=departments,
        rules=rules,
        policy=RoutingPolicy(),
        llm=FakeLLM(structured_script=answer),
        organization="Acme Robotics",
    )
    summary = summarize_request_routing(records)
    assert (summary["items"], summary["correct"], summary["wrong_queue"]) == (
        len(ITEMS),
        len(ITEMS) - 1,
        1,
    )
    assert summary["triaged"] == sum(i.via == "triage" for i in ITEMS)
    section = "\n".join(request_routing_section(records))
    assert "Sent to a wrong department: **1**" in section
    assert "| engineering ✗ | model | 0.90 |" in section


async def test_failed_router_calls_are_counted_not_scored_as_caution() -> None:
    # Every router call failed (a schema the provider refused) and the report still read
    # "wrong department 0, triage items 5/5", like a cautious router.
    departments, rules = seeded()
    records = await evaluate_request_routing(
        ITEMS,
        departments=departments,
        rules=rules,
        policy=RoutingPolicy(),
        llm=FakeLLM(),  # nothing scripted: every structured call fails
        organization="Acme Robotics",
    )
    summary = summarize_request_routing(records)
    asked = sum(r.via != "rule" for r in records)
    assert summary["model_errors"] == asked > 0
    section = "\n".join(request_routing_section(records))
    assert f"Router model calls that failed: **{asked}**" in section
    assert "(model error)" in section

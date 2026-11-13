"""Markdown and JSON reports for an evaluation run."""

import json
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from needwise.evals.analytics import AnalyticsRecord, summarize
from needwise.evals.request_routing import request_routing_section
from needwise.evals.runner import (
    AnswerRecord,
    EvalRun,
    RetrievalRecord,
    RoutingRecord,
    percentile,
    routing_by_agent,
    summarize_retrieval,
)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _rate(rows: list[AnswerRecord], predicate: Callable[[AnswerRecord], object]) -> str:
    return f"{sum(bool(predicate(r)) for r in rows)}/{len(rows)}" if rows else "-"


def _correct(record: AnswerRecord) -> bool:
    return not record.facts_missing and not record.error


def _latency_row(name: str, values: list[float]) -> str:
    p50, p95 = percentile(values, 0.5) / 1000, percentile(values, 0.95) / 1000
    return f"| {name} | {p50:.1f} s | {p95:.1f} s |"


def retrieval_table(records: list[RetrievalRecord], modes: list[str]) -> list[str]:
    lines = [
        "| Mode | Doc Recall@1 | Doc Recall@5 | Doc MRR "
        "| Evidence@1 | Evidence@3 | Evidence@5 | Evidence MRR | p50 | p95 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for mode in modes:
        s = summarize_retrieval(records, mode)
        if not s:
            continue
        cells = [
            _pct(s["recall@1"]),
            _pct(s["recall@5"]),
            f"{s['mrr']:.3f}",
            _pct(s["evidence@1"]),
            _pct(s["evidence@3"]),
            _pct(s["evidence@5"]),
            f"{s['evidence_mrr']:.3f}",
            f"{s['p50_ms']:.0f} ms",
            f"{s['p95_ms']:.0f} ms",
        ]
        lines.append(f"| {mode} | " + " | ".join(cells) + " |")
    return lines


def by_category(records: list[RetrievalRecord], mode: str) -> list[str]:
    groups: dict[str, list[RetrievalRecord]] = defaultdict(list)
    for r in records:
        if r.mode == mode:
            groups[r.category].append(r)
    lines = ["| Category | Questions | Doc Recall@5 | Evidence@3 |", "|---|---|---|---|"]
    for category, rows in sorted(groups.items()):
        hit = sum(r.rank is not None and r.rank <= 5 for r in rows)
        evidence = sum(r.evidence_rank is not None and r.evidence_rank <= 3 for r in rows)
        lines.append(f"| {category} | {len(rows)} | {hit}/{len(rows)} | {evidence}/{len(rows)} |")
    return lines


def suite_latency_table(answered: list[AnswerRecord]) -> list[str]:
    """The same figures per suite: the totals above mix questions answered from documents with
    red-team attacks (some through research or analytics) and abstentions."""
    suites = sorted({a.suite for a in answered})
    if len(suites) < 2:
        return []
    out = [
        "",
        "By suite:",
        "",
        "| Suite | Answers | Time to first token p50 / p95 | Complete answer p50 / p95 "
        "| Mean cost |",
        "|---|---|---|---|---|",
    ]
    for suite in suites:
        rows = [a for a in answered if a.suite == suite]
        ttft = [a.ttft_ms for a in rows]
        total = [a.total_ms for a in rows]
        costs = [c for a in rows if (c := a.cost) is not None]
        out.append(
            f"| {suite} | {len(rows)} "
            f"| {percentile(ttft, 0.5) / 1000:.1f} / {percentile(ttft, 0.95) / 1000:.1f} s "
            f"| {percentile(total, 0.5) / 1000:.1f} / {percentile(total, 0.95) / 1000:.1f} s "
            f"| {f'${statistics.fmean(costs):.4f}' if costs else '-'} |"
        )
    return out


def routing_section(records: list[RoutingRecord]) -> list[str]:
    ran = [r for r in records if r.skipped is None]
    skipped = [r for r in records if r.skipped is not None]
    right = [r for r in ran if r.correct]
    fast = [r for r in ran if r.routed_by == "fast_path"]
    fast_right = [r for r in fast if r.correct]
    planned = [r.latency_ms for r in ran if r.routed_by != "fast_path"]
    # Over every routed question, fast-path decisions (milliseconds) included; the planner's
    # own latency is over the questions it decided.
    out = [
        "",
        "## Routing (planner and fast path)",
        "",
        f"Exact agent set: **{len(right)}/{len(ran)}**. Fast path used for "
        f"{len(fast)} questions, {len(fast_right)} of them correct. Routing latency p50 "
        f"{percentile([r.latency_ms for r in ran], 0.5) / 1000:.1f} s over every question"
        + (
            f"; the planner ({len(planned)} calls) p50 {percentile(planned, 0.5) / 1000:.1f} s, "
            f"p95 {percentile(planned, 0.95) / 1000:.1f} s."
            if planned
            else "."
        ),
    ]
    if skipped:
        reasons = sorted({r.skipped or "" for r in skipped})
        out += [
            "",
            f"Skipped {len(skipped)} question(s) whose expected agent does not exist in this "
            f"tenant: {'; '.join(reasons)}. They are not counted above.",
        ]
    out += ["", "| Expected | Questions | Correct |", "|---|---|---|"]
    for mode in ("single", "fan_out", "direct"):
        rows = [r for r in ran if r.expected_mode == mode]
        if rows:
            out.append(f"| {mode} | {len(rows)} | {sum(r.correct for r in rows)}/{len(rows)} |")
    by_agent = routing_by_agent(records)
    if by_agent:
        out += [
            "",
            "### By agent",
            "",
            "Expected in: questions whose expected plan includes the agent. Exact: of those, "
            "the plan had exactly the expected agents. Picked: of those, the plan included the "
            "agent. Picked when not expected: false positives over every question that ran.",
            "",
            "| Agent | Expected in | Exact | Picked | Picked when not expected |",
            "|---|---|---|---|---|",
        ]
        for key, s in by_agent.items():
            n = s["expected"]
            out.append(
                f"| {key} | {n} | {s['exact']}/{n} | {s['picked']}/{n} | {s['false_positives']} |"
            )
    near = [r for r in ran if r.not_agents]
    if near:
        kept = [r for r in near if not set(r.agents) & set(r.not_agents)]
        out += [
            "",
            f"Near misses kept away from the agent they sound like: **{len(kept)}/{len(near)}**.",
        ]
    unscored = [r for r in ran if r.not_agents_missing and not r.not_agents]
    if unscored:
        missing = sorted({k for r in unscored for k in r.not_agents_missing})
        out += [
            "",
            f"{len(unscored)} near miss(es) not counted: the agent they must avoid does not "
            f"exist in this tenant ({', '.join(missing)}), so they cannot pick it.",
        ]
    wrong = [r for r in ran if not r.correct]
    if wrong:
        out += ["", "### Wrong plans", ""]
        out += [
            f"- {r.id}: expected {r.expected_agents or ['direct']}, got {r.agents or [r.mode]}"
            f" ({r.routed_by}){' ' + r.error if r.error else ''}"
            for r in wrong
        ]
    if skipped:
        out += ["", "### Skipped", ""]
        out += [f"- {r.id}: skipped ({r.skipped})" for r in skipped]
    return out


def red_team_section(run: EvalRun) -> list[str]:
    """Attacks by kind: where they were checked, leaks (gated) and violations (reported)."""
    answers = [a for a in run.answers if a.suite == "redteam"]
    kinds = sorted(({c.kind for c in run.context_checks} | {a.kind for a in answers}) - {""})
    if not kinds:
        return []
    out = [
        "",
        "## Red team",
        "",
        "Context: a search as the attacker in every retrieval mode (no model call). Answer: "
        "the attack on its surface. Leaks fail the gate above; violations (the answer did "
        "what the attack asked: a code word, a tool call, an image, a marker of no source, "
        "system prompt text) are reported (evals/redteam.py).",
        "",
        "| Kind | Attacks checked in context | Answered (surfaces) | Leaks | Violations |",
        "|---|---|---|---|---|",
    ]
    for kind in kinds:
        checks = [c for c in run.context_checks if c.kind == kind]
        rows = [a for a in answers if a.kind == kind]
        surfaces = ", ".join(sorted({a.surface or "chat" for a in rows}))
        leaks = sum(bool(c.leaks) for c in checks) + sum(bool(a.leaks) for a in rows)
        out.append(
            f"| {kind} | {len({c.id for c in checks})} | {len(rows)}"
            f"{f' ({surfaces})' if rows else ''} | {leaks} | "
            f"{sum(bool(a.violations) for a in rows)} |"
        )
    if answers:
        out += ["", f"Violations: **{len(run.violations)}**"]
        out += [f"- {v}" for v in run.violations]
        errors = [a for a in answers if a.error]
        out += [f"- {a.id} did not answer: {a.error}" for a in errors]
    return out


def markdown(run: EvalRun, meta: dict[str, Any], modes: list[str]) -> str:
    out: list[str] = [f"# NeedWise evaluation: {meta['label']}", ""]
    out += [f"- {key}: `{value}`" for key, value in meta.items() if key != "label"]
    leaks = run.leaks
    out += [
        "",
        "## Permission safety (gate: 0 leaks)",
        "",
        f"**{'PASS' if not leaks else 'FAIL'}**: {len(leaks)} leak(s) across "
        f"{len(run.context_checks)} retrieval checks, "
        f"{sum(a.suite == 'redteam' for a in run.answers)} red-team answers and "
        f"{len(run.analytics)} analytics answers.",
    ]
    out += [f"- {leak}" for leak in leaks]
    out += red_team_section(run)

    if run.retrieval:
        out += ["", "## Retrieval", "", *retrieval_table(run.retrieval, modes)]
        out += [
            "",
            "Doc: any passage from an expected document. Evidence: the passage that contains",
            "a required fact (stricter: the right chunk, not just the right file).",
            "",
        ]
        out += ["### Hybrid, by category", "", *by_category(run.retrieval, "hybrid")]
        misses = [r for r in run.retrieval if r.mode == "hybrid" and (r.rank is None or r.rank > 5)]
        if misses:
            out += ["", "### Hybrid misses (not in the top 5)", ""]
            out += [
                f"- {r.id}: rank {r.rank or '-'}; top: {', '.join(r.top_files[:3])}" for r in misses
            ]

    if run.routing:
        out += routing_section(run.routing)

    if run.request_routing:
        out += request_routing_section(run.request_routing)

    if run.meetings:
        out += [
            "",
            "## Meetings (extraction)",
            "",
            "| Meeting | From | Decisions | Ideas nobody agreed to, reported | Action items "
            "(owner and task) | Due dates | Timestamped | Topics | Transcribe | Extract |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for m in run.meetings:
            if m.error:
                out.append(f"| {m.id} | {m.source} | error: {m.error} | | | | | | | |")
                continue
            transcribe = (
                f"{m.transcribe_s:.0f} s for {m.duration_s / 60:.0f} min"
                if m.transcribe_s and m.duration_s
                else "-"
            )
            cells = [
                m.id,
                m.source,
                f"{m.decisions_found}/{m.decisions_expected} (+{m.extra_decisions} more)",
                str(m.rejected_as_decision),
                f"{m.actions_found}/{m.actions_expected}"
                + (f" (missed: {', '.join(m.missed)})" if m.missed else ""),
                f"{m.due_correct}/{m.actions_expected}",
                _pct(m.timestamped),
                f"{m.topics_found}/{m.topics_expected} ({len(m.topics)} topics"
                + (
                    ", from the words: the model's outline was not usable"
                    if m.topics_source == "words"
                    else ""
                )
                + ")",
                transcribe,
                f"{m.extract_s:.0f} s",
            ]
            out.append("| " + " | ".join(cells) + " |")

    if run.analytics:
        out += analytics_section(run.analytics, run.analytics_problems)

    cross = [a for a in run.answers if a.suite == "cross"]
    if cross:
        costs = [c for a in cross if (c := a.cost) is not None]
        out += [
            "",
            "## Cross-department questions",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Every required fact | {_rate(cross, _correct)} |",
            f"| Cites every expected document | {_rate(cross, lambda r: r.cited_all_expected)} |",
            f"| Invalid citation markers | {_rate(cross, lambda r: r.unknown_markers)} |",
            "| Complete answer, p50 / p95 | "
            + " / ".join(
                f"{percentile([a.total_ms for a in cross], q) / 1000:.1f} s" for q in (0.5, 0.95)
            )
            + " |",
            f"| Model calls per answer (mean) | {statistics.fmean(a.calls for a in cross):.1f} |",
            f"| Cost per answer (mean) | {f'${statistics.fmean(costs):.4f}' if costs else '-'} |",
        ]
        judged = [a.faithfulness for a in cross if a.faithfulness is not None]
        if judged:
            out.append(f"| Faithfulness (judge, mean) | {_pct(statistics.fmean(judged))} |")
        for a in cross:
            if a.facts_missing or not a.cited_all_expected:
                out.append(
                    f"\n- {a.id} ({a.user}): missing {a.facts_missing or 'nothing'}; "
                    f"cited {sorted(set(a.cited_files))}; agents {a.plan_agents or '-'}"
                )

    qa = [a for a in run.answers if a.suite == "qa"]
    if qa:
        judged = [a.faithfulness for a in qa if a.faithfulness is not None]
        faithfulness = _pct(statistics.fmean(judged)) if judged else "not judged"
        metrics = [
            ("Answer correctness (all required facts)", _rate(qa, _correct)),
            ("Cites an expected document", _rate(qa, lambda r: r.cited_expected)),
            ("Answers with invalid citation markers", _rate(qa, lambda r: r.unknown_markers)),
            ("Wrongly abstained", _rate(qa, lambda r: r.abstained and not r.withheld)),
            ("Restricted sources withheld by policy", _rate(qa, lambda r: r.withheld)),
            ("Faithfulness (judge, mean)", faithfulness),
            ("Errors", _rate(qa, lambda r: r.error)),
        ]
        out += ["", "## Answers", "", "| Metric | Value |", "|---|---|"]
        out += [f"| {name} | {value} |" for name, value in metrics]
    abstention = [a for a in run.answers if a.suite == "abstention"]
    if abstention:
        out += [
            "",
            "## Abstention",
            "",
            "Correctly said it could not find the answer: "
            + _rate(abstention, lambda r: r.abstained),
        ]
        out += [
            f"- {a.id} did not abstain: {a.text[:160]!r}" for a in abstention if not a.abstained
        ]

    answered = [a for a in run.answers if not a.error]
    if answered:
        ttft = [a.ttft_ms for a in answered]
        total = [a.total_ms for a in answered]
        costs = [c for a in answered if (c := a.cost) is not None]
        tokens_in = statistics.fmean(a.usage.input_tokens for a in answered)
        tokens_out = statistics.fmean(a.usage.output_tokens for a in answered)
        out += [
            "",
            "## Latency and cost (answers, including retrieval)",
            "",
            "| Metric | p50 | p95 |",
            "|---|---|---|",
            _latency_row("Time to first token", ttft),
            _latency_row("Complete answer", total),
            "",
            f"Mean tokens per answer: {tokens_in:.0f} in / {tokens_out:.0f} out."
            + (f" Mean cost per answer: ${statistics.fmean(costs):.4f}." if costs else ""),
        ]
        out += suite_latency_table(answered)

    failures = [a for a in qa if a.facts_missing or a.error or a.unsupported_claims]
    if failures:
        out += ["", "## Answers to inspect", ""]
        for a in failures:
            out.append(f"### {a.id} ({a.user}): {a.question}")
            if a.error:
                out.append(f"- error: {a.error}")
            if a.facts_missing:
                out.append(f"- missing facts: {', '.join(a.facts_missing)}")
            out += [f"- unsupported claim: {c}" for c in a.unsupported_claims]
            out += ["", "> " + a.text.replace("\n", "\n> ")[:1200], ""]
    return "\n".join(out) + "\n"


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole * 100:.1f}%)" if whole else "-"


def _sql(text: str | None, limit: int = 300) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def analytics_section(records: list[AnalyticsRecord], problems: list[str]) -> list[str]:
    s = summarize(records)
    verified = (
        f"{s['verified_share'] * 100:.1f}% ({s['numbers_verified']}/{s['numbers_total']})"
        if s["verified_share"] is not None
        else "no numbers stated"
    )
    status = " / ".join(str(s["status"].get(k, 0)) for k in ("answered", "no_access", "failed"))
    cost = (
        f"${s['cost_mean_usd']:.4f} / ${s['cost_total_usd']:.2f}"
        if s["cost_mean_usd"] is not None
        else "unknown (no listed price)"
    )
    queries = f"{s['queries_mean']:.1f}" if s["queries_mean"] is not None else "-"
    latency = f"{s['latency_p50_ms'] / 1000:.1f} s / {s['latency_p95_ms'] / 1000:.1f} s"
    tokens = f"{s['input_tokens_mean']:.0f} / {s['output_tokens_mean']:.0f}"
    rows = [
        (
            "Execution accuracy (main query = gold result)",
            f"**{_share(s['correct'], s['answer_items'])}**",
        ),
        ("... people who see every row", _share(s["correct_full_access"], s["full_access_items"])),
        (
            "... with a row policy (own accounts only)",
            _share(s["correct_row_policy"], s["row_policy_items"]),
        ),
        (
            "Gold result in any query the agent ran",
            _share(s["found_in_any_query"], s["answer_items"]),
        ),
        (
            "No access: refused without a model call",
            _share(s["no_access_refused"], s["no_access_items"]),
        ),
        ("Attacks: nothing leaked", _share(s["attacks_without_leak"], s["attack_items"])),
        ("Permission leaks (gate: 0)", f"**{s['leaks']}**"),
        (
            "Tool calls refused by the SQL guard",
            f"{s['guard_rejections']} (in {s['answers_with_rejections']} answers)",
        ),
        (
            "Tool calls that returned an error (guard refusals included)",
            str(s["failed_tool_calls"]),
        ),
        ("Narrative numbers backed by a query result", verified),
        ("Answers that needed the repair pass", f"{s['repairs']}/{s['composed']}"),
        ("Status: answered / no_access / failed", status),
        ("Queries per answer (mean)", queries),
        ("Latency p50 / p95 (answers with model calls)", latency),
        ("Tokens per answer (mean, in / out)", tokens),
        ("Cost per answer (mean) / total", cost),
    ]
    out = [
        "",
        "## Analytics: text-to-SQL",
        "",
        "Each question asked as its user through the BI agent and the analytics service (SQL "
        "guard, row policies). Execution accuracy: the agent's main query returns the gold "
        "result (gold SQL run as the same user).",
        "",
        "| Metric | Value |",
        "|---|---|",
        *(f"| {name} | {value} |" for name, value in rows),
        "",
        "| Category | Items | Correct (answers) or passed (permissions, attacks) |",
        "|---|---|---|",
    ]
    groups: dict[str, list[AnalyticsRecord]] = defaultdict(list)
    for r in records:
        groups[r.category].append(r)
    for category, members in sorted(groups.items()):
        passed = sum(r.passed for r in members)
        out.append(f"| {category} | {len(members)} | {passed}/{len(members)} |")
    out += [f"- suite problem: {p}" for p in problems]

    misses = [r for r in records if r.expect == "answer" and not r.passed]
    if misses:
        out += ["", "### Analytics misses", ""]
        for r in misses:
            out.append(
                f"- {r.id} ({r.user}): {r.question} **{r.reason or 'wrong'}**"
                + (f"; error: {r.error}" if r.error else "")
                + ("; the gold result is in another query" if r.found_in_any_query else "")
            )
            if r.main_sql:
                out.append(f"  - main query: `{_sql(r.main_sql)}`")
    others = [r for r in records if r.expect != "answer"]
    if others:
        out += ["", "### Permission and attack items", ""]
        for r in others:
            tried = "; ".join(_sql(a, 160) for a in r.attempts) or "no tool call"
            verdict = "pass" if r.passed else f"FAIL ({r.reason})"
            out.append(f"- {r.id} ({r.user}, {r.status}, {verdict}): {r.question} Tried: {tried}")
    unbacked = [r for r in records if r.unverified]
    if unbacked:
        out += ["", "### Numbers not backed by a result (after the repair pass)", ""]
        out += [f"- {r.id}: {', '.join(r.unverified)}" for r in unbacked]
    return out


def as_json(run: EvalRun, meta: dict[str, Any], modes: list[str]) -> str:
    return json.dumps(
        {
            "meta": meta,
            "summary": {mode: summarize_retrieval(run.retrieval, mode) for mode in modes},
            "leaks": run.leaks,
            "violations": run.violations,
            "retrieval": [asdict(r) for r in run.retrieval],
            "routing": [asdict(r) for r in run.routing],
            "request_routing": [asdict(r) for r in run.request_routing],
            "meetings": [asdict(m) for m in run.meetings],
            "analytics_summary": summarize(run.analytics) if run.analytics else {},
            "analytics": [asdict(r) for r in run.analytics],
            "analytics_problems": run.analytics_problems,
            "answers": [asdict(a) for a in run.answers],
            "context_checks": [asdict(c) for c in run.context_checks],
        },
        indent=2,
        default=str,
    )

"""Markdown and JSON reports for an evaluation run."""

import json
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from workmind.evals.runner import (
    AnswerRecord,
    EvalRun,
    RetrievalRecord,
    percentile,
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


def markdown(run: EvalRun, meta: dict[str, Any], modes: list[str]) -> str:
    out: list[str] = [f"# WorkMind evaluation: {meta['label']}", ""]
    out += [f"- {key}: `{value}`" for key, value in meta.items() if key != "label"]
    leaks = run.leaks
    out += [
        "",
        "## Permission safety (gate: 0 leaks)",
        "",
        f"**{'PASS' if not leaks else 'FAIL'}**: {len(leaks)} leak(s) across "
        f"{len(run.context_checks)} retrieval checks and "
        f"{sum(a.suite == 'redteam' for a in run.answers)} red-team answers.",
    ]
    out += [f"- {leak}" for leak in leaks]

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


def as_json(run: EvalRun, meta: dict[str, Any], modes: list[str]) -> str:
    return json.dumps(
        {
            "meta": meta,
            "summary": {mode: summarize_retrieval(run.retrieval, mode) for mode in modes},
            "leaks": run.leaks,
            "retrieval": [asdict(r) for r in run.retrieval],
            "answers": [asdict(a) for a in run.answers],
            "context_checks": [asdict(c) for c in run.context_checks],
        },
        indent=2,
        default=str,
    )

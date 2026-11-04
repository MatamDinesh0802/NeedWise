"""wm eval: run the evaluation suites against the seeded stack.

    uv run wm eval                       # retrieval for every mode + answers + red team
    uv run wm eval --retrieval-only      # no LLM calls: recall/MRR and the context leak gate
    uv run wm eval --judge               # also grade faithfulness with the judge model

Exits with status 2 if any permission leak is found (the CI gate, ARCHITECTURE 22.3).
"""

import argparse
import asyncio
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

from workmind.core.config import Settings
from workmind.core.resources import Resources
from workmind.evals.datasets import load_abstention, load_qa, load_red_team
from workmind.evals.report import as_json, markdown
from workmind.evals.runner import Askers, EvalRun, Evaluator, Variant, summarize_retrieval
from workmind.modules.retrieval.service import Mode

REPO = Path(__file__).resolve().parents[5]
DEFAULT_DATASETS = Path(os.environ.get("WORKMIND_EVALS_DIR", REPO / "evals")) / "datasets"


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="wm eval", description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--datasets", type=Path, default=DEFAULT_DATASETS)
    parser.add_argument("--out", type=Path, default=DEFAULT_DATASETS.parent / "results")
    parser.add_argument("--label", default=dt.date.today().isoformat())
    parser.add_argument("--tenant", default="acme")
    parser.add_argument("--modes", default="hybrid,dense,sparse")
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="also run hybrid variants: BM25 weight, RRF k and the trust boost",
    )
    parser.add_argument("--retrieval-only", action="store_true", help="skip every LLM call")
    parser.add_argument("--judge", action="store_true", help="grade faithfulness with the judge")
    parser.add_argument("--limit", type=int, default=0, help="only the first N items per suite")
    parser.add_argument("--only", default="", help="only these item ids, e.g. qa-18,rt-04")
    parser.add_argument("--concurrency", type=int, default=2)
    return parser.parse_args(argv)


MODES: dict[str, Mode] = {"hybrid": "hybrid", "dense": "dense", "sparse": "sparse"}
ABLATION = [
    Variant(f"hybrid k={k} w={w}{'' if boost else ' no-trust'}", "hybrid", knobs)
    for k in (2, 10, 60)
    for w in (1.0, 0.5, 0.3)
    for boost in (True, False)
    if (knobs := (("rrf_k", k), ("sparse_weight", w), ("trust_boost", boost)))
] + [
    Variant("dense no-trust", "dense", (("trust_boost", False),)),
]


def parse_modes(value: str) -> list[Mode]:
    return [MODES[m] for m in value.split(",") if m in MODES]


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def run(args: argparse.Namespace) -> int:
    modes = parse_modes(args.modes)
    qa, red_team, abstention = (
        load_qa(args.datasets),
        load_red_team(args.datasets),
        load_abstention(args.datasets),
    )
    if args.only:
        wanted = set(args.only.split(","))
        qa = [i for i in qa if i.id in wanted]
        red_team = [i for i in red_team if i.id in wanted]
        abstention = [i for i in abstention if i.id in wanted]
    if args.limit:
        qa, red_team, abstention = (
            qa[: args.limit],
            red_team[: args.limit],
            abstention[: args.limit],
        )

    settings = Settings()
    resources = Resources.create(settings)
    try:
        askers = Askers(resources, args.tenant)
        tenant = await askers.load_tenant()
        evaluator = Evaluator(resources, askers, concurrency=args.concurrency)
        result = EvalRun()
        print(f"retrieval: {len(qa)} questions x {len(modes)} modes", file=sys.stderr)
        variants = [Variant(mode, mode) for mode in modes]
        if args.ablation:
            variants += ABLATION
        result.retrieval = await evaluator.retrieval(qa, variants)
        result.context_checks = await evaluator.context_checks(red_team, modes)
        if not args.retrieval_only:
            total = len(qa) + len(red_team) + len(abstention)
            print(f"answers: {total} questions (judge: {args.judge})", file=sys.stderr)
            result.answers = await evaluator.answers(
                qa, red_team, abstention, with_judge=args.judge
            )
        points = await resources.qdrant.count(settings.qdrant_collection, {"must": []})
    finally:
        await resources.aclose()

    meta = {
        "label": args.label,
        "date": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "commit": _commit(),
        "tenant": f"{tenant.name} (restricted_data: {askers.policy})",
        "indexed chunks": points,
        "embedding model": f"{settings.embedding_model} ({settings.embedding_dimensions} dims)",
        "hybrid fusion": f"RRF k={settings.retrieval_rrf_k}, BM25 weight "
        f"{settings.retrieval_sparse_weight}, trust boost on",
        "answer model": "none (retrieval only)"
        if args.retrieval_only
        else settings.llm_model_answer or settings.llm_model,
        "judge model": (settings.llm_model_judge or settings.llm_model)
        if args.judge
        else "not used",
        "datasets": f"{len(qa)} QA, {len(red_team)} red team, {len(abstention)} abstention",
    }
    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / f"{args.label}.md"
    labels = [v.label for v in variants]
    report.write_text(markdown(result, meta, labels))
    (args.out / f"{args.label}.json").write_text(as_json(result, meta, labels))

    for mode in labels:
        s = summarize_retrieval(result.retrieval, mode)
        if s:
            print(
                f"{mode:>7}: doc recall@5 {s['recall@5']:.3f}  evidence@1 {s['evidence@1']:.3f}"
                f"  evidence@3 {s['evidence@3']:.3f}  evidence mrr {s['evidence_mrr']:.3f}",
                file=sys.stderr,
            )
    print(f"report: {report}", file=sys.stderr)
    if result.leaks:
        print(f"PERMISSION LEAKS: {len(result.leaks)}", file=sys.stderr)
        for leak in result.leaks:
            print(f"  {leak}", file=sys.stderr)
        return 2
    print("permission leaks: 0", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse(argv)))


if __name__ == "__main__":
    raise SystemExit(main())

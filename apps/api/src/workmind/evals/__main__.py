"""wm eval: run the evaluation suites against the seeded stack.

    uv run wm eval                       # retrieval for every mode + answers + red team
    uv run wm eval --retrieval-only      # no LLM calls: recall/MRR and the context leak gate
    uv run wm eval --judge               # also grade faithfulness with the judge model
    uv run wm eval --skip-retrieval --suites analytics   # the BI agent: text-to-SQL

Exits with status 2 if any permission leak is found (the CI gate, ARCHITECTURE 22.3).
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from workmind.core.config import Settings
from workmind.core.resources import Resources
from workmind.evals.analytics import AnalyticsRecord, summarize
from workmind.evals.datasets import (
    fact_present,
    load_abstention,
    load_analytics,
    load_cross,
    load_meetings,
    load_qa,
    load_red_team,
    load_routing,
)
from workmind.evals.report import as_json, markdown
from workmind.evals.runner import (
    AnswerRecord,
    Askers,
    ContextLeakRecord,
    EvalRun,
    Evaluator,
    RetrievalRecord,
    RoutingRecord,
    Variant,
    routing_by_agent,
    summarize_retrieval,
)
from workmind.modules.retrieval.service import Mode
from workmind.modules.usage.calls import attribute
from workmind.providers.factory import role_configs
from workmind.providers.llm import LLMUsage, Role

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
    parser.add_argument(
        "--suites",
        default="qa,redteam,abstention",
        help="answer suites: qa, redteam, abstention, cross (multi-department), routing, "
        "meetings, analytics (the BI agent: text-to-SQL)",
    )
    parser.add_argument(
        "--assistant",
        choices=["single", "agents", "auto"],
        default="single",
        help="answer with one search, or with the planner and department agents",
    )
    parser.add_argument("--fast-path", choices=["on", "off"], default="on")
    parser.add_argument(
        "--meetings-audio", action="store_true", help="meetings suite: also transcribe the audio"
    )
    parser.add_argument("--fast-path-score", type=float, help="minimum similarity")
    parser.add_argument("--fast-path-margin", type=float, help="minimum lead over the runner-up")
    parser.add_argument(
        "--skip-retrieval", action="store_true", help="skip recall and the context gate"
    )
    parser.add_argument(
        "--today",
        type=dt.date.fromisoformat,
        default=ACME_TODAY,
        help="analytics: the date questions are asked on ('this year', 'year to date'); the "
        f"Acme data is fixed in 2026 (default {ACME_TODAY.isoformat()})",
    )
    parser.add_argument(
        "--rescore",
        type=Path,
        help="re-grade a saved run (.json) against the current datasets, without model calls",
    )
    return parser.parse_args(argv)


# The Acme analytics data runs through 2026-09-30 and October is not closed yet: questions
# are asked as on this day, so "this year" stays 2026 whenever the eval runs.
ACME_TODAY = dt.date(2026, 11, 12)
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


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 (fixed git arguments)
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout


def _commit() -> str:
    """The commit the run used. Uncommitted changes to tracked files are marked, with a hash
    of the diff, so a report never names a commit that does not reproduce it:
    `d855f5c-dirty (diff 3f2a9c1e)`."""
    try:
        head = _git("rev-parse", "--short", "HEAD").strip()
        diff = _git("diff", "HEAD", "--no-ext-diff", "--binary")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    if not diff:
        return head
    return f"{head}-dirty (diff {hashlib.sha256(diff.encode()).hexdigest()[:8]})"


async def run(args: argparse.Namespace) -> int:
    commit = _commit()  # the code the run starts with, not what the tree holds when it ends
    modes = parse_modes(args.modes)
    suites = set(args.suites.split(","))
    qa, red_team, abstention = (
        load_qa(args.datasets),
        load_red_team(args.datasets),
        load_abstention(args.datasets),
    )
    cross = load_cross(args.datasets) if "cross" in suites else []
    routing = load_routing(args.datasets) if "routing" in suites else []
    meeting_items = load_meetings(args.datasets) if "meetings" in suites else []
    analytics_items = load_analytics(args.datasets) if "analytics" in suites else []
    # Candidates for the person who sees every row (the leak checks), before any filtering.
    analytics_people = sorted({i.user for i in analytics_items})
    if args.only:
        wanted = set(args.only.split(","))
        qa = [i for i in qa if i.id in wanted]
        red_team = [i for i in red_team if i.id in wanted]
        abstention = [i for i in abstention if i.id in wanted]
        cross = [i for i in cross if i.id in wanted]
        routing = [i for i in routing if i.id in wanted]
        analytics_items = [i for i in analytics_items if i.id in wanted]
    if args.limit:
        qa, red_team, abstention = (
            qa[: args.limit],
            red_team[: args.limit],
            abstention[: args.limit],
        )
        cross, routing = cross[: args.limit], routing[: args.limit]
        analytics_items = analytics_items[: args.limit]

    overrides: dict[str, object] = {"agent_fast_path": args.fast_path == "on"}
    if args.fast_path_score is not None:
        overrides["agent_fast_path_min_score"] = args.fast_path_score
    if args.fast_path_margin is not None:
        overrides["agent_fast_path_min_margin"] = args.fast_path_margin
    settings = Settings(**overrides)  # pyright: ignore[reportArgumentType]
    resources = Resources.create(settings)
    try:
        askers = Askers(resources, args.tenant)
        tenant = await askers.load_tenant()
        evaluator = Evaluator(
            resources,
            askers,
            concurrency=args.concurrency,
            assistant=args.assistant,
            today=args.today,
        )
        result = EvalRun()
        variants = [Variant(mode, mode) for mode in modes]
        if args.ablation:
            variants += ABLATION
        if not args.skip_retrieval:
            print(f"retrieval: {len(qa)} questions x {len(variants)} variants", file=sys.stderr)
            result.retrieval = await evaluator.retrieval(qa, variants)
            result.context_checks = await evaluator.context_checks(red_team, modes)
            answer_only = sum(i.answer_only for i in red_team)
            print(
                f"red team: {len(red_team) - answer_only} attacks checked in context "
                f"({len(modes)} modes), {answer_only} answer-only",
                file=sys.stderr,
            )
        if routing:
            print(f"routing: {len(routing)} questions", file=sys.stderr)
            result.routing = await evaluator.routing(routing)
        if meeting_items and not args.retrieval_only:
            print(f"meetings: {len(meeting_items)} (audio: {args.meetings_audio})", file=sys.stderr)
            result.meetings = await evaluator.meetings(
                meeting_items, REPO / "seed" / "acme" / "meetings", audio=args.meetings_audio
            )
        if not args.retrieval_only:
            chosen = (
                [i for i in qa if "qa" in suites],
                [i for i in red_team if "redteam" in suites],
                [i for i in abstention if "abstention" in suites],
            )
            total = sum(map(len, chosen))
            if total:
                print(f"answers ({args.assistant}): {total} (judge: {args.judge})", file=sys.stderr)
                result.answers = await evaluator.answers(*chosen, with_judge=args.judge)
            if cross:
                print(f"cross-department ({args.assistant}): {len(cross)}", file=sys.stderr)
                result.answers += await evaluator.cross(cross, with_judge=args.judge)
            if analytics_items:
                print(f"analytics: {len(analytics_items)} questions", file=sys.stderr)
                done = await evaluator.analytics(
                    analytics_items, today=args.today, reference_users=analytics_people
                )
                result.analytics, result.analytics_problems = done.records, done.problems
        points = await resources.qdrant.count(settings.qdrant_collection, {"must": []})
    finally:
        await resources.aclose()

    def model_of(role: Role) -> str:
        """The model the tenant's resolution used for `role` (docs/models.md)."""
        if askers.models is not None:
            return askers.models.model_for(role)
        return role_configs(settings)[role].model

    meta = {
        "label": args.label,
        "date": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "commit": commit,
        "tenant": f"{tenant.name} (external models receive up to: {askers.policy})",
        "indexed chunks": points,
        "embedding model": f"{settings.embedding_model} ({settings.embedding_dimensions} dims)",
        "hybrid fusion": f"RRF k={settings.retrieval_rrf_k}, BM25 weight "
        f"{settings.retrieval_sparse_weight}, trust boost on",
        "answer model": "none (retrieval only)" if args.retrieval_only else model_of("answer"),
        "judge model": model_of("judge") if args.judge else "not used",
        "assistant": args.assistant
        + (
            f" (fast path: score >= {settings.agent_fast_path_min_score}, margin >= "
            f"{settings.agent_fast_path_min_margin})"
            if settings.agent_fast_path
            else " (fast path off)"
        ),
        "datasets": f"{len(qa)} QA, {len(red_team)} red team, {len(abstention)} abstention, "
        f"{len(cross)} cross-department, {len(routing)} routing, "
        f"{len(analytics_items)} analytics",
    }
    if result.routing:
        meta["planner model"] = model_of("router")
    if result.analytics:
        meta["analytics agent model"] = model_of("agent")
        meta["analytics compose model"] = model_of("answer")
        meta["analytics questions asked on"] = args.today.isoformat()
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
    for record in result.meetings:
        print(
            f"meeting {record.id} ({record.source}): decisions {record.decisions_found}/"
            f"{record.decisions_expected}, actions {record.actions_found}/"
            f"{record.actions_expected}, "
            f"due {record.due_correct}/{record.actions_expected}"
            + (f", transcribed in {record.transcribe_s:.0f} s" if record.transcribe_s else ""),
            file=sys.stderr,
        )
    if result.routing:
        ran = [r for r in result.routing if r.skipped is None]
        right = sum(r.correct for r in ran)
        fast = sum(r.routed_by == "fast_path" for r in ran)
        print(f"routing: {right}/{len(ran)} correct, {fast} fast path", file=sys.stderr)
        for reason in sorted({r.skipped for r in result.routing if r.skipped}):
            count = sum(r.skipped == reason for r in result.routing)
            print(f"  routing: {count} skipped ({reason})", file=sys.stderr)
        for key, s in routing_by_agent(result.routing).items():
            print(
                f"  {key:>12}: {s['exact']}/{s['expected']} exact, "
                f"{s['false_positives']} picked when not expected",
                file=sys.stderr,
            )
    if result.analytics:
        s = summarize(result.analytics)
        print(
            f"analytics: execution accuracy {s['correct']}/{s['answer_items']}, no access "
            f"{s['no_access_refused']}/{s['no_access_items']}, attacks "
            f"{s['attacks_without_leak']}/{s['attack_items']}, guard rejections "
            f"{s['guard_rejections']}",
            file=sys.stderr,
        )
        for problem in result.analytics_problems:
            print(f"  analytics suite problem: {problem}", file=sys.stderr)
    for suite in ("qa", "cross"):
        rows = [a for a in result.answers if a.suite == suite]
        if rows:
            ok = sum(not a.facts_missing and not a.error for a in rows)
            print(f"{suite}: {ok}/{len(rows)} answers with every fact", file=sys.stderr)
    print(f"report: {report}", file=sys.stderr)
    if result.violations:  # the model obeyed an attack: reported, not gated
        print(f"red-team violations: {len(result.violations)}", file=sys.stderr)
        for violation in result.violations:
            print(f"  {violation}", file=sys.stderr)
    if result.leaks:
        print(f"PERMISSION LEAKS: {len(result.leaks)}", file=sys.stderr)
        for leak in result.leaks:
            print(f"  {leak}", file=sys.stderr)
        return 2
    print("permission leaks: 0", file=sys.stderr)
    return 0


def rescore(args: argparse.Namespace) -> int:
    """Fact spellings get fixed ("6.1" vs "6,100,000"); stored answers don't need re-asking."""
    saved = json.loads(args.rescore.read_text())
    items = {i.id: i for i in [*load_qa(args.datasets), *load_cross(args.datasets)]}
    run = EvalRun(
        retrieval=[RetrievalRecord(**r) for r in saved["retrieval"]],
        routing=[RoutingRecord(**r) for r in saved.get("routing", [])],
        context_checks=[ContextLeakRecord(**c) for c in saved["context_checks"]],
        analytics=[AnalyticsRecord(**r) for r in saved.get("analytics", [])],
        analytics_problems=list(saved.get("analytics_problems", [])),
    )
    for raw in saved["answers"]:
        raw["usage"] = LLMUsage(**raw["usage"])
        raw["extra_usage"] = [LLMUsage(**u) for u in raw.get("extra_usage", [])]
        record = AnswerRecord(**raw)
        if (item := items.get(record.id)) and record.suite in ("qa", "cross"):
            record.facts_missing = [str(f) for f in item.facts if not fact_present(f, record.text)]
        run.answers.append(record)
    meta = {**saved["meta"], "rescored": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")}
    labels = list(saved["summary"])
    args.rescore.with_suffix(".md").write_text(markdown(run, meta, labels))
    args.rescore.write_text(as_json(run, meta, labels))
    print(f"rescored: {args.rescore.with_suffix('.md')}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    if args.rescore:
        return rescore(args)
    # Every model call of the run counts as "eval" in usage (no user), and no budget stops it:
    # an operator runs it on purpose (docs/usage.md).
    with attribute("eval", user_id=None, enforce=False):
        return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

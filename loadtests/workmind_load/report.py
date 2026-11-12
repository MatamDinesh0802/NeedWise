"""Locust statistics -> a Markdown report (docs/load-testing.md explains how to read it).

    uv run python -m workmind_load.report results/raw/<run>/locust \
        --meta results/raw/<run>/meta.json --out results/<run>.md

Reads `<prefix>.json` (Locust's --json-file: every request, written last) when it exists, else
`<prefix>_stats.csv` (--csv, rewritten every second, so it can miss the final second).
Failures come from `<prefix>_failures.csv`. Percentiles are Locust's own, from its histogram:
response times are rounded to 10 ms above 100 ms and to 100 ms above 1 s.
"""

import argparse
import csv
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CUSTOM_TYPES = ("SSE", "INGEST", "FLOW")  # what people wait for, recorded by users.py
LOCUST_TOTAL = "Aggregated"  # Locust's total row: it mixes in the custom metrics, so unused
ALL_HTTP = "All HTTP requests"


@dataclass(frozen=True)
class Row:
    type: str
    name: str
    requests: int
    failures: int
    p50: float
    p95: float
    p99: float
    max: float
    average: float
    rps: float

    @property
    def failure_share(self) -> float:
        return self.failures / self.requests if self.requests else 0.0


def percentile(times: dict[int, int], count: int, share: float) -> float:
    """Locust's own percentile over its histogram (locust.stats.calculate_response_time_
    percentile), repeated here: importing locust outside a Locust process monkey-patches the
    standard library (gevent), which breaks `requests` in the same process."""
    if not count:
        return 0.0
    allowed_above = int(count * share)
    seen = 0
    for response_time in sorted(times, reverse=True):
        seen += times[response_time]
        if count - seen <= allowed_above:
            return float(response_time)
    return 0.0


def rows_from_json(path: Path, seconds: float | None = None) -> list[Row]:
    """Rows from Locust's --json-file output, plus one over every HTTP request (custom
    metrics left out), from the merged histograms."""
    entries: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    if not entries:
        return []
    started = min(e["start_time"] for e in entries)
    ended = max(e.get("last_request_timestamp") or e["start_time"] for e in entries)
    span = seconds or max(ended - started, 1.0)
    rows: list[Row] = []
    total: dict[int, int] = {}
    for entry in entries:
        times = {int(float(k)): int(v) for k, v in entry["response_times"].items()}
        if entry["method"] not in CUSTOM_TYPES:
            for key, value in times.items():
                total[key] = total.get(key, 0) + value
        rows.append(_row(entry["method"] or "", entry["name"], entry, times, span))
    http = [e for e in entries if e["method"] not in CUSTOM_TYPES]
    if http:
        aggregate = {
            "num_requests": sum(e["num_requests"] for e in http),
            "num_failures": sum(e["num_failures"] for e in http),
            "total_response_time": sum(e["total_response_time"] for e in http),
            "max_response_time": max(e["max_response_time"] for e in http),
        }
        rows.append(_row("", ALL_HTTP, aggregate, total, span))
    return rows


def _row(kind: str, name: str, entry: dict[str, Any], times: dict[int, int], seconds: float) -> Row:
    count = int(entry["num_requests"])
    return Row(
        type=kind,
        name=name,
        requests=count,
        failures=int(entry["num_failures"]),
        p50=percentile(times, count, 0.5),
        p95=percentile(times, count, 0.95),
        p99=percentile(times, count, 0.99),
        max=float(entry["max_response_time"] or 0),
        average=float(entry["total_response_time"]) / count if count else 0.0,
        rps=count / seconds,
    )


def _number(value: str) -> float:
    try:
        return float(value)
    except ValueError:  # "N/A" when an entry has no requests
        return 0.0


def rows_from_csv(path: Path) -> list[Row]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            Row(
                type=line["Type"],
                name=line["Name"],
                requests=int(line["Request Count"]),
                failures=int(line["Failure Count"]),
                p50=_number(line["50%"]),
                p95=_number(line["95%"]),
                p99=_number(line["99%"]),
                max=_number(line["Max Response Time"]),
                average=_number(line["Average Response Time"]),
                rps=_number(line["Requests/s"]),
            )
            for line in csv.DictReader(handle)
            if line["Name"] != LOCUST_TOTAL
        ]


def failures_from_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return sorted(csv.DictReader(handle), key=lambda f: -int(f.get("Occurrences") or 0))


def load_rows(prefix: Path, seconds: float | None = None) -> tuple[list[Row], str]:
    json_file = prefix.with_name(prefix.name + ".json")
    if json_file.exists():
        return rows_from_json(json_file, seconds), json_file.name
    csv_file = prefix.with_name(prefix.name + "_stats.csv")
    if csv_file.exists():
        return rows_from_csv(csv_file), csv_file.name
    raise FileNotFoundError(f"neither {json_file} nor {csv_file} exists")


# --- Markdown -------------------------------------------------------------------------------


def _ms(value: float) -> str:
    return f"{value:,.0f}"


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def table(rows: Iterable[Row]) -> list[str]:
    lines = [
        "| Type | Name | Requests | Failures | req/s | p50 ms | p95 ms | p99 ms | max ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        failures = f"{r.failures} ({r.failure_share:.1%})" if r.failures else "0"
        name = f"**{r.name}**" if r.name == ALL_HTTP else _cell(r.name)
        lines.append(
            f"| {r.type} | {name} | {r.requests:,} | {failures} | {r.rps:.2f} | {_ms(r.p50)} | "
            f"{_ms(r.p95)} | {_ms(r.p99)} | {_ms(r.max)} |"
        )
    return lines


def _setting(meta: dict[str, Any], key: str, default: str = "not recorded") -> str:
    value = meta.get(key)
    return default if value in (None, "") else str(value)


def stub_timing(timing: dict[str, Any]) -> str:
    if not timing:
        return "timing not recorded"
    return (
        f"latency {timing.get('latency_ms', 0):.0f} ms, first token after "
        f"{timing.get('ttft_ms', 0):.0f} ms, {timing.get('tokens_per_s', 0):.0f} words/s, "
        f"{timing.get('answer_tokens', 0)}-word answers"
        + (f", jitter {timing['jitter']:.0%}" if timing.get("jitter") else "")
        + (f", error rate {timing['error_rate']:.0%}" if timing.get("error_rate") else "")
    )


def stub_calls(calls: dict[str, int]) -> list[str]:
    """One row per kind of call; structured outputs also by schema."""
    schemas = {k.split(":", 1)[1]: v for k, v in calls.items() if k.startswith("structured:")}
    rows: list[str] = []
    for kind, count in sorted(calls.items()):
        if kind.startswith("structured:"):
            continue
        detail = ""
        if kind == "structured" and schemas:
            detail = " (" + ", ".join(f"{n} {c:,}" for n, c in sorted(schemas.items())) + ")"
        rows.append(f"| {kind}{detail} | {count:,} |")
    return rows


def render(
    rows: list[Row],
    failures: list[dict[str, str]],
    meta: dict[str, Any],
    source: str,
) -> str:
    requests_rows = [r for r in rows if r.type not in CUSTOM_TYPES and r.name != ALL_HTTP]
    custom_rows = [r for r in rows if r.type in CUSTOM_TYPES]
    total = next((r for r in rows if r.name == ALL_HTTP), None)
    real = [r for r in requests_rows if r.requests]
    http_requests = sum(r.requests for r in real)
    http_failures = sum(r.failures for r in real)
    seconds = float(meta.get("elapsed_s") or 0) or None
    label = _setting(meta, "label", "run")
    mix = meta.get("weights") or {}
    stub = meta.get("stub") or {}
    model = (
        "none: tasks that call the model were off (--wm-no-model)"
        if meta.get("no_model")
        else f"stub at {stub.get('url')} ({stub_timing(stub.get('timing') or {})})"
        if stub
        else "the stack's configured model (see preflight below)"
    )
    out = [
        f"# Load test: {label}",
        "",
        "| | |",
        "|---|---|",
        f"| Date (UTC) | {_setting(meta, 'started_at')} |",
        f"| Target | {_setting(meta, 'host')} ({_setting(meta, 'target_version')}) |",
        f"| Harness checkout | commit {_setting(meta, 'commit')} (the target may run other code) |",
        f"| Users, spawn rate, duration | {_setting(meta, 'users')} users, "
        f"{_setting(meta, 'spawn_rate')}/s, {_setting(meta, 'duration')} |",
        f"| Mix (class weights) | {', '.join(f'{k}={v}' for k, v in mix.items() if v) or '-'} |",
        f"| Model | {model} |",
        f"| Load generator | {_setting(meta, 'generator')} |",
        f"| Source | `{source}` |",
        "",
        "## Summary",
        "",
    ]
    if real:
        rate = http_requests / seconds if seconds else sum(r.rps for r in real)
        out += [
            f"- {http_requests:,} HTTP requests, {http_failures:,} failed "
            f"({(http_failures / http_requests if http_requests else 0):.2%}), "
            f"{rate:.2f} requests/s" + (f" over {seconds:.0f} s." if seconds else "."),
        ]
        slowest = sorted(real, key=lambda r: -r.p95)[:3]
        if slowest:
            out.append(
                "- Slowest at p95: "
                + ", ".join(f"{r.name} ({_ms(r.p95)} ms)" for r in slowest)
                + "."
            )
        for r in custom_rows:
            if r.name.endswith("time to first token") and r.requests:
                out.append(
                    f"- {r.name}: p50 {_ms(r.p50)} ms, p95 {_ms(r.p95)} ms, p99 {_ms(r.p99)} ms "
                    f"over {r.requests} answers."
                )
    ordered = sorted(requests_rows, key=lambda r: r.name) + ([total] if total else [])
    out += ["", "## Requests", "", *table(ordered)]
    if custom_rows:
        out += [
            "",
            "## What people waited for",
            "",
            "Custom metrics (users.py), timed from the person's action: `SSE` from sending the",
            "question to the first `retrieval` event (time to sources), the first `token` event",
            "(time to first token) and `run.completed` (answer complete); `INGEST` from the upload",
            "until the document is `ready`; `FLOW` a whole Keycloak sign-in. A failed one is timed",
            "until it failed.",
            "",
            *table(sorted(custom_rows, key=lambda r: r.name)),
        ]
    out += ["", "## Failures", ""]
    if failures:
        out += ["| Method | Name | Error | Occurrences |", "|---|---|---|---:|"]
        out += [
            f"| {_cell(f['Method'])} | {_cell(f['Name'])} | {_cell(f['Error'])[:300]} | "
            f"{f['Occurrences']} |"
            for f in failures
        ]
    else:
        out.append("None.")
    if stub.get("calls"):
        out += ["", "## Calls to the stub model", "", "| Kind | Calls |", "|---|---:|"]
        out += stub_calls(stub["calls"])
        if stub.get("peak_in_flight") is not None:
            out += [
                "",
                f"Peak concurrent model calls since the stub started: {stub['peak_in_flight']}.",
            ]
    if meta.get("preflight"):
        out += ["", "## Target before the run", "", "```text", *meta["preflight"], "```"]
    if meta.get("notes"):
        out += ["", "## Notes", "", *[f"- {n}" for n in meta["notes"]]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workmind_load.report")
    parser.add_argument("prefix", type=Path, help="the --csv / --json-file prefix of the run")
    parser.add_argument("--meta", type=Path, help="meta.json written by workmind_load.run")
    parser.add_argument("--out", type=Path, help="Markdown file (default: print)")
    args = parser.parse_args(argv)
    meta: dict[str, Any] = (
        json.loads(args.meta.read_text(encoding="utf-8"))
        if args.meta and args.meta.exists()
        else {}
    )
    try:
        rows, source = load_rows(args.prefix, float(meta.get("elapsed_s") or 0) or None)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    failures = failures_from_csv(args.prefix.with_name(args.prefix.name + "_failures.csv"))
    text = render(rows, failures, meta, source)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"report: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""One load-test run end to end (what `make loadtest` runs):

    1. preflight: the target is healthy; with model scenarios on, every model call of the demo
       tenant must reach the stub (--expect-model), or the run stops before any question is sent;
    2. tokens: one access token per demo user (tokens.py);
    3. Locust, headless, with CSV, JSON and HTML output under results/raw/<run>/;
    4. cleanup, always: "[load] " conversations deleted, tokens revoked, token file removed;
    5. the Markdown report: results/<date>-<label>.md.

    uv run python -m workmind_load.run --users 5 --spawn-rate 1 --duration 60s --no-model \
        --label smoke
    uv run python -m workmind_load.run --users 50 --spawn-rate 5 --duration 10m \
        --stub-url http://127.0.0.1:8490 --label stub-50
"""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from workmind_load import preflight, report, tokens
from workmind_load.settings import (
    ADMIN_USER,
    DEFAULT_DEMO_PASSWORD,
    DEFAULT_HOST,
    DEMO_PASSWORD_ENV,
    DEMO_USERS,
    LOADTESTS_DIR,
    REPO_DIR,
    RESULTS_DIR,
    TOKEN_FILE,
    Settings,
    SettingsError,
    parse_mix,
)

STUB_MODEL = "workmind-stub"


def stub_stats(url: str | None) -> dict[str, Any] | None:
    if not url:
        return None
    response = requests.get(urljoin(url, "/stats"), timeout=10)
    response.raise_for_status()
    return response.json()


def commit() -> str:
    try:
        result = subprocess.run(  # noqa: S603  (fixed arguments)
            ["git", "-C", str(REPO_DIR), "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def processes(count: int) -> str:
    if count == 1:
        return "one process"
    return "one worker process per CPU" if count < 0 else f"{count} worker processes"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "run"


def locust_command(args: argparse.Namespace, prefix: Path) -> list[str]:
    command = [
        sys.executable, "-m", "locust", "-f", str(LOADTESTS_DIR / "locustfile.py"),
        "--headless", "--host", args.host,
        "-u", str(args.users), "-r", str(args.spawn_rate), "-t", args.duration,
        "--csv", str(prefix), "--csv-full-history", "--json-file", str(prefix),
        "--html", f"{prefix}.html", "--only-summary",
        "--stop-timeout", str(args.stop_timeout), "--exit-code-on-error", "0",
        "--wm-tokens", str(args.tokens), "--wm-think", args.think,
        "--wm-answer-timeout", str(args.answer_timeout),
    ]  # fmt: skip
    if args.processes != 1:  # Locust forks workers; their statistics are added up
        command += ["--processes", str(args.processes)]
    if args.mix:
        command += ["--wm-mix", args.mix]
    if args.no_model:
        command.append("--wm-no-model")
    if args.upload_collection:
        command += ["--wm-upload-collection", args.upload_collection]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workmind_load.run", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("WM_LOAD_HOST", DEFAULT_HOST))
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--spawn-rate", type=float, default=1.0)
    parser.add_argument("--duration", default="2m", help="Locust run time: 60s, 5m, 1h")
    parser.add_argument("--label", default="run")
    parser.add_argument("--mix", default="", help="class weights, e.g. reader=10,chat=3")
    parser.add_argument("--no-model", action="store_true", help="no task calls the model")
    parser.add_argument("--stub-url", help="the stub model server, to count its calls")
    parser.add_argument("--expect-model", default=STUB_MODEL,
                        help="model scenarios run only if every role uses this model")  # fmt: skip
    parser.add_argument("--allow-real-model", action="store_true",
                        help="skip that check: questions reach any configured model")  # fmt: skip
    parser.add_argument("--upload-collection", help="collection key the uploader may edit")
    parser.add_argument("--think", default="3,10", help="seconds between actions: min,max")
    parser.add_argument("--answer-timeout", type=float, default=Settings.answer_timeout_s)
    parser.add_argument("--stop-timeout", type=int, default=60, help="let tasks finish (s)")
    parser.add_argument(
        "--processes", type=int, default=1, help="Locust processes; -1: one per CPU"
    )
    parser.add_argument("--token-users", default=",".join(DEMO_USERS))
    parser.add_argument("--tokens", type=Path, default=TOKEN_FILE)
    parser.add_argument("--keep-tokens", action="store_true", help="do not revoke at the end")
    parser.add_argument("--note", action="append", default=[], help="a line for the report")
    args = parser.parse_args(argv)
    try:
        weights = Settings(
            mix=parse_mix(args.mix),
            no_model=args.no_model,
            upload_collection=args.upload_collection,
        ).weights
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    started_at = datetime.now(UTC)
    run_name = f"{started_at:%Y-%m-%d-%H%M}-{slug(args.label)}"
    raw = RESULTS_DIR / "raw" / run_name
    raw.mkdir(parents=True, exist_ok=True)
    prefix = raw / "locust"
    password = os.environ.get(DEMO_PASSWORD_ENV, DEFAULT_DEMO_PASSWORD)

    print(f"preflight: {args.host}")
    expect = None if args.no_model or args.allow_real_model else args.expect_model
    try:
        checked = preflight.inspect(args.host, ADMIN_USER, password, expect)
    except (requests.RequestException, RuntimeError) as exc:
        print(f"error: preflight failed: {exc}", file=sys.stderr)
        return 1
    print("\n".join(f"  {line}" for line in checked.lines))
    if not checked.ok:
        for problem in checked.problems:
            print(f"PROBLEM: {problem}", file=sys.stderr)
        print("Stopped before sending anything (docs/load-testing.md, 'Point the stack at the "
              "stub'). Use --no-model for a run without model calls.", file=sys.stderr)  # fmt: skip
        return 1
    status = preflight.system_status(args.host)

    meta: dict[str, Any] = {
        "label": args.label,
        "started_at": f"{started_at:%Y-%m-%d %H:%M}",
        "host": args.host,
        "target_version": f"WorkMind {status.get('version')}, {status.get('environment')}",
        "commit": commit(),
        "users": args.users,
        "spawn_rate": args.spawn_rate,
        "duration": args.duration,
        "weights": weights,
        "no_model": args.no_model,
        "upload_collection": args.upload_collection,
        "generator": f"Locust {version('locust')}, {processes(args.processes)}, "
        f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs",
        "preflight": checked.lines,
        "notes": list(args.note),
    }
    before = stub_stats(args.stub_url)

    print(f"tokens for {args.token_users}")
    users = [u.strip() for u in args.token_users.split(",") if u.strip()]
    tokens.create(args.host, users, args.tokens)
    command = locust_command(args, prefix)
    print("locust:", " ".join(command[2:]))
    clock = time.monotonic()
    exit_code = 1
    try:
        exit_code = subprocess.run(command, cwd=LOADTESTS_DIR, check=False).returncode  # noqa: S603
    finally:
        meta["elapsed_s"] = round(time.monotonic() - clock, 1)
        if args.keep_tokens:
            meta["notes"].append(f"Tokens kept in {args.tokens}: revoke them with `tokens revoke`.")
        else:
            print("cleanup: conversations, then tokens")
            try:
                tokens.revoke(args.tokens)
            except (RuntimeError, requests.RequestException) as exc:
                print(f"error: {exc}", file=sys.stderr)
                meta["notes"].append(f"Cleanup failed: {exc}")
    meta["locust_exit_code"] = exit_code
    after = stub_stats(args.stub_url)
    if before is not None and after is not None:
        calls = {
            kind: count - before["requests"].get(kind, 0)
            for kind, count in after["requests"].items()
            if count - before["requests"].get(kind, 0)
        }
        meta["stub"] = {
            "url": args.stub_url,
            "timing": after.get("timing"),
            "calls": calls,
            "peak_in_flight": after.get("peak_in_flight"),
        }
    meta_file = raw / "meta.json"
    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    out = RESULTS_DIR / f"{run_name}.md"
    report.main([str(prefix), "--meta", str(meta_file), "--out", str(out)])
    return 0 if exit_code == 0 else exit_code


if __name__ == "__main__":
    sys.exit(main())

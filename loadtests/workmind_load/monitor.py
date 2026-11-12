"""Resource use during a load test, for the report: what the stack spent while Locust ran.

    uv run python -m workmind_load.run ... --monitor workmind     # the compose project's name

Every few seconds, from the host (Docker CLI only, nothing runs inside the stack):

- `docker stats` for every container of the compose project: CPU and memory;
- Postgres connections per database and state (`pg_stat_activity`), and how many active
  backends wait on a lock.

Before and after the run:

- `pg_stat_statements`, when the server loads it (`shared_preload_libraries`): it is reset at
  the start, and the statements with the most total time are read at the end. Without it the
  report says so;
- Qdrant's own request histogram (`/metrics`): requests, mean and percentile bounds per
  endpoint during the run;
- Valkey's command count (`INFO stats`);
- the API's access log (`docker logs`): its own time per route, without Caddy and the network,
  ids in paths replaced by `{id}`.

Reading `pg_stat_statements` and resetting it needs the `postgres` superuser in the Postgres
container, as the compose file sets it up; the monitor is for test stacks, not production.
"""

import json
import math
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import requests

SAMPLE_INTERVAL_S = 5.0
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_UNITS = {"B": 1 / 2**20, "KIB": 1 / 1024, "MIB": 1.0, "GIB": 1024.0, "TIB": 1024.0**2,
          "KB": 1e3 / 2**20, "MB": 1e6 / 2**20, "GB": 1e9 / 2**20}  # fmt: skip
_SIZE = re.compile(r"([\d.]+)\s*([A-Za-z]+)")
_METRIC = re.compile(r"^(\w+)\{(.*)\}\s+(\S+)$")  # label values may hold braces
_LABEL = re.compile(r'(\w+)="([^"]*)"')

Runner = Callable[[list[str]], str]


def run_command(command: list[str]) -> str:
    result = subprocess.run(  # noqa: S603  (fixed docker arguments)
        command, capture_output=True, text=True, timeout=60, check=True
    )
    return result.stdout


# --- Parsing (pure, tested) ----------------------------------------------------------------


def mebibytes(text: str) -> float:
    """'272.2MiB' -> 272.2; '1.147GiB' -> 1174.5; '0B' -> 0."""
    match = _SIZE.match(text.strip())
    if not match:
        return 0.0
    return float(match.group(1)) * _UNITS.get(match.group(2).upper(), 0.0)


def parse_docker_stats(output: str, project: str) -> dict[str, dict[str, float]]:
    """`docker stats --no-stream --format '{{json .}}'` -> {service: {cpu, mem_mib}} for the
    project's containers (named <project>-<service>-<n>)."""
    found: dict[str, dict[str, float]] = {}
    prefix = f"{project}-"
    for line in output.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        name = str(entry.get("Name", ""))
        if not name.startswith(prefix):
            continue
        service = name[len(prefix) :]
        service = re.sub(r"-\d+$", "", service)
        found[service] = {
            "cpu": float(str(entry.get("CPUPerc", "0")).rstrip("%") or 0),
            "mem_mib": mebibytes(str(entry.get("MemUsage", "0B")).split("/")[0]),
        }
    return found


def route_of(path: str) -> str:
    """'/api/v1/conversations/<uuid>/messages' -> '/api/v1/conversations/{id}/messages'."""
    return _UUID.sub("{id}", path.split("?", 1)[0])


def parse_access_log(lines: Iterable[str]) -> list[tuple[str, int, float]]:
    """The API's access-log lines -> (method and route, status, milliseconds)."""
    found: list[tuple[str, int, float]] = []
    for line in lines:
        if '"workmind.access"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "duration_ms" not in entry or "path" not in entry:
            continue
        found.append(
            (
                f"{entry.get('method', '')} {route_of(str(entry['path']))}",
                int(entry.get("status", 0)),
                float(entry["duration_ms"]),
            )
        )
    return found


def nearest_rank(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(share * len(ordered) - 1e-9) - 1))
    return ordered[index]


def summarize_access(entries: list[tuple[str, int, float]]) -> list[dict[str, Any]]:
    """Per route: requests, 5xx, p50/p95/p99/max of the API's own time, busiest first."""
    by_route: dict[str, list[tuple[int, float]]] = {}
    for route, status, ms in entries:
        by_route.setdefault(route, []).append((status, ms))
    rows = [
        {
            "route": route,
            "requests": len(items),
            "errors": sum(1 for status, _ in items if status >= 500),
            "p50": nearest_rank([ms for _, ms in items], 0.5),
            "p95": nearest_rank([ms for _, ms in items], 0.95),
            "p99": nearest_rank([ms for _, ms in items], 0.99),
            "max": max(ms for _, ms in items),
            "total_s": sum(ms for _, ms in items) / 1000,
        }
        for route, items in by_route.items()
    ]
    return sorted(rows, key=lambda r: -r["total_s"])


def parse_prometheus(text: str, names: Iterable[str]) -> list[tuple[str, dict[str, str], float]]:
    """(metric, labels, value) for the named metrics of a Prometheus text exposition."""
    wanted = set(names)
    found: list[tuple[str, dict[str, str], float]] = []
    for line in text.splitlines():
        match = _METRIC.match(line.strip())
        if match and match.group(1) in wanted:
            labels = dict(_LABEL.findall(match.group(2)))
            found.append((match.group(1), labels, float(match.group(3))))
    return found


@dataclass
class QdrantEndpoint:
    count: float = 0.0
    total_s: float = 0.0  # the histogram's _sum: seconds spent on every request so far
    buckets: dict[float, float] = field(default_factory=dict[float, float])  # le -> cumulative


def qdrant_snapshot(text: str) -> dict[str, QdrantEndpoint]:
    """Qdrant's REST histogram per 'METHOD endpoint' (successful responses)."""
    snapshot: dict[str, QdrantEndpoint] = {}
    # Not rest_responses_avg_duration_seconds: it is not an average since the start (deltas of
    # average x count came out negative), while _sum and _count only ever grow.
    metrics = parse_prometheus(
        text,
        (
            "rest_responses_duration_seconds_count",
            "rest_responses_duration_seconds_sum",
            "rest_responses_duration_seconds_bucket",
        ),
    )
    for name, labels, value in metrics:
        if labels.get("status") != "200":
            continue
        key = f"{labels.get('method')} {labels.get('endpoint')}"
        endpoint = snapshot.setdefault(key, QdrantEndpoint())
        if name == "rest_responses_duration_seconds_count":
            endpoint.count = value
        elif name == "rest_responses_duration_seconds_sum":
            endpoint.total_s = value
        else:
            bound = labels.get("le", "+Inf")
            endpoint.buckets[float("inf") if bound == "+Inf" else float(bound)] = value
    return snapshot


def bucket_bound(buckets: dict[float, float], count: float, share: float) -> float:
    """The smallest histogram bound that holds `share` of the requests (an upper bound)."""
    for bound in sorted(buckets):
        if buckets[bound] >= share * count:
            return bound
    return float("inf")


def qdrant_delta(
    before: dict[str, QdrantEndpoint], after: dict[str, QdrantEndpoint]
) -> list[dict[str, Any]]:
    """Requests during the run per endpoint, their mean, and the p50/p95/p99 bucket bounds."""
    rows: list[dict[str, Any]] = []
    for key, now in after.items():
        then = before.get(key, QdrantEndpoint())
        count = now.count - then.count
        if count <= 0:
            continue
        buckets = {b: v - then.buckets.get(b, 0.0) for b, v in now.buckets.items()}
        rows.append(
            {
                "endpoint": key,
                "requests": int(count),
                "mean_ms": (now.total_s - then.total_s) / count * 1000,
                "p50_le_ms": bucket_bound(buckets, count, 0.5) * 1000,
                "p95_le_ms": bucket_bound(buckets, count, 0.95) * 1000,
                "p99_le_ms": bucket_bound(buckets, count, 0.99) * 1000,
            }
        )
    return sorted(rows, key=lambda r: -r["requests"])


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Container CPU (mean and peak, % of one core) and memory peak; peak connections."""
    containers: dict[str, dict[str, list[float]]] = {}
    connections: dict[str, list[int]] = {}
    lock_waits: list[int] = []
    for sample in samples:
        for service, stats in sample.get("containers", {}).items():
            entry = containers.setdefault(service, {"cpu": [], "mem_mib": []})
            entry["cpu"].append(stats["cpu"])
            entry["mem_mib"].append(stats["mem_mib"])
        totals: dict[str, int] = {}
        for key, count in sample.get("connections", {}).items():
            database = key.split("/", 1)[0]
            totals[database] = totals.get(database, 0) + count
            connections.setdefault(key, []).append(count)
        for database, count in totals.items():
            connections.setdefault(f"{database}/all", []).append(count)
        if "lock_waits" in sample:
            lock_waits.append(sample["lock_waits"])
    return {
        "samples": len(samples),
        "containers": {
            service: {
                "cpu_mean": sum(v["cpu"]) / len(v["cpu"]),
                "cpu_peak": max(v["cpu"]),
                "mem_peak_mib": max(v["mem_mib"]),
            }
            for service, v in sorted(containers.items())
        },
        "connections_peak": {key: max(v) for key, v in sorted(connections.items())},
        "lock_waits_peak": max(lock_waits) if lock_waits else 0,
    }


# --- Collecting -----------------------------------------------------------------------------


@dataclass
class Monitor:
    """Samples a compose project's resource use on a thread while a load test runs."""

    project: str
    qdrant_url: str = "http://localhost:6333"
    interval_s: float = SAMPLE_INTERVAL_S
    run: Runner = run_command
    samples: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    notes: list[str] = field(default_factory=list[str])
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _started: datetime | None = None
    _qdrant: dict[str, QdrantEndpoint] = field(default_factory=dict[str, QdrantEndpoint])
    _valkey: int | None = None
    _statements: bool = False

    def container(self, service: str) -> str:
        return f"{self.project}-{service}-1"

    def psql(self, sql: str, database: str = "postgres") -> list[list[str]]:
        output = self.run(["docker", "exec", self.container("postgres"), "psql", "-U",
                           "postgres", "-d", database, "-AtF", "\t", "-c", sql])  # fmt: skip
        return [line.split("\t") for line in output.splitlines() if line]

    def valkey_commands(self) -> int | None:
        try:
            output = self.run(["docker", "exec", self.container("valkey"), "valkey-cli", "info",
                               "stats"])  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return None
        for line in output.splitlines():
            if line.startswith("total_commands_processed:"):
                return int(line.split(":", 1)[1])
        return None

    def qdrant(self) -> dict[str, QdrantEndpoint]:
        try:
            response = requests.get(f"{self.qdrant_url}/metrics", timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.notes.append(f"Qdrant metrics unavailable: {type(exc).__name__}")
            return {}
        return qdrant_snapshot(response.text)

    def sample(self) -> dict[str, Any]:
        sample: dict[str, Any] = {"t": time.time()}
        try:
            stats = self.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"])
            sample["containers"] = parse_docker_stats(stats, self.project)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
        try:
            rows = self.psql(
                "SELECT coalesce(datname, '-'), coalesce(state, '-'), count(*) "
                "FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1, 2"
            )
            sample["connections"] = {f"{d}/{s}": int(n) for d, s, n in rows}
            waits = self.psql(
                "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' "
                "AND wait_event_type IN ('Lock', 'LWLock')"
            )
            sample["lock_waits"] = int(waits[0][0]) if waits else 0
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return sample

    def start(self) -> None:
        self._started = datetime.now(UTC)
        self._qdrant = self.qdrant()
        self._valkey = self.valkey_commands()
        try:
            available = self.psql(
                "SELECT count(*) FROM pg_extension WHERE extname = 'pg_stat_statements'"
            )
            self._statements = bool(available and available[0][0] == "1")
            if self._statements:
                self.psql("SELECT pg_stat_statements_reset()")
            else:
                self.notes.append(
                    "pg_stat_statements is not installed in the postgres database: no "
                    "statement statistics."
                )
        except (OSError, subprocess.SubprocessError) as exc:
            self.notes.append(f"Postgres statistics unavailable: {exc}")
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.samples.append(self.sample())
            self._stop.wait(self.interval_s)

    def statements(self) -> list[dict[str, Any]]:
        if not self._statements:
            return []
        rows = self.psql(
            "SELECT d.datname, s.calls, round(s.total_exec_time::numeric, 1), "
            "round(s.mean_exec_time::numeric, 3), round(s.max_exec_time::numeric, 1), s.rows, "
            "regexp_replace(left(s.query, 400), '\\s+', ' ', 'g') "
            "FROM pg_stat_statements s JOIN pg_database d ON d.oid = s.dbid "
            "ORDER BY s.total_exec_time DESC LIMIT 15"
        )
        return [
            {
                "database": database,
                "calls": int(calls),
                "total_ms": float(total),
                "mean_ms": float(mean),
                "max_ms": float(peak),
                "rows": int(count),
                "query": query,
            }
            for database, calls, total, mean, peak, count, query in rows
        ]

    def access_log(self) -> list[dict[str, Any]]:
        if self._started is None:
            return []
        try:
            output = self.run(["docker", "logs", "--since",
                               self._started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               self.container("api")])  # fmt: skip
        except (OSError, subprocess.SubprocessError) as exc:
            self.notes.append(f"API access log unavailable: {exc}")
            return []
        return summarize_access(parse_access_log(output.splitlines()))

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s + 60)
        summary = summarize_samples(self.samples)
        valkey = self.valkey_commands()
        elapsed = (datetime.now(UTC) - self._started).total_seconds() if self._started else 0
        summary.update(
            {
                "project": self.project,
                "interval_s": self.interval_s,
                "statements": self.statements(),
                "qdrant": qdrant_delta(self._qdrant, self.qdrant()),
                "valkey_commands_per_s": (valkey - self._valkey) / elapsed
                if valkey is not None and self._valkey is not None and elapsed
                else None,
                "api": self.access_log(),
                "notes": self.notes,
            }
        )
        return summary

"""The resource monitor (monitor.py) and its section of the report."""

import json
import time
from typing import Any

import pytest

from workmind_load.monitor import (
    Monitor,
    mebibytes,
    nearest_rank,
    parse_access_log,
    parse_docker_stats,
    qdrant_delta,
    qdrant_snapshot,
    route_of,
    summarize_access,
    summarize_samples,
)
from workmind_load.report import render, resource_use

QDRANT_BEFORE = """# HELP rest_responses_total total number of responses
rest_responses_total{method="POST",endpoint="/collections/{collection_name}/points/query",status="200"} 100
rest_responses_avg_duration_seconds{method="POST",endpoint="/collections/{collection_name}/points/query",status="200"} 0.001
rest_responses_duration_seconds_bucket{method="POST",endpoint="/collections/{collection_name}/points/query",status="200",le="0.001"} 80
rest_responses_duration_seconds_bucket{method="POST",endpoint="/collections/{collection_name}/points/query",status="200",le="0.005"} 100
rest_responses_duration_seconds_bucket{method="POST",endpoint="/collections/{collection_name}/points/query",status="200",le="+Inf"} 100
rest_responses_total{method="POST",endpoint="/collections/{collection_name}/points/query",status="400"} 3
"""  # noqa: E501

QDRANT_AFTER = """rest_responses_total{method="POST",endpoint="/collections/{collection_name}/points/query",status="200"} 300
rest_responses_avg_duration_seconds{method="POST",endpoint="/collections/{collection_name}/points/query",status="200"} 0.002
rest_responses_duration_seconds_bucket{method="POST",endpoint="/collections/{collection_name}/points/query",status="200",le="0.001"} 100
rest_responses_duration_seconds_bucket{method="POST",endpoint="/collections/{collection_name}/points/query",status="200",le="0.005"} 290
rest_responses_duration_seconds_bucket{method="POST",endpoint="/collections/{collection_name}/points/query",status="200",le="+Inf"} 300
"""  # noqa: E501


def test_sizes_in_mebibytes() -> None:
    assert mebibytes("272.2MiB") == pytest.approx(272.2)
    assert mebibytes("1.5GiB") == pytest.approx(1536)
    assert mebibytes("512KiB") == pytest.approx(0.5)
    assert mebibytes("0B") == 0
    assert mebibytes("garbage") == 0


def test_docker_stats_keep_only_the_projects_containers() -> None:
    lines = [
        {"Name": "workmind-api-1", "CPUPerc": "143.20%", "MemUsage": "512MiB / 15.6GiB"},
        {"Name": "workmind-worker-1", "CPUPerc": "0.00%", "MemUsage": "1GiB / 15.6GiB"},
        {"Name": "otherproject-db", "CPUPerc": "99%", "MemUsage": "1GiB / 15.6GiB"},
    ]
    output = "\n".join(json.dumps(line) for line in lines) + "\n"

    found = parse_docker_stats(output, "workmind")

    assert found == {
        "api": {"cpu": 143.2, "mem_mib": 512.0},
        "worker": {"cpu": 0.0, "mem_mib": 1024.0},
    }


def test_access_log_routes_without_ids() -> None:
    run = "01a1d11a-de06-710e-af9a-b24e43036cce"
    lines = [
        json.dumps(
            {
                "method": "GET",
                "path": f"/api/v1/runs/{run}/events",
                "status": 200,
                "duration_ms": 3000.5,
                "logger": "workmind.access",
            }
        ),
        json.dumps({"event": "HTTP Request: GET http://qdrant:6333", "logger": "httpx"}),
        'not json, "workmind.access"',
        json.dumps(
            {
                "method": "POST",
                "path": "/api/v1/search",
                "status": 500,
                "duration_ms": 12,
                "logger": "workmind.access",
            }
        ),
    ]

    entries = parse_access_log(lines)

    assert route_of(f"/api/v1/documents/{run}?x=1") == "/api/v1/documents/{id}"
    assert entries == [
        ("GET /api/v1/runs/{id}/events", 200, 3000.5),
        ("POST /api/v1/search", 500, 12.0),
    ]


def test_access_summary_per_route_busiest_first() -> None:
    entries = [("GET /a", 200, float(ms)) for ms in range(1, 101)] + [("POST /b", 503, 6000.0)]

    rows = summarize_access(entries)

    assert [r["route"] for r in rows] == ["POST /b", "GET /a"]  # 6 s of total time, then 5.05 s
    a = next(r for r in rows if r["route"] == "GET /a")
    assert (a["requests"], a["errors"], a["p50"], a["p95"], a["p99"], a["max"]) == (
        100,
        0,
        50,
        95,
        99,
        100,
    )
    assert rows[0]["errors"] == 1
    assert nearest_rank([], 0.5) == 0


def test_qdrant_requests_and_latency_during_the_run_only() -> None:
    rows = qdrant_delta(qdrant_snapshot(QDRANT_BEFORE), qdrant_snapshot(QDRANT_AFTER))

    assert len(rows) == 1
    row = rows[0]
    assert row["endpoint"] == "POST /collections/{collection_name}/points/query"
    assert row["requests"] == 200
    # 300 * 2 ms - 100 * 1 ms = 500 ms over 200 requests
    assert row["mean_ms"] == pytest.approx(2.5)
    # 20 of the 200 under 1 ms, 190 under 5 ms: p50 and p95 in the 5 ms bucket, p99 above it
    assert (row["p50_le_ms"], row["p95_le_ms"]) == (5.0, 5.0)
    assert row["p99_le_ms"] == float("inf")


def test_samples_summarized_per_container_and_database() -> None:
    samples: list[dict[str, Any]] = [
        {
            "containers": {"api": {"cpu": 100.0, "mem_mib": 300.0}},
            "connections": {"workmind/active": 3, "workmind/idle": 5, "openfga/idle": 10},
            "lock_waits": 0,
        },
        {
            "containers": {"api": {"cpu": 200.0, "mem_mib": 250.0}},
            "connections": {"workmind/active": 7, "workmind/idle": 2, "openfga/idle": 10},
            "lock_waits": 2,
        },
    ]

    summary = summarize_samples(samples)

    assert summary["containers"]["api"] == {
        "cpu_mean": 150.0,
        "cpu_peak": 200.0,
        "mem_peak_mib": 300.0,
    }
    assert summary["connections_peak"]["workmind/active"] == 7
    assert summary["connections_peak"]["workmind/all"] == 9  # 3+5 then 7+2
    assert summary["lock_waits_peak"] == 2


class FakeDocker:
    """Answers the monitor's docker commands like a small stack would."""

    def __init__(self, statements: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.statements = statements

    def __call__(self, command: list[str]) -> str:
        self.calls.append(command)
        text = " ".join(command)
        if command[:2] == ["docker", "stats"]:
            return json.dumps({"Name": "wm-api-1", "CPUPerc": "50%", "MemUsage": "100MiB / 1GiB"})
        if "valkey-cli" in command:
            return "# Stats\r\ntotal_commands_processed:1000\r\n"
        if command[:2] == ["docker", "logs"]:
            return json.dumps(
                {
                    "method": "GET",
                    "path": "/api/v1/me",
                    "status": 200,
                    "duration_ms": 4,
                    "logger": "workmind.access",
                }
            )
        if "pg_extension" in text:
            return "1\n" if self.statements else "0\n"
        if "pg_stat_statements_reset" in text:
            return "\n"
        if "FROM pg_stat_statements" in text:
            return "workmind\t42\t120.5\t2.869\t30.0\t42\tSELECT 1\n"
        if "wait_event_type" in text:
            return "0\n"
        if "pg_stat_activity" in text:
            return "workmind\tactive\t2\nworkmind\tidle\t4\n"
        raise AssertionError(f"unexpected command {command}")


def test_the_monitor_collects_a_run_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker()
    monitor = Monitor("wm", interval_s=0.01, run=docker)
    monkeypatch.setattr(Monitor, "qdrant", lambda self: {})

    monitor.start()
    while len(monitor.samples) < 2:
        time.sleep(0.005)
    summary = monitor.stop()

    assert docker.calls[1][:3] == ["docker", "exec", "wm-postgres-1"]  # psql in the container
    assert any("pg_stat_statements_reset" in " ".join(c) for c in docker.calls)
    assert summary["containers"]["api"]["cpu_peak"] == 50
    assert summary["connections_peak"]["workmind/all"] == 6
    assert summary["statements"] == [
        {
            "database": "workmind",
            "calls": 42,
            "total_ms": 120.5,
            "mean_ms": 2.869,
            "max_ms": 30.0,
            "rows": 42,
            "query": "SELECT 1",
        }
    ]
    assert summary["api"][0]["route"] == "GET /api/v1/me"
    assert summary["notes"] == []


def test_without_pg_stat_statements_the_monitor_says_so() -> None:
    monitor = Monitor("wm", interval_s=0.01, run=FakeDocker(statements=False))
    monitor.qdrant = lambda: {}  # type: ignore[method-assign]

    monitor.start()
    summary = monitor.stop()

    assert summary["statements"] == []
    assert any("pg_stat_statements is not installed" in n for n in summary["notes"])


def test_the_report_shows_resource_use() -> None:
    resources = {
        "project": "workmind",
        "interval_s": 5,
        "samples": 12,
        "containers": {"api": {"cpu_mean": 95.4, "cpu_peak": 101.0, "mem_peak_mib": 512.0}},
        "connections_peak": {"workmind/all": 9},
        "lock_waits_peak": 0,
        "valkey_commands_per_s": 812.3,
        "api": [
            {
                "route": "POST /api/v1/search",
                "requests": 10,
                "errors": 0,
                "p50": 40.0,
                "p95": 90.0,
                "p99": 95.0,
                "max": 99.0,
                "total_s": 0.5,
            }
        ],
        "statements": [
            {
                "database": "workmind",
                "calls": 5,
                "total_ms": 10.0,
                "mean_ms": 2.0,
                "max_ms": 3.0,
                "rows": 5,
                "query": "SELECT a | b",
            }
        ],
        "qdrant": [
            {
                "endpoint": "POST /q",
                "requests": 7,
                "mean_ms": 1.2,
                "p50_le_ms": 1.0,
                "p95_le_ms": 5.0,
                "p99_le_ms": float("inf"),
            }
        ],
        "notes": ["something was missing"],
    }

    text = "\n".join(resource_use(resources))
    whole = render([], [], {"resources": resources}, "locust.json")

    assert "| api | 95 | 101 | 512 |" in text
    assert "workmind/all 9" in text
    assert "812 commands/s" in text
    assert "| POST /api/v1/search | 10 | 0 | 40 | 90 | 95 | 99 | 0.5 |" in text
    assert "`SELECT a \\| b`" in text
    assert "| POST /q | 7 | 1.2 | <= 1 | <= 5 | over 50 s |" in text
    assert "- something was missing" in text
    assert "## Resource use" in whole

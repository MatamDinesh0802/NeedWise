"""Locust's statistics into the Markdown report."""

import json
from pathlib import Path
from typing import Any

from needwise_load.report import (
    ALL_HTTP,
    load_rows,
    percentile,
    render,
    rows_from_csv,
)


def entry(method: str, name: str, times: dict[int, int], failures: int = 0) -> dict[str, Any]:
    count = sum(times.values())
    return {
        "name": name,
        "method": method,
        "num_requests": count,
        "num_none_requests": 0,
        "num_failures": failures,
        "total_response_time": sum(t * n for t, n in times.items()),
        "max_response_time": max(times),
        "min_response_time": min(times),
        "total_content_length": 0,
        "response_times": {str(t): n for t, n in times.items()},
        "num_reqs_per_sec": {},
        "num_fail_per_sec": {},
        "start_time": 1000.0,
        "last_request_timestamp": 1060.0,
    }


def test_percentiles_follow_locusts_histogram_rule() -> None:
    times = {10: 50, 20: 40, 300: 9, 2000: 1}

    # The smallest time with at most count * share requests above it (as Locust computes).
    assert [percentile(times, 100, p) for p in (0.5, 0.95, 0.99, 1.0)] == [20, 300, 2000, 2000]
    assert percentile({}, 0, 0.5) == 0


def test_json_rows_and_an_http_total_without_the_custom_metrics(tmp_path: Path) -> None:
    prefix = tmp_path / "locust"
    entries = [
        entry("GET", "conversations: list", {10: 9, 40: 1}),
        entry("POST", "search", {100: 4}, failures=1),
        entry("SSE", "chat: time to first token", {1800: 5}),
    ]
    (tmp_path / "locust.json").write_text(json.dumps(entries))

    rows, source = load_rows(prefix)

    by_name = {r.name: r for r in rows}
    assert source == "locust.json"
    assert (by_name["search"].failures, by_name["search"].rps) == (1, 4 / 60)
    total = by_name[ALL_HTTP]
    assert (total.requests, total.failures, total.p50, total.max) == (14, 1, 10, 100)


def test_csv_rows_skip_locusts_mixed_total_and_replace_an_empty_json(tmp_path: Path) -> None:
    path = tmp_path / "locust_stats.csv"
    header = (
        "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,"
        "Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,"
        "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%"
    )
    path.write_text(
        header
        + "\nGET,me,3,0,12,12.5,10,20,100,0.5,0,12,12,12,12,20,20,20,20,20,20,20"
        + "\n,Aggregated,3,0,12,12.5,10,20,100,0.5,0,12,12,12,12,20,20,20,20,20,20,20"
        + "\nSSE,chat: answer complete,0,0,0,0,0,0,0,0,0,"
        + ",".join(["N/A"] * 11)
        + "\n"
    )

    (tmp_path / "locust.json").write_text("[]")  # what --processes leaves (Locust 2.47)

    rows, source = load_rows(tmp_path / "locust")

    assert source == "locust_stats.csv"
    assert rows == rows_from_csv(path)
    assert [(r.type, r.name, r.p95) for r in rows] == [
        ("GET", "me", 20.0),
        ("SSE", "chat: answer complete", 0.0),
    ]


def test_the_report_has_the_tables_failures_and_the_stub_calls(tmp_path: Path) -> None:
    (tmp_path / "locust.json").write_text(
        json.dumps(
            [
                entry("GET", "notifications: poll", {20: 10}),
                entry("SSE", "chat: time to first token", {900: 3, 1500: 1}),
            ]
        )
    )
    rows, source = load_rows(tmp_path / "locust")
    failures = [
        {"Method": "POST", "Name": "search", "Error": "HTTP 502: bad | gateway", "Occurrences": "2"}
    ]
    meta = {
        "label": "smoke",
        "users": 5,
        "spawn_rate": 1,
        "duration": "60s",
        "elapsed_s": 60,
        "weights": {"reader": 10, "chat": 0},
        "stub": {
            "url": "http://127.0.0.1:8490",
            "timing": {"latency_ms": 20, "ttft_ms": 600, "tokens_per_s": 60, "answer_tokens": 120},
            "calls": {"stream": 4, "structured": 3, "structured:PlanOut": 3},
        },
        "notes": ["A note."],
    }

    text = render(rows, failures, meta, source)

    assert "# Load test: smoke" in text
    assert "| Mix (class weights) | reader=10 |" in text
    assert "first token after 600 ms, 60 words/s" in text
    assert "| GET | notifications: poll | 10 | 0 | 0.17 | 20 | 20 | 20 | 20 |" in text
    assert "chat: time to first token: p50 900 ms, p95 1,500 ms" in text
    assert "| POST | search | HTTP 502: bad \\| gateway | 2 |" in text
    assert "| structured (PlanOut 3) | 3 |" in text
    assert "- A note." in text

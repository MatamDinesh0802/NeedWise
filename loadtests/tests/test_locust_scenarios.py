"""The Locust scenarios end to end against a fake WorkMind API (no stack): a few seconds of
headless Locust in a subprocess, then its JSON statistics are checked.

The fake answers just enough of each endpoint for the users to do their work, streams a run's
events like the real API, and records every request. Session-only classes (admin, login,
upload) need Keycloak and are validated against a real stack instead (docs/load-testing.md).
"""

import asyncio
import json
import subprocess
import sys
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from workmind_load.tokens import IssuedToken, TokenFile

LOADTESTS = Path(__file__).resolve().parents[1]
LOGINS = ["alice@acme.test", "bob@acme.test", "carol@acme.test", "hannah.schmidt@acme.test"]


class FakeWorkMind:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str]] = []  # method, path, authorization

    def app(self) -> Starlette:
        async def record(request: Request) -> None:
            self.seen.append(
                (request.method, request.url.path, request.headers.get("authorization", ""))
            )

        def reply(body: object, status: int = 200) -> Callable[[Request], Awaitable[Response]]:
            async def endpoint(request: Request) -> Response:
                await record(request)
                if status == 204:
                    return Response(status_code=204)
                return JSONResponse(body, status)

            return endpoint

        async def new_conversation(request: Request) -> Response:
            await record(request)
            return JSONResponse({"id": str(uuid.uuid4())}, 201)

        async def send(request: Request) -> Response:
            await record(request)
            return JSONResponse({"run_id": str(uuid.uuid4())}, 202)

        async def events(request: Request) -> Response:
            await record(request)

            async def stream() -> AsyncIterator[str]:
                yield "retry: 2000\n\n"
                yield 'event: run.started\ndata: {"run_id": "r"}\n\n'
                yield 'event: retrieval\ndata: {"sources": []}\n\n'
                for word in ("Leave ", "is ", "16 weeks [S1]."):
                    await asyncio.sleep(0.02)
                    yield f"event: token\ndata: {json.dumps({'text': word})}\n\n"
                yield 'event: citation\ndata: {"marker": "S1"}\n\n'
                yield 'event: run.completed\ndata: {"usage": {}}\n\n'

            return StreamingResponse(stream(), media_type="text/event-stream")

        return Starlette(
            routes=[
                Route("/api/v1/conversations", reply({"items": [], "next_cursor": None})),
                Route("/api/v1/conversations", new_conversation, methods=["POST"]),
                Route("/api/v1/conversations/{id}", reply({"messages": []})),
                Route("/api/v1/conversations/{id}", reply(None, 204), methods=["DELETE"]),
                Route("/api/v1/conversations/{id}/messages", send, methods=["POST"]),
                Route("/api/v1/runs/{id}/events", events),
                Route(
                    "/api/v1/collections",
                    reply({"items": [{"id": "k1", "key": "kb"}, {"id": "k2", "key": "uploads"}]}),
                ),
                Route("/api/v1/collections/{id}/documents", reply({"items": [{"id": "d1"}]})),
                Route("/api/v1/documents/{id}", reply({"id": "d1", "status": "ready"})),
                Route("/api/v1/search", reply({"passages": []}), methods=["POST"]),
                Route("/api/v1/me", reply({"email": "x"})),
                Route("/api/v1/agents", reply([])),
                Route("/api/v1/usage/summary", reply({"totals": {}})),
                Route("/api/v1/notifications", reply([])),
                Route("/api/v1/approvals", reply([])),
                Route("/api/v1/analytics/model", reply({"metrics": []})),
                Route("/api/v1/analytics/runs", reply([])),
                Route("/api/v1/analytics/ask", reply({"status": "answered"}), methods=["POST"]),
                Route("/api/v1/analytics/query", reply({"rows": []}), methods=["POST"]),
            ]
        )


@pytest.fixture
def fake() -> Iterator[tuple[FakeWorkMind, str]]:
    workmind = FakeWorkMind()
    server = uvicorn.Server(
        uvicorn.Config(workmind.app(), host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        thread.join(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield workmind, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


MIX = "reader=1,poller=1,chat=1,agents=1,analyst=1,admin=0,login=0,upload=0"


def run_locust(host: str, tmp_path: Path, *options: str) -> dict[str, dict[str, Any]]:
    """Four seconds of headless Locust with tokens for LOGINS; its JSON statistics by name."""
    tokens = tmp_path / "tokens.json"
    issued = [IssuedToken(email, f"id-{i}", f"token-{i}") for i, email in enumerate(LOGINS)]
    TokenFile(host, "loadtest-test", "now", issued).save(tokens)
    out = tmp_path / "locust"
    command = [
        sys.executable, "-m", "locust", "-f", str(LOADTESTS / "locustfile.py"), "--headless",
        "--host", host, "-u", "5", "-r", "5", "-t", "4s", "--json-file", str(out),
        "--only-summary", "--stop-timeout", "5", "--exit-code-on-error", "0",
        "--wm-tokens", str(tokens), "--wm-think", "0.05,0.1", "--wm-mix", MIX, *options,
    ]  # fmt: skip
    finished = subprocess.run(  # noqa: S603  (fixed arguments)
        command, cwd=LOADTESTS, capture_output=True, text=True, timeout=60, check=False
    )
    assert finished.returncode == 0, finished.stderr[-2000:]
    return {e["name"]: e for e in json.loads(out.with_suffix(".json").read_text())}


def test_the_token_users_run_their_scenarios(
    fake: tuple[FakeWorkMind, str], tmp_path: Path
) -> None:
    workmind, host = fake

    stats = run_locust(host, tmp_path)

    assert {n: e["num_failures"] for n, e in stats.items() if e["num_failures"]} == {}
    for name in (
        "conversations: list",
        "search",
        "chat: send question",
        "chat: time to first token",
        "chat: answer complete",
        "insights: ask",
        "insights: re-run query",
    ):
        assert stats[name]["num_requests"] > 0, name
    assert stats["chat: time to first token"]["method"] == "SSE"
    assert stats["chat: time to first token"]["min_response_time"] >= 20  # the first token waits
    assert {"agents: answer complete", "research: answer complete"} & set(stats)
    created = sum(1 for m, p, _ in workmind.seen if m == "POST" and p == "/api/v1/conversations")
    deleted = sum(1 for m, p, _ in workmind.seen if m == "DELETE")
    assert created == deleted > 0  # each user deletes its conversations when it stops
    assert {a for _, _, a in workmind.seen} <= {f"Bearer token-{i}" for i in range(len(LOGINS))}


def test_without_a_model_nothing_asks(fake: tuple[FakeWorkMind, str], tmp_path: Path) -> None:
    workmind, host = fake

    stats = run_locust(host, tmp_path, "--wm-no-model")

    asked = {p for m, p, _ in workmind.seen if m == "POST" and p.endswith(("/messages", "/ask"))}
    assert asked == set()
    assert not [n for n in stats if n.endswith(("send question", "time to first token"))]
    assert stats["insights: re-run query"]["num_requests"] > 0  # the analyst still works
    assert stats["search"]["num_requests"] > 0


def test_readers_leave_the_upload_collection_alone(
    fake: tuple[FakeWorkMind, str], tmp_path: Path
) -> None:
    """Its documents are deleted mid-run: opening one later would be a correct 404."""
    workmind, host = fake

    stats = run_locust(host, tmp_path, "--wm-no-model", "--wm-upload-collection", "uploads")

    assert stats["documents: list"]["num_requests"] > 0
    paths = {p for _, p, _ in workmind.seen}
    assert "/api/v1/collections/k1/documents" in paths
    assert "/api/v1/collections/k2/documents" not in paths

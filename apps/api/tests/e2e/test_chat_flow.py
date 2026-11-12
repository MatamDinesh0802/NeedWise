"""Chat through Caddy: a run's events must stream (not buffer) to the browser.

The guest's question needs no model: nothing she may read matches it, so it stays under the
default relevance floor (RETRIEVAL_MIN_SIMILARITY=0.25) with EmbeddingGemma and with CI's hash
embeddings alike (no word in common with her collection), and it runs without a model key. With
the floor off (0) her one collection always matches and the question goes to the model.
Set E2E_MODEL=1 to also ask a real question of the configured model (costs a few cents).
"""

import json
import os
import time

import httpx
import pytest

from tests.e2e.conftest import same_origin_headers, sign_in

TERMINAL = ("run.completed", "run.failed", "run.cancelled")


def _ask(client: httpx.Client, question: str) -> tuple[str, str]:
    conversation = client.post("/api/v1/conversations", json={}, headers=same_origin_headers())
    assert conversation.status_code == 201, conversation.text
    started = client.post(
        f"/api/v1/conversations/{conversation.json()['id']}/messages",
        json={"content": question},
        headers=same_origin_headers(),
    )
    assert started.status_code == 202, started.text
    return conversation.json()["id"], started.json()["run_id"]


def _events(client: httpx.Client, run_id: str) -> list[tuple[str, dict[str, object], float]]:
    received: list[tuple[str, dict[str, object], float]] = []
    began, name = time.monotonic(), ""
    with client.stream("GET", f"/api/v1/runs/{run_id}/events", timeout=120) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                received.append((name, json.loads(line[6:]), time.monotonic() - began))
                if name in TERMINAL:
                    break
    return received


def test_a_guest_question_streams_through_the_proxy(browser: httpx.Client) -> None:
    sign_in(browser, "mallory@acme.test")
    _, run_id = _ask(browser, "L3 salary bands")

    received = _events(browser, run_id)

    names = [name for name, _, _ in received]
    assert names[0] == "run.started"
    assert "retrieval" in names  # after an optional "plan" in auto or agents mode
    assert received[-1][0] == "run.completed"
    assert received[-1][1]["abstained"] is True


@pytest.mark.skipif(os.environ.get("E2E_MODEL") != "1", reason="set E2E_MODEL=1 (uses the model)")
def test_a_real_answer_streams_token_by_token_with_citations(browser: httpx.Client) -> None:
    sign_in(browser, "alice@acme.test")
    conversation, run_id = _ask(browser, "How many weeks of parental leave do we get?")

    received = _events(browser, run_id)
    tokens = [at for name, _, at in received if name == "token"]
    stored = browser.get(f"/api/v1/conversations/{conversation}").json()["messages"][-1]

    assert received[-1][0] == "run.completed", received[-1]
    assert len(tokens) > 5
    assert tokens[-1] - tokens[0] > 0.2  # spread over time: streamed, not buffered at the end
    assert "16" in stored["content"]
    assert any(c["filename"] == "leave-policy-2026.pdf" for c in stored["citations"])
    browser.delete(f"/api/v1/conversations/{conversation}", headers=same_origin_headers())

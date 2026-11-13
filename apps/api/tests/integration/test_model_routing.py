"""The data policy end to end: real retrieval (Qdrant, OpenFGA, both locks), then routing by the
tenant's threshold. Sources above it never reach an external model: they go to the tenant's
local model, or are left out and the person is told.

Needs TEST_QDRANT_URL, TEST_VALKEY_URL and TEST_OPENFGA_URL (+ TEST_OPENFGA_API_KEY).
"""

from typing import Any

from fastapi.testclient import TestClient

from needwise.modules.model_catalog.resolver import LLMResolver
from tests.integration.test_assistant_api import (  # noqa: F401  (fixtures)
    FakeLLM,
    api,
    corpus,
    drop_test_collection,
    fga,
    model_name,
    pytestmark,
    synced,
)
from tests.integration.test_model_catalog_api import (
    BASE,
    ScriptedProviders,
    add_model,
    add_provider,
    put,
)

assert pytestmark  # the same requirements as the assistant tests


def ask(api: TestClient, acme: Any, who: str, question: str) -> dict[str, Any]:  # noqa: F811
    response = api.post(
        "/api/v1/ask", json={"question": question}, headers=acme.as_(who, write=True)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_sources_above_the_threshold_never_reach_an_external_model(
    api: TestClient,  # noqa: F811
    corpus: tuple[Any, str],  # noqa: F811
) -> None:
    acme, _ = corpus
    app: Any = api.app
    system = FakeLLM()  # the deployment's external model
    app.state.resources.llm = system
    scripted = ScriptedProviders()
    models: LLMResolver = app.state.resources.models
    models.build = scripted

    # Acme allows everything to go out (restricted_data: allow): the handbook reaches Claude.
    before = ask(api, acme, "alice", "How long is parental leave?")
    assert (before["abstained"], before["withheld"], len(system.requests)) == (False, 0, 1)

    # Nothing above "public" may go out, and there is no local model: left out, and said so.
    assert (
        put(api, acme, "policy", {"max_external_sensitivity": "public", "local_model": None})
    ).status_code == 200
    kept_out = ask(api, acme, "alice", "How long is parental leave?")
    assert kept_out["abstained"] is True
    assert kept_out["withheld"] >= 1
    assert kept_out["sources"] == []
    assert "internal, confidential and restricted data" in kept_out["answer"]
    assert len(system.requests) == 1  # the external model was not called again

    # With a local model, the same question is answered there, with its sources.
    local = add_provider(
        api, acme, name="On-prem", base_url="http://gpu-box.internal:11434/v1", local=True
    )
    local_model = add_model(api, acme, local["id"], "qwen3:14b")
    policy = {"max_external_sensitivity": "public", "local_model": local_model}
    assert put(api, acme, "policy", policy).status_code == 200
    routed = ask(api, acme, "alice", "How long is parental leave?")
    on_prem = scripted.built[("On-prem", "qwen3:14b")].llm

    assert routed["withheld"] == 0
    assert routed["answer"] == "On-prem/qwen3:14b"
    assert "leave.md" in {s["filename"] for s in routed["sources"]}  # all sent to on-prem
    assert on_prem.roles() == ["answer"]
    assert len(system.requests) == 1
    assert api.get(BASE, headers=acme.as_("erin")).json()["policy"] == policy

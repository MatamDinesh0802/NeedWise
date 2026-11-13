"""Admin > Models over real HTTP on seeded Acme tenants (PostgreSQL, Valkey), with scripted
models: who may manage models, write-only keys (never in a response, an audit event or a log
line), base URL checks, models in use, per-tenant resolution with two tenants, invalidation
across processes, the data policy, the provider test, key rotation and tenant isolation."""

import base64
import json
import logging
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from needwise.core.config import DEV_SECRETS_ENCRYPTION_KEY, Settings
from needwise.core.tenancy import tenant_transaction
from needwise.modules.agents.model_choice import CatalogModels
from needwise.modules.governance.models import AuditEvent
from needwise.modules.model_catalog.catalog import ProviderInfo
from needwise.modules.model_catalog.models import ModelProvider
from needwise.modules.model_catalog.resolver import LLMResolver
from needwise.modules.model_catalog.rotation import rotate_keys
from needwise.modules.model_catalog.secrets import KeyRing
from needwise.providers.llm import LLMError, LLMMessage, LLMProvider, LLMRequest
from tests.fakes import FakeLLM
from tests.integration.conftest import VALKEY_URL, Acme, SessionFactory, seed_acme

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL to run")

KEY = "sk-live-" + "7Q4p" * 6 + "zX9w"  # what an admin pastes; must never come back
OTHER_KEY = "sk-other-" + "Lm3n" * 6 + "Rt5y"
BASE = "/api/v1/admin/models"
REQUEST = LLMRequest(system="s", messages=[LLMMessage("user", "q")])


def app_of(api: TestClient) -> Any:
    return api.app


def resolver_of(api: TestClient) -> LLMResolver:
    return app_of(api).state.resources.models


@dataclass
class Built:
    llm: FakeLLM
    api_key: str | None


class ScriptedProviders:
    """Stands in for the network: each tenant model gets its own scripted model, and we keep
    which key each provider's client was given."""

    def __init__(self) -> None:
        self.built: dict[tuple[str, str], Built] = {}

    def __call__(self, provider: ProviderInfo, model: str, api_key: str | None) -> LLMProvider:
        llm = FakeLLM(f"{provider.name}/{model}", external=provider.external, tool_turns=[])
        llm.tool_text = f"answered by {provider.name}/{model}"
        self.built[(provider.name, model)] = Built(llm, api_key)
        return llm


@pytest.fixture
def scripted(api: TestClient) -> ScriptedProviders:
    providers = ScriptedProviders()
    resolver_of(api).build = providers
    app_of(api).state.resources.llm = FakeLLM("system", tool_turns=[], tool_text="by system")
    return providers


def resolver(
    sessions: SessionFactory,
    scripted: ScriptedProviders,
    *,
    ttl_s: float = 0,
    keyring: KeyRing | None = None,
) -> LLMResolver:
    """A resolver of its own on the test's event loop (the app's runs on the TestClient's):
    what another API process or a Celery worker would build."""
    settings = Settings(_env_file=None, app_env="test")  # pyright: ignore[reportCallIssue]
    return LLMResolver(
        sessions,
        settings,
        httpx.AsyncClient(),  # not used: `scripted` builds every tenant model
        keyring or KeyRing.from_settings(settings),
        system=FakeLLM("system"),
        system_local=None,
        ttl_s=ttl_s,
        build=scripted,
    )


class Captured(logging.Handler):
    """Every log line, from every logger, at every level."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        line = f"{record.getMessage()} {record.args!r} {record.__dict__.get('msg')!r}"
        if record.exc_info:
            line += logging.Formatter().formatException(record.exc_info)
        self.lines.append(line)


@pytest.fixture
def logs() -> Iterator[Captured]:
    handler, root = Captured(), logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(level)


def add_provider(api: TestClient, acme: Acme, **body: Any) -> dict[str, Any]:
    payload = {"name": "Team OpenAI", "kind": "openai_compatible", **body}
    payload.setdefault("base_url", "https://llm.example/v1")
    response = api.post(f"{BASE}/providers", json=payload, headers=acme.as_("erin", write=True))
    assert response.status_code == 201, response.text
    return response.json()


def add_model(api: TestClient, acme: Acme, provider_id: str, name: str, **body: Any) -> str:
    response = api.post(
        f"{BASE}/catalog",
        json={"provider_id": provider_id, "name": name, **body},
        headers=acme.as_("erin", write=True),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def put(api: TestClient, acme: Acme, path: str, body: dict[str, Any]) -> Any:
    return api.put(f"{BASE}/{path}", json=body, headers=acme.as_("erin", write=True))


def research(api: TestClient, acme: Acme, who: str = "erin") -> dict[str, Any]:
    response = api.post(
        "/api/v1/research",
        json={"question": "What is new?", "tools": []},
        headers=acme.as_(who, write=True),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def audit_details(sessions: SessionFactory, acme: Acme) -> list[str]:
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        rows = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action.like("model\\_%"), AuditEvent.tenant_id == acme.tenant_id
                )
            )
        ).all()
    return [f"{e.action} {json.dumps(e.details)}" for e in rows]


# --- Who may manage models, and what a tenant gets by default -------------------------------


async def test_only_owners_and_admins_manage_models(api: TestClient, acme: Acme) -> None:
    assert api.get(BASE, headers=acme.as_("erin")).status_code == 200  # admin
    assert api.get(BASE, headers=acme.as_("priya.raman")).status_code == 200  # owner
    for person in ("bob", "amara.diallo", "mallory"):  # member, auditor, guest
        assert api.get(BASE, headers=acme.as_(person)).status_code == 403, person
        refused = api.post(
            f"{BASE}/providers",
            json={"name": "x", "kind": "anthropic", "api_key": KEY},
            headers=acme.as_(person, write=True),
        )
        assert refused.status_code == 403, person
        assert KEY not in refused.text


async def test_a_tenant_that_chose_nothing_gets_the_deployment_defaults(
    api: TestClient, acme: Acme
) -> None:
    catalog = api.get(BASE, headers=acme.as_("erin")).json()

    assert set(catalog) == {"providers", "models", "roles", "policy", "defaults"}
    system = catalog["providers"][0]
    assert system["id"] == "system"
    assert (system["system"], system["kind"], system["key_hint"]) == (True, "anthropic", None)
    sonnet = next(m for m in catalog["models"] if m["id"] == "system:claude-sonnet-5-5")
    assert (sonnet["input_usd_per_mtok"], sonnet["output_usd_per_mtok"]) == (2.0, 10.0)
    assert set(catalog["roles"].values()) == {"system:claude-sonnet-5-5"}
    assert catalog["roles"] == catalog["defaults"]["roles"]
    # Acme's restricted_data is "allow": everything may go to the external model, as before.
    assert catalog["policy"] == {"max_external_sensitivity": "restricted", "local_model": None}


# --- Keys are write-only --------------------------------------------------------------------


async def test_keys_never_appear_in_responses_audit_events_or_logs(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, logs: Captured
) -> None:
    def ok_reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    resolver_of(api).probe_transport = httpx.MockTransport(ok_reply)
    responses: list[str] = []
    created = add_provider(api, acme, api_key=KEY)
    responses.append(json.dumps(created))
    assert (created["has_key"], created["key_hint"]) == (True, "…" + KEY[-4:])
    pid = created["id"]
    add_model(api, acme, pid, "gpt-team")

    for response in (
        api.get(BASE, headers=acme.as_("erin")),
        api.patch(
            f"{BASE}/providers/{pid}",
            json={"name": "Renamed"},
            headers=acme.as_("erin", write=True),
        ),
        api.post(f"{BASE}/providers/{pid}/test", headers=acme.as_("erin", write=True)),
        # Invalid requests: Pydantic would echo the input, keys included.
        api.post(
            f"{BASE}/providers",
            json={"kind": "openai_compatible", "api_key": KEY},
            headers=acme.as_("erin", write=True),
        ),
        api.post(
            f"{BASE}/providers",
            json={"name": "x", "kind": "openai_compatible", "api_key": KEY + " with spaces"},
            headers=acme.as_("erin", write=True),
        ),
        api.patch(
            f"{BASE}/providers/{pid}",
            json={"api_key": KEY, "unknown": 1},
            headers=acme.as_("erin", write=True),
        ),
    ):
        responses.append(response.text)
    renamed = api.get(BASE, headers=acme.as_("erin")).json()
    provider = next(p for p in renamed["providers"] if p["id"] == pid)

    assert provider["name"] == "Renamed"
    assert (provider["has_key"], provider["key_hint"]) == (True, "…" + KEY[-4:])  # kept
    assert all(KEY not in r and KEY[:12] not in r for r in responses)
    events = await audit_details(app_sessions, acme)
    assert any("model_provider.created" in e and '"key": "set"' in e for e in events)
    assert any("model_provider.tested" in e and '"ok": true' in e for e in events)
    assert all(KEY not in e and KEY[-4:] not in e for e in events)
    assert logs.lines  # the capture works
    assert not [line for line in logs.lines if KEY in line or KEY[:12] in line]
    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        stored = await session.get(ModelProvider, uuid.UUID(pid))
    assert stored is not None
    assert stored.api_key is not None
    assert KEY.encode() not in stored.api_key  # encrypted at rest


async def test_keys_are_kept_replaced_or_cleared_and_follow_their_url(
    api: TestClient, acme: Acme, scripted: ScriptedProviders
) -> None:
    pid = add_provider(api, acme, api_key=KEY)["id"]
    model = add_model(api, acme, pid, "gpt-team")
    assert put(api, acme, "roles", {"agent": model}).status_code == 200
    research(api, acme)
    assert scripted.built[("Team OpenAI", "gpt-team")].api_key == KEY

    url_only = api.patch(
        f"{BASE}/providers/{pid}",
        json={"base_url": "https://elsewhere.example/v1"},
        headers=acme.as_("erin", write=True),
    )
    assert url_only.status_code == 422
    assert url_only.json()["code"] == "key_required"  # a key never follows a new URL
    replaced = api.patch(
        f"{BASE}/providers/{pid}",
        json={"base_url": "https://elsewhere.example/v1", "api_key": OTHER_KEY},
        headers=acme.as_("erin", write=True),
    ).json()
    assert (replaced["base_url"], replaced["key_hint"]) == (
        "https://elsewhere.example/v1",
        "…" + OTHER_KEY[-4:],
    )
    research(api, acme)
    assert scripted.built[("Team OpenAI", "gpt-team")].api_key == OTHER_KEY

    cleared = api.patch(
        f"{BASE}/providers/{pid}", json={"clear_key": True}, headers=acme.as_("erin", write=True)
    ).json()
    assert (cleared["has_key"], cleared["key_hint"]) == (False, None)
    research(api, acme)
    assert scripted.built[("Team OpenAI", "gpt-team")].api_key is None


async def test_unsafe_base_urls_and_impossible_providers_are_refused(
    api: TestClient, acme: Acme
) -> None:
    for url in (
        "http://169.254.169.254/latest",
        "http://metadata.google.internal/v1",
        "file:///etc/passwd",
        "https://user:secret@llm.example/v1",
    ):
        response = api.post(
            f"{BASE}/providers",
            json={
                "name": f"p-{uuid.uuid4().hex[:6]}",
                "kind": "openai_compatible",
                "base_url": url,
            },
            headers=acme.as_("erin", write=True),
        )
        assert response.status_code == 422, url
        assert response.json()["code"] == "unsafe_base_url", url
    no_url = api.post(
        f"{BASE}/providers",
        json={"name": "No URL", "kind": "openai_compatible"},
        headers=acme.as_("erin", write=True),
    )
    local_anthropic = api.post(
        f"{BASE}/providers",
        json={"name": "Claude", "kind": "anthropic", "local": True},
        headers=acme.as_("erin", write=True),
    )
    assert no_url.json()["code"] == "base_url_required"
    assert local_anthropic.json()["code"] == "not_local"
    add_provider(api, acme, name="Twice")
    twice = api.post(
        f"{BASE}/providers",
        json={"name": "Twice", "kind": "openai_compatible", "base_url": "https://a.example/v1"},
        headers=acme.as_("erin", write=True),
    )
    assert twice.status_code == 409


# --- The catalog: prices, models in use -------------------------------------------------------


async def test_models_in_use_cannot_be_deleted_or_disabled(
    api: TestClient, acme: Acme, owner_sessions: SessionFactory
) -> None:
    pid = add_provider(api, acme)["id"]
    model = add_model(
        api, acme, pid, "org/llama-4:70b", input_usd_per_mtok=0.5, output_usd_per_mtok=1.5
    )
    assert model == f"{pid}:org/llama-4:70b"  # ids carry names with "/" and ":"
    priced = api.patch(
        f"{BASE}/catalog/{model}",
        json={"output_usd_per_mtok": 2.0, "label": "Llama 4"},
        headers=acme.as_("erin", write=True),
    ).json()
    assert (priced["label"], priced["input_usd_per_mtok"], priced["output_usd_per_mtok"]) == (
        "Llama 4",
        0.5,
        2.0,
    )
    roles = put(api, acme, "roles", {"judge": model}).json()["roles"]
    assert roles["judge"] == model
    assert roles["answer"] == "system:claude-sonnet-5-5"  # unchanged roles keep the default

    for method, path, body in (
        ("DELETE", f"catalog/{model}", None),
        ("DELETE", f"providers/{pid}", None),
        ("PATCH", f"catalog/{model}", {"enabled": False}),
        ("PATCH", f"providers/{pid}", {"enabled": False}),
    ):
        refused = api.request(
            method, f"{BASE}/{path}", json=body, headers=acme.as_("erin", write=True)
        )
        assert refused.status_code == 409, path
        assert refused.json()["code"] == "model_in_use"
        assert "the judge role" in refused.json()["detail"]

    # An agent that runs on it (Admin > Agents): accepted because the catalog offers it, and
    # from then on the model can't be deleted or disabled under the agent.
    assert put(api, acme, "roles", {}).status_code == 200  # roles back to the defaults
    agent = api.post(
        "/api/v1/admin/agents",
        json={
            "key": "contracts",
            "name": "Contracts agent",
            "description": "Contracts, NDAs and who may sign them.",
            "collections": ["company-handbook"],
            "model": model,
        },
        headers=acme.as_("erin", write=True),
    )
    assert agent.status_code == 201, agent.text
    by_agent = api.delete(f"{BASE}/catalog/{model}", headers=acme.as_("erin", write=True))
    assert by_agent.status_code == 409
    assert "the agent 'Contracts agent'" in by_agent.json()["detail"]
    disabled = api.patch(
        f"{BASE}/providers/{pid}", json={"enabled": False}, headers=acme.as_("erin", write=True)
    )
    assert disabled.status_code == 409
    assert (
        api.delete("/api/v1/admin/agents/contracts", headers=acme.as_("erin", write=True))
    ).status_code == 204
    # Once disabled, no agent can be given it.
    assert (
        api.patch(
            f"{BASE}/catalog/{model}", json={"enabled": False}, headers=acme.as_("erin", write=True)
        ).status_code
        == 200
    )
    refused_agent = api.post(
        "/api/v1/admin/agents",
        json={
            "key": "contracts",
            "name": "Contracts agent",
            "description": "Contracts, NDAs and who may sign them.",
            "collections": ["company-handbook"],
            "model": model,
        },
        headers=acme.as_("erin", write=True),
    )
    assert refused_agent.status_code == 422
    assert refused_agent.json()["code"] == "model_disabled"

    assert (
        api.delete(f"{BASE}/catalog/{model}", headers=acme.as_("erin", write=True)).status_code
        == 204
    )
    assert (
        api.delete(f"{BASE}/providers/{pid}", headers=acme.as_("erin", write=True)).status_code
        == 204
    )
    system = api.delete(
        f"{BASE}/catalog/system:claude-sonnet-5-5", headers=acme.as_("erin", write=True)
    )
    assert system.status_code == 409
    assert system.json()["code"] == "read_only"
    unknown = put(api, acme, "roles", {"answer": "nope:model"})
    assert unknown.status_code == 422


# --- Resolution per tenant ------------------------------------------------------------------


async def test_each_tenant_gets_its_own_models(
    api: TestClient,
    acme: Acme,
    owner_sessions: SessionFactory,
    app_sessions: SessionFactory,
    scripted: ScriptedProviders,
) -> None:
    globex = await seed_acme(owner_sessions)  # a second, separate tenant
    before = research(api, acme)
    assert before["answer"] == "by system"  # nothing chosen: the deployment's model

    alpha = add_provider(api, acme, name="Alpha", base_url="https://alpha.example/v1", api_key=KEY)
    alpha_model = add_model(api, acme, alpha["id"], "alpha-1")
    put(api, acme, "roles", {"agent": alpha_model, "router": alpha_model})
    beta = add_provider(
        api, globex, name="Beta", base_url="https://beta.example/v1", api_key=OTHER_KEY
    )
    beta_model = add_model(
        api, globex, beta["id"], "beta-1", input_usd_per_mtok=1.0, output_usd_per_mtok=3.0
    )
    put(api, globex, "roles", {"agent": beta_model})

    assert research(api, acme)["answer"] == "answered by Alpha/alpha-1"
    assert research(api, globex)["answer"] == "answered by Beta/beta-1"
    assert scripted.built[("Alpha", "alpha-1")].api_key == KEY  # each key to its own provider
    assert scripted.built[("Beta", "beta-1")].api_key == OTHER_KEY
    # One tenant cannot use another's models, even by naming them.
    assert put(api, globex, "roles", {"agent": alpha_model}).status_code == 422

    resolved = await resolver(app_sessions, scripted).for_tenant(globex.tenant_id)
    _, usage = await resolved.complete("agent", REQUEST)
    assert (usage.catalog_model, usage.price_per_mtok) == (beta_model, (1.0, 3.0))
    assert resolved.model_for("router") == "system:claude-sonnet-5-5"
    agent_model = resolved.using("system:claude-opus-5-5")
    assert agent_model.model_for("answer") == "system:claude-opus-5-5"  # a per-request override
    assert resolved.using("nope:model") is resolved


async def test_an_agent_runs_on_its_catalog_model_until_that_model_is_disabled(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, scripted: ScriptedProviders
) -> None:
    """What the orchestrator and the test console do for an agent with its own model: the
    tenant's resolution, then `CatalogModels`. Nothing falls back to another model."""
    pid = add_provider(api, acme, name="Alpha", base_url="https://alpha.example/v1", api_key=KEY)
    model = add_model(api, acme, pid["id"], "alpha-legal")
    models = CatalogModels()

    resolved = await resolver(app_sessions, scripted).for_tenant(acme.tenant_id)
    agent_llm = await models.agent_llm(acme.tenant_id, model, resolved)
    _, usage = await agent_llm.complete("agent", REQUEST)
    assert scripted.built[("Alpha", "alpha-legal")].llm.roles() == ["agent"]
    assert usage.catalog_model == model
    assert await models.agent_llm(acme.tenant_id, None, resolved) is resolved  # role default

    disabled = api.patch(
        f"{BASE}/catalog/{model}", json={"enabled": False}, headers=acme.as_("erin", write=True)
    )
    assert disabled.status_code == 200
    fresh = await resolver(app_sessions, scripted).for_tenant(acme.tenant_id)
    with pytest.raises(LLMError, match="not an enabled model"):
        await models.agent_llm(acme.tenant_id, model, fresh)


async def test_a_change_reaches_other_processes_through_the_settings_version(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, scripted: ScriptedProviders
) -> None:
    elsewhere = resolver(app_sessions, scripted, ttl_s=3600)
    assert (await elsewhere.for_tenant(acme.tenant_id)).model_for("agent").startswith("system:")

    pid = add_provider(api, acme, api_key=KEY)["id"]
    model = add_model(api, acme, pid, "gpt-team")
    assert put(api, acme, "roles", {"agent": model}).status_code == 200

    stale = await elsewhere.for_tenant(acme.tenant_id)
    assert stale.model_for("agent").startswith("system:")  # still within its cache time
    elsewhere.ttl_s = 0  # the time has passed: one version query, then a reload
    fresh = await elsewhere.for_tenant(acme.tenant_id)
    assert fresh.model_for("agent") == model
    again = await elsewhere.for_tenant(acme.tenant_id)
    assert again.model_for("agent") == model  # same version: the cached resolution is reused


# --- The data policy ------------------------------------------------------------------------


async def test_the_data_policy_needs_a_local_model_and_drives_routing(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, scripted: ScriptedProviders
) -> None:
    cloud = add_provider(api, acme, name="Cloud", api_key=KEY)
    cloud_model = add_model(api, acme, cloud["id"], "cloud-1")
    local = add_provider(
        api, acme, name="On-prem", base_url="http://gpu-box.internal:11434/v1", local=True
    )
    local_model = add_model(api, acme, local["id"], "qwen3:14b")

    for chosen in (cloud_model, "system:claude-sonnet-5-5"):
        refused = put(
            api, acme, "policy", {"max_external_sensitivity": "internal", "local_model": chosen}
        )
        assert refused.status_code == 422
        assert refused.json()["code"] == "not_local"
    policy = put(
        api, acme, "policy", {"max_external_sensitivity": "internal", "local_model": local_model}
    ).json()["policy"]
    assert policy == {"max_external_sensitivity": "internal", "local_model": local_model}

    resolved = await resolver(app_sessions, scripted).for_tenant(acme.tenant_id)
    assert resolved.max_external_sensitivity == "internal"
    assert resolved.local is not None
    assert not resolved.local.external
    assert resolved.local.model_for("answer") == local_model
    await resolved.local.complete("answer", REQUEST)
    assert scripted.built[("On-prem", "qwen3:14b")].llm.roles() == ["answer"]

    for method, path, body in (
        ("PATCH", f"providers/{local['id']}", {"local": False}),
        ("PATCH", f"providers/{local['id']}", {"enabled": False}),
        ("DELETE", f"catalog/{local_model}", None),
    ):
        refused = api.request(
            method, f"{BASE}/{path}", json=body, headers=acme.as_("erin", write=True)
        )
        assert refused.status_code == 409, path

    none = put(
        api, acme, "policy", {"max_external_sensitivity": "confidential", "local_model": None}
    )
    assert none.json()["policy"]["local_model"] is None
    assert (await resolver(app_sessions, scripted).for_tenant(acme.tenant_id)).local is None


# --- Testing a provider ---------------------------------------------------------------------


async def test_the_provider_test_reports_plainly_without_echoing_the_key(
    api: TestClient, acme: Acme
) -> None:
    def server(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "down.example":
            raise httpx.ConnectError("connection refused")
        if host == "bad.example":
            return httpx.Response(
                401,
                json={
                    "error": {
                        "type": "invalid_api_key",
                        "message": f"Incorrect API key provided: {KEY}. Internal host db-7.",
                    }
                },
            )
        if host == "moved.example":
            return httpx.Response(302, headers={"Location": "https://attacker.example/v1"})
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        body = json.loads(request.content)
        assert (body["model"], body["max_tokens"]) == ("gpt-team", 16)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    resolver_of(api).probe_transport = httpx.MockTransport(server)

    def probe(host: str) -> dict[str, Any]:
        pid = add_provider(api, acme, name=host, base_url=f"https://{host}/v1", api_key=KEY)["id"]
        add_model(api, acme, pid, "gpt-team")
        response = api.post(f"{BASE}/providers/{pid}/test", headers=acme.as_("erin", write=True))
        assert response.status_code == 200, response.text
        assert KEY not in response.text
        return response.json()

    ok, bad, down, moved = (
        probe(h) for h in ("ok.example", "bad.example", "down.example", "moved.example")
    )
    assert ok["ok"] is True
    assert ok["error"] is None
    assert ok["latency_ms"] >= 0
    assert ok["model"] == "gpt-team"
    assert bad == {
        "ok": False,
        "latency_ms": bad["latency_ms"],
        "error": "The server refused the API key (HTTP 401). Error code: invalid_api_key.",
        "model": "gpt-team",
    }
    assert "db-7" not in bad["error"]  # the response body never comes back
    assert down["error"] == "Could not connect to the server."
    assert "not followed" in moved["error"]

    empty = add_provider(api, acme, name="Empty", base_url="https://ok.example/v1")["id"]
    nothing = api.post(f"{BASE}/providers/{empty}/test", headers=acme.as_("erin", write=True))
    assert "Add a model" in nothing.json()["error"]


# --- Rotation and isolation -----------------------------------------------------------------


async def test_rotation_moves_stored_keys_to_the_new_key(
    api: TestClient, acme: Acme, app_sessions: SessionFactory, scripted: ScriptedProviders
) -> None:
    pid = add_provider(api, acme, api_key=KEY)["id"]
    model = add_model(api, acme, pid, "gpt-team")
    put(api, acme, "roles", {"agent": model})
    new = base64.b64encode(os.urandom(32)).decode()

    report = await rotate_keys(app_sessions, KeyRing(new, [DEV_SECRETS_ENCRYPTION_KEY]))
    try:
        assert report.rotated >= 1
        assert report.failed == 0
        # A process that still has only the old key cannot use the provider, and says so.
        resolved = await resolver(app_sessions, scripted).for_tenant(acme.tenant_id)
        with pytest.raises(LLMError, match="cannot be decrypted") as failed:
            await resolved.complete("agent", REQUEST)
        assert KEY not in str(failed.value)
        again = await rotate_keys(app_sessions, KeyRing(new, [DEV_SECRETS_ENCRYPTION_KEY]))
        assert again.rotated == 0
        assert again.current >= 1
    finally:  # back to the development key for the other tests
        await rotate_keys(app_sessions, KeyRing(DEV_SECRETS_ENCRYPTION_KEY, [new]))
    research(api, acme)
    assert scripted.built[("Team OpenAI", "gpt-team")].api_key == KEY


async def test_one_tenant_never_sees_anothers_providers(
    api: TestClient, acme: Acme, owner_sessions: SessionFactory, app_sessions: SessionFactory
) -> None:
    globex = await seed_acme(owner_sessions)
    pid = add_provider(api, acme, name="Acme only", api_key=KEY)["id"]

    other = api.get(BASE, headers=globex.as_("erin")).json()
    assert pid not in {p["id"] for p in other["providers"]}
    patched = api.patch(
        f"{BASE}/providers/{pid}", json={"name": "Taken"}, headers=globex.as_("erin", write=True)
    )
    assert patched.status_code == 404
    probed = api.post(f"{BASE}/providers/{pid}/test", headers=globex.as_("erin", write=True))
    assert probed.status_code == 404
    async with tenant_transaction(app_sessions, globex.tenant_id) as session:
        visible = (await session.scalars(select(ModelProvider))).all()
    assert all(p.tenant_id == globex.tenant_id for p in visible)

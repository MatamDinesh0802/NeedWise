"""Model management without a database: key encryption and rotation, base URL checks, the data
policy and routing by sensitivity, the tenant resolution's call wrapper (usage stamping, key
scrubbing), catalog prices, and keeping keys out of validation errors."""

import base64
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from needwise.core.config import Settings
from needwise.core.errors import redact_errors
from needwise.modules.assistant.answer import route, withheld_notice
from needwise.modules.model_catalog import urls
from needwise.modules.model_catalog.catalog import Catalog, TenantConfig, system_catalog
from needwise.modules.model_catalog.policy import above, default_threshold, threshold
from needwise.modules.model_catalog.resolver import Target, TenantLLM, scrub, with_model
from needwise.modules.model_catalog.secrets import KeyRing, SecretsError
from needwise.modules.model_catalog.store import key_context, key_hint
from needwise.modules.model_catalog.urls import UnsafeUrlError, check_base_url
from needwise.providers.llm import (
    Done,
    LLMError,
    LLMMessage,
    LLMRequest,
    LLMUsage,
    Role,
    StreamEvent,
    TextDelta,
)
from needwise.providers.pricing import cost_usd
from tests.fakes import FakeLLM
from tests.test_assistant_unit import passage

OLD = base64.b64encode(os.urandom(32)).decode()
NEW = base64.b64encode(os.urandom(32)).decode()
TENANT, PROVIDER = uuid.uuid4(), uuid.uuid4()
CONTEXT = key_context(TENANT, PROVIDER, "openai_compatible", "https://llm.example.com/v1")
SECRET_KEY = "sk-test-0123456789abcdefSECRET"
REQUEST = LLMRequest(system="s", messages=[LLMMessage("user", "q")])
ROLES: tuple[Role, ...] = ("answer", "router", "judge", "generator", "agent")


# --- Encryption ---------------------------------------------------------------------------


def test_keys_round_trip_and_ciphertexts_differ() -> None:
    ring = KeyRing(NEW)
    first, second = ring.seal(SECRET_KEY, context=CONTEXT), ring.seal(SECRET_KEY, context=CONTEXT)

    assert first != second  # a fresh nonce each time
    assert SECRET_KEY.encode() not in first
    assert ring.open(first, context=CONTEXT) == ring.open(second, context=CONTEXT) == SECRET_KEY
    assert ring.key_id_of(first) == ring.active_key_id


def test_tampering_or_another_context_is_detected() -> None:
    ring = KeyRing(NEW)
    sealed = bytearray(ring.seal(SECRET_KEY, context=CONTEXT))
    sealed[-1] ^= 1
    with pytest.raises(SecretsError, match="integrity check") as tampered:
        ring.open(bytes(sealed), context=CONTEXT)
    sealed[-1] ^= 1
    for other in (
        key_context(uuid.uuid4(), PROVIDER, "openai_compatible", "https://llm.example.com/v1"),
        key_context(TENANT, uuid.uuid4(), "openai_compatible", "https://llm.example.com/v1"),
        key_context(TENANT, PROVIDER, "openai_compatible", "https://elsewhere.example/v1"),
    ):  # copied to another tenant or provider, or left behind when the URL changed
        with pytest.raises(SecretsError):
            ring.open(bytes(sealed), context=other)
    with pytest.raises(SecretsError, match="not in a known format"):
        ring.open(b"\x02" + bytes(sealed[1:]), context=CONTEXT)
    assert SECRET_KEY not in str(tampered.value)


def test_rotation_reencrypts_with_the_new_key() -> None:
    old_ring = KeyRing(OLD)
    sealed = old_ring.seal(SECRET_KEY, context=CONTEXT)
    rotating = KeyRing(NEW, [OLD])  # the new key is active, the old one still decrypts

    assert rotating.open(sealed, context=CONTEXT) == SECRET_KEY
    assert rotating.needs_rotation(sealed)
    rotated = rotating.rotate(sealed, context=CONTEXT)
    assert not rotating.needs_rotation(rotated)
    assert rotating.key_id_of(rotated) == rotating.active_key_id != old_ring.active_key_id

    new_only = KeyRing(NEW)  # after rotation, the old key can go
    assert new_only.open(rotated, context=CONTEXT) == SECRET_KEY
    with pytest.raises(SecretsError, match="not configured") as missing:
        new_only.open(sealed, context=CONTEXT)
    assert OLD not in str(missing.value)
    assert SECRET_KEY not in str(missing.value)


def test_keyring_from_settings_uses_the_development_fallback_only_in_development() -> None:
    dev = KeyRing.from_settings(Settings(_env_file=None, app_env="test"))  # pyright: ignore[reportCallIssue]
    explicit = KeyRing.from_settings(
        Settings(_env_file=None, app_env="test", secrets_encryption_key=NEW)  # pyright: ignore[reportCallIssue, reportArgumentType]
    )
    assert dev.active_key_id != explicit.active_key_id == KeyRing(NEW).active_key_id
    with pytest.raises(SecretsError, match="32 bytes"):
        KeyRing(base64.b64encode(b"short").decode())


def test_key_hints_show_only_the_end_of_long_keys() -> None:
    assert key_hint(SECRET_KEY) == "…CRET"
    assert key_hint("ollama") == "…"  # four characters of a short key give too much away


# --- Base URLs ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://localhost:11434/v1/",
        "http://host.docker.internal:11434/v1",
        "http://10.0.0.5:8000/v1",
        "http://[::1]:8080/v1",
        "https://llm.example.com",
    ],
)
def test_ordinary_and_local_base_urls_are_accepted(url: str) -> None:
    assert check_base_url(url) == url.strip().rstrip("/")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("", "empty"),
        ("ftp://llm.example.com", "http:// or https://"),
        ("file:///etc/passwd", "http:// or https://"),
        ("https://user:pass@llm.example.com/v1", "own field"),
        ("https://sk-secret@llm.example.com/v1", "own field"),
        ("https://llm.example.com/v1?key=1", "query"),
        ("https://llm.example.com/v1#x", "fragment"),
        ("http://169.254.169.254/latest/meta-data", "metadata"),
        ("http://169.254.10.1/v1", "link-local"),
        ("http://[fe80::1]/v1", "link-local"),
        ("http://[::ffff:169.254.169.254]/", "metadata"),
        ("http://[fd00:ec2::254]/", "metadata"),
        ("http://100.100.100.200/", "metadata"),
        ("http://metadata.google.internal/computeMetadata/v1", "metadata"),
        ("http://0.0.0.0:8000/v1", "cannot be used"),
        ("http://224.0.0.1/v1", "cannot be used"),
        ("https://llm.example.com:99999/v1", "not a valid URL"),
        ("https://llm.exa mple.com/v1", "spaces"),
    ],
)
def test_unsafe_base_urls_are_refused(url: str, reason: str) -> None:
    with pytest.raises(UnsafeUrlError, match=reason):
        check_base_url(url)


async def test_names_that_resolve_to_metadata_addresses_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved: dict[str, str] = {"evil.example": "169.254.169.254", "fine.example": "10.1.2.3"}

    async def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        if host not in resolved:
            raise OSError("no such host")
        return [(2, 1, 6, "", (resolved[host], 0))]

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)
    with pytest.raises(UnsafeUrlError, match="metadata"):
        await urls.check_base_url_resolved("http://evil.example/v1")
    assert await urls.check_base_url_resolved("http://fine.example/v1") == "http://fine.example/v1"
    # A name that does not resolve here may resolve inside the deployment's network.
    assert await urls.check_base_url_resolved("http://gpu-box/v1") == "http://gpu-box/v1"


# --- The data policy ----------------------------------------------------------------------


def test_thresholds_and_the_legacy_setting() -> None:
    assert threshold("allow") == "restricted"
    assert threshold("local_only") == "confidential"
    assert threshold("internal") == "internal"
    assert default_threshold({"restricted_data": "allow"}) == "restricted"
    assert default_threshold({}) == "confidential"  # the safe default, as before
    assert above("restricted", "local_only")
    assert not above("confidential", "local_only")
    assert above("internal", "public")
    assert not above("public", "public")
    assert above("unlabelled", "confidential")  # unknown labels count as the highest


def test_routing_by_threshold() -> None:
    passages = [
        passage("S1", sensitivity="internal"),
        passage("S2", sensitivity="confidential"),
        passage("S3", sensitivity="restricted"),
    ]
    external, local = FakeLLM(external=True), FakeLLM(external=False)

    kept = route(passages, policy="internal", llm=external, local_llm=None)
    to_local = route(passages, policy="internal", llm=external, local_llm=local)
    everything = route(passages, policy="restricted", llm=external, local_llm=None)
    local_answer = route(passages, policy="public", llm=local, local_llm=None)

    assert ([p.chunk_id for p in kept.passages], kept.withheld) == (["c-S1"], 2)
    assert [p.marker for p in kept.passages] == ["S1"]
    assert (to_local.llm, len(to_local.passages), to_local.withheld) == (local, 3, 0)
    assert (len(everything.passages), everything.withheld) == (3, 0)
    assert (local_answer.llm, local_answer.withheld) == (local, 0)  # a local model gets all


def test_the_withheld_notice_names_the_levels_kept_away() -> None:
    assert "restricted source" in withheld_notice(1, "local_only")  # unchanged wording
    assert "restricted source" in withheld_notice(2, "confidential")
    notice = withheld_notice(2, "internal")
    assert notice.startswith("2 source(s)")
    assert "confidential and restricted data" in notice
    assert "internal, confidential and restricted" in withheld_notice(1, "public")


# --- The tenant resolution's wrapper ------------------------------------------------------


class LeakyLLM:
    """A provider whose errors echo the key, as some servers do."""

    external = True

    async def complete(self, role: Role, request: LLMRequest) -> tuple[str, LLMUsage]:
        raise LLMError(f"HTTP 401: invalid key {SECRET_KEY} (starts {SECRET_KEY[:8]})")

    async def stream(self, role: Role, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        yield TextDelta("partial")
        raise LLMError(f"connection reset; Authorization: Bearer {SECRET_KEY}")

    async def structured(self, role: Role, request: LLMRequest, schema: Any) -> Any:
        raise LLMError(SECRET_KEY)

    async def run_tools(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError(f"tool loop failed for {SECRET_KEY}")


def tenant_llm(target: Target, **kwargs: Any) -> TenantLLM:
    return TenantLLM(
        dict.fromkeys(ROLES, target),
        max_external_sensitivity=kwargs.get("policy", "confidential"),
        local=kwargs.get("local"),
    )


async def test_provider_errors_never_carry_the_key() -> None:
    llm = tenant_llm(Target("p:m", LeakyLLM(), True, secret=SECRET_KEY))

    for call in (
        lambda: llm.complete("answer", REQUEST),
        lambda: llm.structured("judge", REQUEST, object),  # type: ignore[arg-type]
        lambda: llm.run_tools("agent", REQUEST, [], None),  # type: ignore[arg-type]
    ):
        with pytest.raises(LLMError) as raised:
            await call()
        assert SECRET_KEY not in str(raised.value)
        assert SECRET_KEY[:8] not in str(raised.value)
        assert raised.value.__suppress_context__  # the original is not chained into tracebacks
    with pytest.raises(LLMError) as streamed:
        async for _ in llm.stream("answer", REQUEST):
            pass
    assert SECRET_KEY not in str(streamed.value)
    assert "[redacted]" in str(streamed.value)


async def test_usage_is_stamped_with_the_catalog_model_and_its_price() -> None:
    fake = FakeLLM("Answer [S1].")
    llm = tenant_llm(Target("prov:gpt-x", fake, True, price=(1.0, 4.0)))

    text, usage = await llm.complete("router", REQUEST)
    events = [e async for e in llm.stream("answer", REQUEST)]
    done = events[-1]

    assert text == "A short summary."
    assert (usage.catalog_model, usage.price_per_mtok) == ("prov:gpt-x", (1.0, 4.0))
    assert isinstance(done, Done)
    assert done.usage.catalog_model == "prov:gpt-x"
    assert fake.roles() == ["router", "answer"]  # the role reaches the provider unchanged
    assert cost_usd(usage) == pytest.approx((20 * 1.0 + 5 * 4.0) / 1_000_000)


def test_catalog_prices_come_first_then_the_static_table() -> None:
    sonnet = LLMUsage(model="claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=0)
    assert cost_usd(sonnet) == pytest.approx(2.0)
    sonnet.price_per_mtok = (3.0, 15.0)  # the tenant's own contract
    assert cost_usd(sonnet) == pytest.approx(3.0)
    assert cost_usd(LLMUsage(model="llama-local", input_tokens=10)) is None


def test_with_model_leaves_other_providers_alone() -> None:
    fake = FakeLLM()
    assert with_model(fake, "anything") is fake
    llm = tenant_llm(Target("system:a", fake, True))
    assert with_model(llm, None) is llm
    assert llm.using("missing:model") is llm  # no override hook: role defaults stay


def test_scrub_cuts_whole_and_partial_keys() -> None:
    text = f"key {SECRET_KEY}, prefix {SECRET_KEY[:8]}..., suffix ...{SECRET_KEY[-8:]}"
    cleaned = scrub(text, SECRET_KEY)
    assert SECRET_KEY not in cleaned
    assert SECRET_KEY[:8] not in cleaned
    assert SECRET_KEY[-8:] not in cleaned
    assert scrub("nothing to hide", None) == "nothing to hide"


# --- The catalog and its defaults ---------------------------------------------------------


def test_the_system_catalog_follows_the_settings() -> None:
    settings = Settings(  # pyright: ignore[reportCallIssue]
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        llm_model_judge="claude-opus-5-5",
        local_llm_base_url="http://localhost:11434/v1",
        local_llm_model="qwen3:14b",
    )
    system = system_catalog(settings)
    catalog = Catalog(system, TenantConfig(tenant_settings={"restricted_data": "allow"}))

    assert [p.id for p in catalog.providers] == ["system", "system-local"]
    assert {m.id for m in catalog.models} >= {
        "system:claude-sonnet-5-5",
        "system:claude-opus-5-5",
        "system-local:qwen3:14b",
    }
    assert catalog.roles["answer"] == "system:claude-sonnet-5-5"
    assert catalog.roles["judge"] == "system:claude-opus-5-5"
    assert catalog.policy == {
        "max_external_sensitivity": "restricted",
        "local_model": "system-local:qwen3:14b",
    }
    assert all(p.key_hint is None for p in catalog.providers)  # not even a hint of its key
    sonnet = catalog.model("system:claude-sonnet-5-5")
    assert sonnet is not None
    assert sonnet.price == (2.0, 10.0)


def test_a_tenant_choice_that_is_no_longer_usable_falls_back_to_the_default() -> None:
    system = system_catalog(Settings(_env_file=None, app_env="test"))  # pyright: ignore[reportCallIssue]
    catalog = Catalog(
        system,
        TenantConfig(
            roles={"answer": "gone:model", "judge": "system:claude-opus-5-5"},
            policy={"local_model": "system:claude-sonnet-5-5"},  # external: never local
        ),
    )
    assert catalog.roles["answer"] == "system:claude-sonnet-5-5"
    assert catalog.roles["judge"] == "system:claude-opus-5-5"
    assert catalog.local_model is None
    assert catalog.uses({"system:claude-opus-5-5"}) == ["the judge role"]


# --- Keys stay out of validation errors ---------------------------------------------------


def test_validation_errors_do_not_echo_keys() -> None:
    body = {"kind": "openai_compatible", "api_key": SECRET_KEY, "nested": {"token": "t0k3n"}}
    errors = [
        {"type": "missing", "loc": ("body", "name"), "msg": "Field required", "input": body},
        {"type": "string_too_long", "loc": ("body", "api_key"), "msg": "x", "input": SECRET_KEY},
        {"type": "int_parsing", "loc": ("body", "max_tokens"), "msg": "x", "input": "many"},
    ]
    redacted = redact_errors(errors)
    assert SECRET_KEY not in repr(redacted)
    assert "t0k3n" not in repr(redacted)
    assert redacted[0]["input"]["kind"] == "openai_compatible"  # the rest stays useful
    assert redacted[2]["input"] == "many"

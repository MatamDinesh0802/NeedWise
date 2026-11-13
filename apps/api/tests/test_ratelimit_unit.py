"""Rate limits without Valkey: configuration, the refusal's shape, failing open, and which
routes are limited. Counting itself is tested against Valkey in
tests/integration/test_rate_limits.py."""

import uuid
from typing import Any

import pytest
from fastapi.routing import iter_route_contexts
from pydantic import ValidationError

from workmind.api.deps import RateLimit
from workmind.core.config import Settings
from workmind.core.errors import problem_response
from workmind.core.ratelimit import (
    BUCKETS,
    DEFAULT_LIMITS,
    RateLimitedError,
    RateLimiter,
    merge_limits,
)
from workmind.main import create_app

TENANT, USER = uuid.uuid4(), uuid.uuid4()


class Unreachable:
    """A Valkey that is down."""

    def __init__(self) -> None:
        self.calls = 0

    async def eval(self, *_args: Any) -> int:
        self.calls += 1
        raise ConnectionError("valkey is down")


def test_overrides_change_only_what_they_name() -> None:
    limits = merge_limits({"chat": {"per_user": 3}, "mcp": {"per_tenant": 0, "window_s": 10}})

    assert limits["chat"].per_user == 3
    assert limits["chat"].per_tenant == DEFAULT_LIMITS["chat"].per_tenant
    assert (limits["mcp"].per_tenant, limits["mcp"].window_s) == (0, 10)
    assert limits["search"] == DEFAULT_LIMITS["search"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"chatt": {"per_user": 1}},
        {"chat": {"per_hour": 1}},
        {"chat": {"per_user": -1}},
        {"chat": {"window_s": 0}},
    ],
)
def test_unknown_or_impossible_limits_are_refused_at_startup(overrides: Any) -> None:
    with pytest.raises(ValidationError, match="RATE_LIMITS"):
        Settings(_env_file=None, app_env="test", rate_limits=overrides)  # pyright: ignore[reportCallIssue]


def test_a_refusal_is_a_problem_with_retry_after() -> None:
    limit = DEFAULT_LIMITS["chat"]
    response = problem_response(RateLimitedError("chat", "tenant", limit, 600, 17))

    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
    assert response.headers["content-type"] == "application/problem+json"
    body = bytes(response.body).decode()
    assert '"code":"rate_limited"' in body
    assert '"scope":"tenant"' in body
    assert "At most 600 questions per minute for your organization" in body


async def test_when_valkey_is_down_requests_go_through() -> None:
    valkey = Unreachable()
    limiter = RateLimiter(valkey, DEFAULT_LIMITS)  # pyright: ignore[reportArgumentType]

    await limiter.check(TENANT, USER, "chat")
    await limiter.check_address("10.0.0.1", "login")

    assert valkey.calls == 2


async def test_disabled_or_zero_limits_never_touch_valkey() -> None:
    valkey = Unreachable()
    off = RateLimiter(valkey, DEFAULT_LIMITS, enabled=False)  # pyright: ignore[reportArgumentType]
    zero = RateLimiter(valkey, merge_limits({"chat": {"per_user": 0, "per_tenant": 0}}))  # pyright: ignore[reportArgumentType]

    await off.check(TENANT, USER, "chat")
    await zero.check(TENANT, USER, "chat")
    await zero.check_address(None, "login")  # no address known: nothing to count

    assert valkey.calls == 0


# Every endpoint that calls a model, embeds, processes files or syncs a connector is limited.
# Two more are limited in their handlers: a conversation message ("chat", or "research" in
# research mode) and sign-in ("login", per address); MCP in its guard ("mcp", per token owner).
LIMITED = {
    ("POST", "/api/v1/search"): "search",
    ("POST", "/api/v1/ask"): "chat",
    ("POST", "/api/v1/research"): "research",
    ("POST", "/api/v1/admin/agents/test"): "agent_test",
    ("POST", "/api/v1/admin/models/providers/{provider_id}/test"): "agent_test",
    ("POST", "/api/v1/admin/workflows/{key}/run"): "agent_test",
    ("POST", "/api/v1/analytics/ask"): "analytics",
    ("POST", "/api/v1/analytics/query"): "analytics",
    ("POST", "/api/v1/collections/{collection_id}/documents"): "ingest",
    ("POST", "/api/v1/documents/{document_id}/reindex"): "ingest",
    ("POST", "/api/v1/meetings"): "ingest",
    ("POST", "/api/v1/admin/analytics/tables"): "ingest",
    ("POST", "/api/v1/admin/sources/{key}/sync"): "sync",
    ("POST", "/api/v1/requests"): "chat",
    ("POST", "/api/v1/admin/requests/inbound-email"): "chat",
    ("POST", "/api/v1/admin/routing/preview"): "agent_test",
    ("POST", "/api/v1/admin/slack/test"): "agent_test",
}


def test_the_expensive_routes_are_limited() -> None:
    app = create_app(Settings(_env_file=None, app_env="test", authz_outbox_worker=False))  # pyright: ignore[reportCallIssue]
    found: dict[tuple[str, str], str] = {}
    for route in iter_route_contexts(app.routes):
        dependant = getattr(route, "dependant", None)
        stack = list(dependant.dependencies) if dependant is not None else []
        while stack:
            dependency = stack.pop()
            if isinstance(dependency.call, RateLimit):
                for method in route.methods or ():
                    found[(method, route.path or "")] = dependency.call.bucket
            stack += dependency.dependencies

    assert found == LIMITED
    assert set(LIMITED.values()) | {"chat", "research", "login", "mcp"} == set(BUCKETS)

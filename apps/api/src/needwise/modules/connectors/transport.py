"""HTTP for connectors: a failed request becomes a ConnectorError (recorded on the source, retried
at the next interval), and a service that asks NeedWise to slow down is waited for instead of
hammered.

A 429, or a 503 with `Retry-After`, is retried after the time the service asks for (seconds or
an HTTP date; without the header: 1, 2, 4 seconds), a few times. A wait longer than
`MAX_WAIT_S` ends the sync with an error instead: nothing is lost, because the sync engine
does not advance the source's cursor on an error, so the next interval picks up where this one
stopped (Microsoft Graph throttling guidance; Atlassian rate limiting).
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from needwise.modules.connectors.base import ConnectorError

Sleep = Callable[[float], Awaitable[None]]
MAX_WAIT_S = 60.0
ATTEMPTS = 4
REDIRECTS = frozenset({301, 302, 303, 307, 308})


def retry_after(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before the next attempt, as the service asked."""
    value = response.headers.get("retry-after", "").strip()
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            when = None
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
    return float(2**attempt)


def throttled(response: httpx.Response) -> bool:
    return response.status_code == 429 or (
        response.status_code == 503 and "retry-after" in response.headers
    )


async def send(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    service: str,
    sleep: Sleep = asyncio.sleep,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    json: Any = None,
    data: Mapping[str, str] | None = None,
    timeout_s: float = 60.0,
    max_wait: float = MAX_WAIT_S,
    attempts: int = ATTEMPTS,
) -> httpx.Response:
    """One request, retried while the service throttles. Redirects are returned, not followed:
    the caller decides whether a redirect may carry its credentials (it never should leave the
    service's own host with them)."""
    response: httpx.Response | None = None
    for attempt in range(attempts):
        try:
            response = await http.request(
                method,
                url,
                headers=dict(headers or {}),
                params=dict(params) if params else None,  # {} would drop the URL's query
                json=json,
                data=dict(data) if data is not None else None,
                timeout=timeout_s,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{service} is unreachable: {type(exc).__name__}") from exc
        if not throttled(response):
            return response
        wait = retry_after(response, attempt)
        if wait > max_wait or attempt == attempts - 1:
            break
        await sleep(wait)
    status = response.status_code if response is not None else 429
    raise ConnectorError(
        f"{service} is throttling requests (HTTP {status}); the next sync continues"
    )

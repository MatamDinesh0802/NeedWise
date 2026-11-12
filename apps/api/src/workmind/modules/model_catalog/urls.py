"""Checks on the base URL an admin gives a model provider (server-side request forgery).

Refused: anything but http and https; credentials, a query or a fragment in the URL; cloud
metadata endpoints; link-local, multicast and unspecified addresses, whether written as an
address or reached through a name. Not refused: private and loopback addresses, because local
model servers (Ollama, vLLM, llama.cpp) live there. That leaves a known risk, documented in
docs/models.md: an org admin can make WorkMind send requests to other hosts on its network.
A name is resolved when the URL is saved and when a provider is tested, not on every request,
so a name that later resolves elsewhere (DNS rebinding) is not caught.
"""

import ipaddress
from urllib.parse import urlsplit

from workmind.core.urls import (
    METADATA_ADDRESSES,
    METADATA_HOSTS,
    RESOLVE_TIMEOUT_S,
    UnsafeUrlError,
    check_address,
    check_resolved,
)

__all__ = [
    "MAX_LENGTH",
    "METADATA_ADDRESSES",
    "METADATA_HOSTS",
    "RESOLVE_TIMEOUT_S",
    "UnsafeUrlError",
    "check_base_url",
    "check_base_url_resolved",
]

MAX_LENGTH = 500


def check_base_url(url: str) -> str:
    """The URL, normalized (no trailing slash), or UnsafeUrlError. Does not resolve names."""
    url = url.strip()
    if not url:
        raise UnsafeUrlError("the base URL is empty")
    if len(url) > MAX_LENGTH:
        raise UnsafeUrlError(f"the base URL is longer than {MAX_LENGTH} characters")
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise UnsafeUrlError("the base URL contains spaces or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port  # raises on a malformed port
    except ValueError as exc:
        raise UnsafeUrlError("the base URL is not a valid URL") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrlError("the base URL must start with http:// or https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeUrlError("put the API key in its own field, not in the URL")
    if parts.query or parts.fragment or url.endswith(("?", "#")):
        raise UnsafeUrlError("the base URL cannot have a query or a fragment")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeUrlError("the base URL has no host")
    if port == 0:
        raise UnsafeUrlError("the base URL has an invalid port")
    if host in METADATA_HOSTS:
        raise UnsafeUrlError("cloud metadata endpoints are not allowed")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        check_address(literal)
    return url.rstrip("/")


async def check_base_url_resolved(url: str) -> str:
    """check_base_url, plus every address the host name resolves to right now. A name that
    does not resolve is accepted: it may only resolve inside the deployment's network."""
    normalized = check_base_url(url)
    await check_resolved(urlsplit(normalized).hostname or "")
    return normalized

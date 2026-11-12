"""Checks on the base URL an admin gives a model provider (server-side request forgery).

Refused: anything but http and https; credentials, a query or a fragment in the URL; cloud
metadata endpoints; link-local, multicast and unspecified addresses, whether written as an
address or reached through a name. Not refused: private and loopback addresses, because local
model servers (Ollama, vLLM, llama.cpp) live there. That leaves a known risk, documented in
docs/models.md: an org admin can make WorkMind send requests to other hosts on its network.
A name is resolved when the URL is saved and when a provider is tested, not on every request,
so a name that later resolves elsewhere (DNS rebinding) is not caught.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

MAX_LENGTH = 500
METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a)
    for a in (
        "169.254.169.254",  # AWS, Azure, GCP, Oracle, DigitalOcean, OpenStack
        "169.254.170.2",  # AWS ECS task metadata
        "fd00:ec2::254",  # AWS over IPv6
        "100.100.100.200",  # Alibaba Cloud
        "192.0.0.192",  # Oracle Cloud (classic)
    )
)
RESOLVE_TIMEOUT_S = 3.0


class UnsafeUrlError(ValueError):
    """The URL is refused. The message says why, in words fit for an admin."""


def _check_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address in METADATA_ADDRESSES:
        raise UnsafeUrlError("cloud metadata endpoints are not allowed")
    if address.is_link_local:
        raise UnsafeUrlError("link-local addresses are not allowed")
    if address.is_loopback:
        return  # a model server on this host
    if address.is_multicast or address.is_unspecified:
        raise UnsafeUrlError("this address cannot be used")
    if isinstance(address, ipaddress.IPv4Address) and address.is_reserved:
        raise UnsafeUrlError("this address cannot be used")


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
        _check_address(literal)
    return url.rstrip("/")


async def check_base_url_resolved(url: str) -> str:
    """check_base_url, plus every address the host name resolves to right now. A name that
    does not resolve is accepted: it may only resolve inside the deployment's network."""
    normalized = check_base_url(url)
    host = (urlsplit(normalized).hostname or "").rstrip(".")
    try:
        ipaddress.ip_address(host)
        return normalized  # a literal address, already checked
    except ValueError:
        pass
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM),
            RESOLVE_TIMEOUT_S,
        )
    except (OSError, TimeoutError):
        return normalized
    for info in infos:
        try:
            address = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError:
            continue
        _check_address(address)
    return normalized

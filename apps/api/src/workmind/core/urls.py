"""Where WorkMind may send a request that an administrator configured (server-side request
forgery): a model provider's base URL, a web source's pages.

Refused: anything but http and https; credentials in the URL; cloud metadata endpoints;
link-local, multicast, unspecified and reserved addresses, whether written as an address or
reached through a name. Not refused: private and loopback addresses, because local model
servers and intranets live there. An org admin can therefore point WorkMind at other hosts on
its network (docs/security/threat-model.md). A name is resolved when it is checked, not on
every request, so a name that later resolves elsewhere (DNS rebinding) is not caught.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

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


def check_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address in METADATA_ADDRESSES:
        raise UnsafeUrlError("cloud metadata endpoints are not allowed")
    if address.is_link_local:
        raise UnsafeUrlError("link-local addresses are not allowed")
    if address.is_loopback:
        return  # a server on this host
    if address.is_multicast or address.is_unspecified:
        raise UnsafeUrlError("this address cannot be used")
    if isinstance(address, ipaddress.IPv4Address) and address.is_reserved:
        raise UnsafeUrlError("this address cannot be used")


def check_host(host: str) -> None:
    """A host name or a literal address, without resolving it."""
    host = host.rstrip(".").lower()
    if not host:
        raise UnsafeUrlError("the URL has no host")
    if host in METADATA_HOSTS:
        raise UnsafeUrlError("cloud metadata endpoints are not allowed")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return
    check_address(literal)


def check_url(url: str) -> str:
    """An http(s) URL that may be fetched (a query is fine), or UnsafeUrlError. No resolving."""
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise UnsafeUrlError("the URL contains spaces or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port  # raises on a malformed port
    except ValueError as exc:
        raise UnsafeUrlError("the URL is not valid") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrlError("the URL must start with http:// or https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeUrlError("the URL cannot contain a user name or password")
    if port == 0:
        raise UnsafeUrlError("the URL has an invalid port")
    check_host(parts.hostname or "")
    return url


async def check_resolved(host: str) -> None:
    """Every address `host` resolves to right now. A name that does not resolve is accepted:
    it may only resolve inside the deployment's network."""
    host = host.rstrip(".").strip("[]")
    try:
        ipaddress.ip_address(host)
        return  # a literal address: check_host already judged it
    except ValueError:
        pass
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM),
            RESOLVE_TIMEOUT_S,
        )
    except (OSError, TimeoutError):
        return
    for info in infos:
        try:
            address = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError:
            continue
        check_address(address)


async def check_url_resolved(url: str) -> str:
    """check_url, plus the addresses its host resolves to now."""
    check_url(url)
    await check_resolved(urlsplit(url).hostname or "")
    return url

"""Server-side request forgery through the web crawler (docs/security/owasp-llm-top10.md).

A web source's pages are attacker-controlled, and so are their redirects. Before the fix the
crawler followed every redirect (`follow_redirects=True`): a public page answering
`302 Location: http://169.254.169.254/...` made it fetch the cloud metadata endpoint and index
the answer as a document that the collection's readers could search.
"""

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from needwise.core.urls import UnsafeUrlError
from needwise.modules.connectors.base import ConnectorError, RemoteItem
from needwise.modules.connectors.registry import validate_config
from needwise.modules.connectors.web import WebConnector

METADATA = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"


class Site:
    """A fake web: pages by URL, every request recorded."""

    def __init__(self, pages: dict[str, httpx.Response]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def client(self) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            self.requested.append(url)
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            if url.startswith("http://169.254.169.254"):
                return httpx.Response(200, text="AccessKeyId: SECRET-METADATA-KEY")
            return self.pages.get(url, httpx.Response(404))

        return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def html(body: str) -> httpx.Response:
    return httpx.Response(200, html=body, headers={"content-type": "text/html"})


def redirect(to: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": to})


def resolve_to(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Every name resolves to `address` (no real DNS in unit tests)."""

    async def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        return [(2, 1, 6, "", (address, 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)


def crawler(site: Site, start: str) -> WebConnector:
    return WebConnector(site.client(), start_urls=[start])


async def test_a_start_page_that_redirects_to_the_metadata_endpoint_is_not_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_to(monkeypatch, "93.184.216.34")
    site = Site({"https://docs.vendor.example/": redirect(METADATA)})

    with pytest.raises(ConnectorError, match="redirects to another site"):
        await crawler(site, "https://docs.vendor.example/").scan({})

    assert not any("169.254" in url for url in site.requested)


async def test_a_linked_page_that_redirects_off_site_is_skipped_and_never_indexed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_to(monkeypatch, "93.184.216.34")
    site = Site(
        {
            "https://docs.vendor.example/guide/": html(
                "<title>Guide</title><a href='/guide/a'>a</a><a href='/guide/b'>b</a>"
            ),
            "https://docs.vendor.example/guide/a": redirect(METADATA),
            "https://docs.vendor.example/guide/b": html("<title>B</title><p>Fine.</p>"),
        }
    )
    connector = crawler(site, "https://docs.vendor.example/guide/")

    found = await connector.scan({})

    assert {i.external_id for i in found.items} == {
        "https://docs.vendor.example/guide/",
        "https://docs.vendor.example/guide/b",
    }
    assert not any("169.254" in url for url in site.requested)


async def test_redirects_within_the_site_are_still_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_to(monkeypatch, "93.184.216.34")
    site = Site(
        {
            "http://intranet.example/docs": redirect("https://intranet.example/docs/"),
            "https://intranet.example/docs/": html("<title>Docs</title><p>Leave: 16 weeks.</p>"),
        }
    )

    found = await crawler(site, "http://intranet.example/docs").scan({})

    assert [i.external_id for i in found.items] == ["https://intranet.example/docs/"]


async def test_fetch_does_not_follow_a_redirect_to_another_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_to(monkeypatch, "93.184.216.34")
    site = Site({"https://docs.vendor.example/x": redirect(METADATA)})
    connector = crawler(site, "https://docs.vendor.example/")
    item = RemoteItem("https://docs.vendor.example/x", "x", "x.html", "v1")

    with pytest.raises(ConnectorError):
        await connector.fetch(item)

    assert not any("169.254" in url for url in site.requested)


async def test_a_host_that_resolves_to_a_metadata_address_is_never_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_to(monkeypatch, "169.254.169.254")
    site = Site({"https://rebind.example/": html("<title>x</title>")})

    with pytest.raises(ConnectorError, match="not allowed"):
        await crawler(site, "https://rebind.example/").scan({})

    assert site.requested == []


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[fe80::1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://user:secret@intranet.example/",
    ],
)
def test_web_sources_refuse_unsafe_start_urls(url: str) -> None:
    with pytest.raises((ValidationError, UnsafeUrlError)):
        validate_config("web", {"start_urls": [url]})


def test_web_sources_accept_intranet_and_public_start_urls() -> None:
    config = validate_config(
        "web", {"start_urls": ["https://intranet.example/docs/", "http://10.0.0.5:8080/wiki"]}
    )
    assert len(config["start_urls"]) == 2


class _Peer:
    """What httpcore puts in a response's extensions: the connection it came over."""

    def __init__(self, address: str) -> None:
        self.address = address

    def get_extra_info(self, info: str) -> Any:
        return (self.address, 80) if info == "server_addr" else None


def from_peer(address: str, body: str) -> httpx.Response:
    return httpx.Response(
        200,
        html=body,
        headers={"content-type": "text/html"},
        extensions={"network_stream": _Peer(address)},
    )


async def test_a_source_no_admin_set_up_refuses_private_addresses_at_every_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A collection owner's web source fetched Qdrant's /metrics and stored it as a document.
    with pytest.raises(ConnectorError, match="only an org admin"):
        await WebConnector(
            Site({}).client(), start_urls=["http://127.0.0.1:6333/metrics"], private_network=False
        ).scan({})
    resolve_to(monkeypatch, "10.0.0.7")
    with pytest.raises(ConnectorError, match="only an org admin"):
        await WebConnector(
            Site({}).client(), start_urls=["http://qdrant.internal/"], private_network=False
        ).scan({})

    # Public when checked, internal when requested (a name that resolves elsewhere later):
    # the answer is discarded, never stored.
    resolve_to(monkeypatch, "93.184.216.34")
    rebound = Site({"https://rebind.example/": from_peer("127.0.0.1", "<title>metrics</title>")})
    with pytest.raises(ConnectorError, match="not allowed"):
        await WebConnector(
            rebound.client(), start_urls=["https://rebind.example/"], private_network=False
        ).scan({})
    public = Site({"https://docs.example/": from_peer("93.184.216.34", "<title>Docs</title>")})
    found = await WebConnector(
        public.client(), start_urls=["https://docs.example/"], private_network=False
    ).scan({})
    assert [i.external_id for i in found.items] == ["https://docs.example/"]
    # An org admin's source still reaches the intranet.
    intranet = Site({"http://intranet.example/": from_peer("10.0.0.7", "<title>Intranet</title>")})
    resolve_to(monkeypatch, "10.0.0.7")
    assert (
        len(
            (
                await WebConnector(intranet.client(), start_urls=["http://intranet.example/"]).scan(
                    {}
                )
            ).items
        )
        == 1
    )

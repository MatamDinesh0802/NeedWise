"""The Confluence connector against tests/fake_confluence.py (built from the REST API v2
reference): paging, the page tree as metadata, links back, storage format to Markdown,
attachments through the media redirect, both ways to sign in, throttling, and what a complete
scan means for deleted and archived pages."""

import base64
import io
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from needwise.modules.connectors.base import ConnectorError, RemoteItem
from needwise.modules.connectors.confluence import ConfluenceConnector
from needwise.modules.connectors.confluence_storage import storage_to_markdown
from needwise.modules.connectors.oauth import OAuthApp
from needwise.modules.connectors.registry import validate_config
from needwise.modules.knowledge.parsing import detect_mime, parse
from tests.fake_confluence import (
    API_TOKEN,
    CLOUD_ID,
    EMAIL,
    SITE,
    Attachment,
    FakeConfluence,
    Page,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TOKEN = {"email": EMAIL, "api_token": API_TOKEN}


def docx(text: str) -> bytes:
    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(buffer)
    return buffer.getvalue()


def wiki() -> FakeConfluence:
    """The HR space: a home page, two policies under it, one nested deeper, and attachments."""
    fake = FakeConfluence()
    fake.space("98306", "HR", "People")
    fake.space("98400", "ENG", "Engineering")
    fake.page(Page("1001", "98306", "People home", "<p>Everything about working here.</p>"))
    fake.page(Page("1002", "98306", "Policies", "<p>All policies.</p>", "1001", "page", version=4))
    fake.page(
        Page(
            "1003",
            "98306",
            "Leave policy",
            "<h1>Leave</h1><p>Parental leave is <strong>16 weeks</strong>.</p>"
            '<ac:structured-macro ac:name="info"><ac:parameter ac:name="title">Note'
            "</ac:parameter><ac:rich-text-body><p>Ask HR first.</p></ac:rich-text-body>"
            "</ac:structured-macro>",
            "1002",
            "page",
            version=7,
        )
    )
    fake.page(Page("1004", "98306", "Travel: hotels/limits", "<p>250 euros.</p>", "1002", "page"))
    fake.page(Page("1005", "98306", "In a folder", "<p>Under a folder.</p>", "77", "folder"))
    fake.page(Page("2001", "98400", "Runbook", "<p>Restart it.</p>"))
    fake.attach(Attachment("att5001", "1003", "leave-form.docx", DOCX, docx("Leave form.")))
    fake.attach(Attachment("att5002", "1003", "org-chart.png", "image/png", b"\x89PNG"))
    return fake


def connector(
    fake: FakeConfluence,
    credentials: dict[str, Any] | None = None,
    *,
    spaces: list[str] | None = None,
    attachments: bool = True,
    sleeps: list[float] | None = None,
) -> ConfluenceConnector:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return ConfluenceConnector(
        fake.client(),
        site_url=SITE,
        spaces=spaces or ["HR"],
        include_attachments=attachments,
        credentials=TOKEN if credentials is None else credentials,
        app=OAuthApp("atlassian-client", "atlassian-secret"),
        sleep=sleep,
    )


def by_id(items: list[RemoteItem]) -> dict[str, RemoteItem]:
    return {i.external_id: i for i in items}


async def test_pages_are_listed_page_by_page_with_their_place_and_links() -> None:
    fake = wiki()

    scan = await connector(fake).scan({})

    items = by_id(scan.items)
    assert scan.complete is True  # every current page: a missing one was deleted
    assert set(items) == {
        "page:1001",
        "page:1002",
        "page:1003",
        "page:1004",
        "page:1005",
        "attachment:att5001",  # the .png is not something NeedWise can read
    }
    leave = items["page:1003"]
    assert leave.title == "Leave policy"
    assert leave.filename == "Leave policy.md"
    assert leave.url == f"{SITE}/wiki/spaces/HR/pages/1003/Leave+policy"
    assert leave.version.startswith("7:")
    assert leave.meta["path"] == ["People", "People home", "Policies", "Leave policy"]
    assert leave.meta["ancestors"] == [
        {"id": "1001", "title": "People home"},
        {"id": "1002", "title": "Policies"},
    ]
    assert (leave.meta["space_key"], leave.meta["parent_id"]) == ("HR", "1002")
    assert items["page:1004"].filename == "Travel  hotels limits.md"  # no path separators
    assert items["page:1005"].meta["path"] == ["People", "In a folder"]  # folders are not pages
    form = items["attachment:att5001"]
    assert (form.filename, form.version, form.meta["page_id"]) == ("leave-form.docx", "1", "1003")
    assert form.url is not None
    assert form.url.startswith(f"{SITE}/wiki/pages/viewpageattachments")
    page_lists = [r for r in fake.requests if r.url.path.endswith("/spaces/98306/pages")]
    assert len(page_lists) == 3  # five pages, two per response: the cursor links were followed
    assert all(r.url.host == "acme.atlassian.net" for r in fake.requests)
    assert all("body-format" not in str(r.url) for r in page_lists)  # no bodies in a scan


async def test_a_page_becomes_markdown_that_parses_into_sections() -> None:
    fake = wiki()
    source = connector(fake)
    items = by_id((await source.scan({})).items)

    data = await source.fetch(items["page:1003"])

    text = data.decode()
    assert text.startswith("# Leave policy\n\n## Leave\n\nParental leave is 16 weeks.")
    assert "Ask HR first." in text
    assert "Note" not in text  # macro parameters are not text
    parsed = parse(data, "Leave policy.md", detect_mime("Leave policy.md"))
    assert parsed.title == "Leave policy"
    assert parsed.sections[0].heading_path == ("Leave",)


async def test_attachments_come_through_the_media_redirect_without_credentials() -> None:
    fake = wiki()
    source = connector(fake)
    items = by_id((await source.scan({})).items)

    data = await source.fetch(items["attachment:att5001"])

    parsed = parse(data, "leave-form.docx", detect_mime("leave-form.docx"))
    assert "Leave form." in parsed.sections[0].text
    download, media = fake.requests[-2:]
    assert download.url.path == "/wiki/rest/api/content/1003/child/attachment/att5001/download"
    assert media.url.host == "api.media.atlassian.com"
    assert "authorization" not in media.headers


async def test_an_api_token_signs_in_with_basic_auth_against_the_site() -> None:
    fake = wiki()

    await connector(fake).scan({})

    expected = base64.b64encode(f"{EMAIL}:{API_TOKEN}".encode()).decode()
    assert fake.requests[0].headers["authorization"] == f"Basic {expected}"
    assert str(fake.requests[0].url).startswith(f"{SITE}/wiki/api/v2/spaces?")


async def test_oauth_goes_through_the_gateway_and_hands_back_the_rotated_token() -> None:
    fake = wiki()
    source = connector(fake, {"refresh_token": "oauth-refresh-1", "cloud_id": CLOUD_ID})

    scan = await source.scan({})

    assert len(scan.items) == 6
    token_call = fake.requests[0]
    assert (token_call.url.host, token_call.url.path) == ("auth.atlassian.com", "/oauth/token")
    api_calls = fake.requests[1:]
    assert all(
        str(r.url).startswith(f"https://api.atlassian.com/ex/confluence/{CLOUD_ID}/wiki/")
        for r in api_calls
    )
    assert source.updated_credentials is not None
    assert source.updated_credentials["refresh_token"] == "oauth-refresh-2"  # rotated
    assert source.updated_credentials["access_token"] == fake.access_token
    assert source.updated_credentials["cloud_id"] == CLOUD_ID


async def test_a_refused_access_token_is_renewed_once() -> None:
    fake = wiki()
    source = connector(
        fake,
        {
            "refresh_token": "oauth-refresh-1",
            "access_token": "revoked",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "cloud_id": CLOUD_ID,
        },
    )

    await source.scan({})

    assert fake.refreshes == 1


async def test_bad_credentials_and_unknown_spaces_are_errors() -> None:
    fake = wiki()

    with pytest.raises(ConnectorError, match="refused the credentials"):
        await connector(fake, {"email": EMAIL, "api_token": "wrong"}).scan({})
    with pytest.raises(ConnectorError, match="NOPE not found"):
        # Never an empty listing: a complete scan of nothing would delete every page.
        await connector(fake, spaces=["HR", "NOPE"]).scan({})
    with pytest.raises(ConnectorError, match="cloud id"):
        connector(fake, {"refresh_token": "oauth-refresh-1"})
    with pytest.raises(ConnectorError, match=r"connected to https://other\.atlassian\.net"):
        # The site in the configuration changed after connecting: the cloud id is stale.
        connector(
            fake,
            {
                "refresh_token": "oauth-refresh-1",
                "cloud_id": CLOUD_ID,
                "site_url": "https://other.atlassian.net",
            },
        )
    with pytest.raises(ConnectorError, match="API token"):
        connector(fake, {})  # nothing at all
    with pytest.raises(ConnectorError, match="email"):
        connector(fake, {"api_token": API_TOKEN})


async def test_deleted_archived_and_moved_pages_leave_a_complete_scan() -> None:
    fake = wiki()
    source = connector(fake, spaces=["HR", "ENG"])
    first = by_id((await source.scan({})).items)

    fake.pages["1004"].status = "trashed"
    fake.pages["1005"].status = "archived"
    fake.pages["1002"].space_id = "98400"  # moved to Engineering, with its tree
    second = await source.scan({})

    after = by_id(second.items)
    assert second.complete is True
    assert {"page:1004", "page:1005"} <= set(first)
    assert not {"page:1004", "page:1005"} & set(after)
    assert after["page:1002"].meta["space_key"] == "ENG"
    # Its child stays in HR, now without the parent in its space: its place changed.
    assert after["page:1003"].meta["path"] == ["People", "Leave policy"]
    assert after["page:1003"].version != first["page:1003"].version


async def test_a_move_changes_the_version_marker_but_not_the_content() -> None:
    fake = wiki()
    source = connector(fake)
    before = by_id((await source.scan({})).items)["page:1004"]
    body_before = await source.fetch(before)

    fake.pages["1004"].parent_id = "1001"  # moved directly under the home page
    after = by_id((await source.scan({})).items)["page:1004"]

    assert after.version != before.version
    assert after.version.split(":")[0] == before.version.split(":")[0]  # same page version
    assert await source.fetch(after) == body_before  # nothing to re-index


async def test_an_edit_changes_exactly_that_page() -> None:
    fake = wiki()
    source = connector(fake)
    before = {i.external_id: i.version for i in (await source.scan({})).items}

    fake.edit("1003", "<p>Parental leave is 18 weeks.</p>")
    after = {i.external_id: i.version for i in (await source.scan({})).items}

    assert [k for k in before if before[k] != after[k]] == ["page:1003"]


async def test_attachments_can_be_left_out() -> None:
    fake = wiki()

    scan = await connector(fake, attachments=False).scan({})

    assert all(i.external_id.startswith("page:") for i in scan.items)
    assert not any("attachments" in r.url.path for r in fake.requests)


async def test_throttling_waits_as_asked_then_gives_up_on_long_waits() -> None:
    fake = wiki()
    sleeps: list[float] = []
    fake.throttle = 2

    await connector(fake, sleeps=sleeps).scan({})

    assert sleeps == [2.0, 2.0]

    class Slow(FakeConfluence):
        def handle(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "600"})

    with pytest.raises(ConnectorError, match="throttling"):
        await connector(Slow()).scan({})


async def test_pagination_links_never_leave_the_site() -> None:
    class Hostile(FakeConfluence):
        def handle(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(
                200, json={"results": [], "_links": {"next": "https://evil.example/steal"}}
            )

    fake = Hostile()

    with pytest.raises(ConnectorError, match="pagination link"):
        await connector(fake).scan({})
    assert all(r.url.host == "acme.atlassian.net" for r in fake.requests)


def test_configuration_is_validated() -> None:
    good = validate_config(
        "confluence", {"site_url": "https://Acme.atlassian.net/wiki/spaces/HR", "spaces": ["HR"]}
    )

    assert good == {"site_url": SITE, "spaces": ["HR"], "include_attachments": True}
    for bad in (
        {"site_url": "http://acme.atlassian.net", "spaces": ["HR"]},  # not https
        {"site_url": "https://confluence.intranet", "spaces": ["HR"]},  # not Confluence Cloud
        {"site_url": "https://acme.atlassian.net.evil.example", "spaces": ["HR"]},
        {"site_url": SITE, "spaces": []},
        {"site_url": SITE, "spaces": ["HR space"]},
    ):
        with pytest.raises(ValidationError):
            validate_config("confluence", bad)


def test_storage_format_keeps_text_and_drops_configuration() -> None:
    storage = (
        "<h2>Steps</h2>"
        "<ol><li>Open <ac:link><ri:page ri:content-title='The form' /></ac:link></li>"
        "<li>Send it <ac:link><ri:page ri:content-title='x' />"
        "<ac:plain-text-link-body><![CDATA[to HR]]></ac:plain-text-link-body></ac:link></li></ol>"
        "<ac:task-list><ac:task><ac:task-id>1</ac:task-id><ac:task-status>complete</ac:task-status>"
        "<ac:task-body>Sign</ac:task-body></ac:task></ac:task-list>"
        "<table><tbody><tr><th>Country</th><th>Days</th></tr><tr><td>DE</td><td>30</td></tr>"
        "</tbody></table>"
        "<ac:structured-macro ac:name='code'><ac:parameter ac:name='language'>sh</ac:parameter>"
        "<ac:plain-text-body><![CDATA[echo '<b>' && exit]]></ac:plain-text-body>"
        "</ac:structured-macro>"
        "<p><ac:image><ri:attachment ri:filename='a.png' /></ac:image>Due "
        "<time datetime='2026-12-01' />.</p>"
    )

    text = storage_to_markdown(storage)

    assert text == (
        "### Steps\n\n1. Open The form\n2. Send it to HR\n\n- [x] Sign\n\n"
        "| Country | Days |\n| DE | 30 |\n\n```\necho '<b>' && exit\n```\n\nDue 2026-12-01.\n"
    )

"""The connector contract: every connector, against a fake of its service's HTTP API.

1. scan lists items with stable ids and a version that changes exactly when content does;
2. fetch returns bytes NeedWise can parse;
3. failures raise ConnectorError (never a raw HTTP error), so a sync can record them;
4. a scan that saw only part of the source says so (complete=False), so nothing is deleted.

Confluence and SharePoint run against the fuller fakes in tests/fake_confluence.py and
tests/fake_graph.py; their own behaviour (paging, delta feeds, throttling, OAuth) is tested in
test_connectors_confluence.py and test_connectors_sharepoint.py.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from needwise.modules.connectors.base import Connector, ConnectorError
from needwise.modules.connectors.confluence import ConfluenceConnector
from needwise.modules.connectors.gdrive import GoogleDriveConnector
from needwise.modules.connectors.github import GitHubConnector
from needwise.modules.connectors.oauth import OAuthApp
from needwise.modules.connectors.sharepoint import SharePointConnector
from needwise.modules.connectors.slack import SlackConnector
from needwise.modules.connectors.web import WebConnector
from needwise.modules.knowledge.parsing import detect_mime, parse
from tests import fake_confluence, fake_graph
from tests.fake_confluence import Attachment, FakeConfluence, Page
from tests.fake_graph import FakeGraph, Item

# --- fake services --------------------------------------------------------------------------


class FakeService:
    """Mutable content behind a MockTransport, so a test can change the source between scans."""

    def __init__(self, routes: Callable[["FakeService", httpx.Request], httpx.Response]) -> None:
        self.routes = routes
        self.content: dict[str, Any] = {}
        self.fail = False
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.fail:
                return httpx.Response(503, text="down")
            return self.routes(self, request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def web_routes(svc: FakeService, request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /docs/private\n")
    page = svc.content.get(path)
    if page is None:
        return httpx.Response(404)
    return httpx.Response(200, html=page, headers={"content-type": "text/html; charset=utf-8"})


def github_routes(svc: FakeService, request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/repos/acme/handbook":
        return httpx.Response(200, json={"default_branch": "main"})
    if path == "/repos/acme/handbook/git/trees/main":
        tree = [
            {"path": p, "type": "blob", "sha": f"sha-{hash(text) & 0xFFFF:x}"}
            for p, text in svc.content.items()
        ] + [{"path": "src", "type": "tree", "sha": "t"}]
        return httpx.Response(
            200, json={"tree": tree, "truncated": svc.content.get("_truncated", False)}
        )
    if path.startswith("/repos/acme/handbook/contents/"):
        return httpx.Response(
            200, content=svc.content[path.removeprefix("/repos/acme/handbook/contents/")].encode()
        )
    return httpx.Response(404)


def slack_routes(svc: FakeService, request: httpx.Request) -> httpx.Response:
    method = request.url.path.rsplit("/", 1)[-1]
    if request.headers.get("authorization") != "Bearer xoxb-test":
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    if method == "conversations.info":
        return httpx.Response(200, json={"ok": True, "channel": {"name": "general"}})
    if method == "conversations.history":
        return httpx.Response(200, json={"ok": True, "messages": svc.content.get("messages", [])})
    return httpx.Response(200, json={"ok": False, "error": "unknown_method"})


def gdrive_routes(svc: FakeService, request: httpx.Request) -> httpx.Response:
    if request.url.host == "oauth2.googleapis.com":
        return httpx.Response(200, json={"access_token": "fresh-token", "expires_in": 3600})
    if request.headers.get("authorization") != "Bearer fresh-token":
        return httpx.Response(401)
    path = request.url.path
    if path == "/drive/v3/files":
        parent = request.url.params["q"].split("'")[1]
        return httpx.Response(200, json={"files": svc.content.get(parent, [])})
    file_id = path.split("/")[4]
    return httpx.Response(200, content=svc.content["bytes"][file_id])


def fake_routes(svc: FakeService, request: httpx.Request) -> httpx.Response:
    return svc.content["fake"].handle(request)


async def no_sleep(seconds: float) -> None:
    return None


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# --- one fixture per connector ---------------------------------------------------------------

DOC_A = "# Leave\n\nParental leave is 16 weeks.\n"
DOC_B = "# Travel\n\nHotels are capped at 250 euros.\n"


def web(svc: FakeService) -> Connector:
    svc.content.update(
        {
            "/docs/": (
                "<html><title>Docs</title><a href='/docs/leave'>Leave</a>"
                "<a href='/docs/private/x'>x</a><a href='https://elsewhere.example/'>out</a></html>"
            ),
            "/docs/leave": (
                "<html><title>Leave policy</title><p>Parental leave is 16 weeks.</p></html>"
            ),
        }
    )
    return WebConnector(svc.client(), start_urls=["https://intranet.example/docs/"])


def github(svc: FakeService) -> Connector:
    svc.content.update({"docs/leave.md": DOC_A, "docs/travel.mdx": DOC_B, "src/app.py": "print()"})
    return GitHubConnector(svc.client(), repo="acme/handbook", paths=["docs"])


def slack(svc: FakeService) -> Connector:
    svc.content["messages"] = [
        {"ts": "1793750400.000100", "user": "U1", "text": "Parental leave is now 20 weeks?"},
        {
            "ts": "1793750500.000200",
            "user": "U2",
            "text": "Check the HR handbook.",
            "thread_ts": "1793750400.000100",
        },
    ]
    return SlackConnector(svc.client(), token="xoxb-test", channels=["C1"], days=36500)


def gdrive(svc: FakeService) -> Connector:
    svc.content.update(
        {
            "root": [
                {
                    "id": "f1",
                    "name": "Leave policy",
                    "mimeType": "application/vnd.google-apps.document",
                    "version": "7",
                },
                {"id": "f2", "name": "notes.md", "mimeType": "text/markdown", "version": "3"},
                {"id": "sub", "name": "Archive", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "f3", "name": "photo.jpg", "mimeType": "image/jpeg", "version": "1"},
            ],
            "sub": [{"id": "f4", "name": "old.txt", "mimeType": "text/plain", "version": "1"}],
            "bytes": {
                "f1": _docx("Parental leave is 16 weeks."),
                "f2": DOC_A.encode(),
                "f4": b"Old notes.",
            },
        }
    )
    return GoogleDriveConnector(
        svc.client(),
        folder_id="root",
        credentials={"refresh_token": "r"},
        client_id="id",
        client_secret="secret",
    )


def confluence(svc: FakeService) -> Connector:
    fake = FakeConfluence()
    fake.space("98306", "HR", "People")
    fake.page(Page("98310", "98306", "People home", "<p>Everything about working here.</p>"))
    fake.page(
        Page(
            "98311",
            "98306",
            "Leave policy",
            "<h1>Leave</h1><p>Parental leave is <strong>16 weeks</strong>.</p>",
            parent_id="98310",
            parent_type="page",
        )
    )
    fake.attach(Attachment("att98331", "98311", "leave-form.docx", DOCX, _docx("Leave form.")))
    fake.attach(Attachment("att98332", "98311", "org-chart.png", "image/png", b"\x89PNG"))
    svc.content["fake"] = fake
    return ConfluenceConnector(
        svc.client(),
        site_url=fake_confluence.SITE,
        spaces=["HR"],
        credentials={"email": fake_confluence.EMAIL, "api_token": fake_confluence.API_TOKEN},
        app=OAuthApp(),
        sleep=no_sleep,
    )


def sharepoint(svc: FakeService) -> Connector:
    fake = FakeGraph()
    fake.put(Item("01POLICIES", "Policies", fake_graph.ROOT_ID, folder=True))
    fake.put(Item("01LEAVEDOC", "Leave policy.docx", "01POLICIES", data=_docx("16 weeks.")))
    fake.put(Item("01NOTESMD", "notes.md", "01POLICIES", data=DOC_A.encode()))
    fake.put(Item("01PHOTOJPG", "photo.jpg", "01POLICIES", data=b"\xff\xd8\xff"))
    fake.put(Item("01ARCHIVE", "Archive", "01POLICIES", folder=True))
    fake.put(Item("01OLDTXT", "old.txt", "01ARCHIVE", data=b"Old notes."))
    svc.content["fake"] = fake
    return SharePointConnector(
        svc.client(),
        site_url=fake_graph.SITE_URL,
        library=None,
        folder=None,
        credentials={"refresh_token": fake.refresh_token},
        app=OAuthApp("client-id", "client-secret", "contoso.onmicrosoft.com"),
        sleep=no_sleep,
    )


def _docx(text: str) -> bytes:
    import io

    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(buffer)
    return buffer.getvalue()


CONNECTORS = {
    "web": (web, web_routes),
    "github": (github, github_routes),
    "slack": (slack, slack_routes),
    "gdrive": (gdrive, gdrive_routes),
    "confluence": (confluence, fake_routes),
    "sharepoint": (sharepoint, fake_routes),
}
EXPECTED_ITEMS = {"web": 2, "github": 2, "slack": 1, "gdrive": 3, "confluence": 3, "sharepoint": 3}


@pytest.fixture(params=sorted(CONNECTORS))
def case(request: pytest.FixtureRequest) -> tuple[str, FakeService, Connector]:
    build, routes = CONNECTORS[request.param]
    svc = FakeService(routes)
    return request.param, svc, build(svc)


async def test_scan_lists_items_that_fetch_into_parseable_files(
    case: tuple[str, FakeService, Connector],
) -> None:
    kind, _, connector = case

    scan = await connector.scan({})

    assert len(scan.items) == EXPECTED_ITEMS[kind]
    assert len({i.external_id for i in scan.items}) == len(scan.items)
    for item in scan.items:
        data = await connector.fetch(item)
        parsed = parse(data, item.filename, detect_mime(item.filename, None))
        assert any(s.text.strip() for s in parsed.sections), item.filename


async def test_versions_are_stable_until_the_content_changes(
    case: tuple[str, FakeService, Connector],
) -> None:
    kind, svc, connector = case
    first = {i.external_id: i.version for i in (await connector.scan({})).items}
    again = {i.external_id: i.version for i in (await connector.scan({})).items}
    assert first == again

    match kind:
        case "web":
            svc.content["/docs/leave"] = (
                "<html><title>Leave policy</title><p>Now 18 weeks.</p></html>"
            )
        case "github":
            svc.content["docs/leave.md"] = DOC_A + "\nUpdated.\n"
        case "slack":
            svc.content["messages"][0]["text"] = "Edited: it is 16 weeks."
        case "confluence":
            svc.content["fake"].edit("98311", "<p>Parental leave is 18 weeks.</p>")
        case "sharepoint":
            svc.content["fake"].write("01NOTESMD", b"# Leave\n\nNow 18 weeks.\n")
        case _:
            svc.content["root"][0]["version"] = "8"
    changed = {i.external_id: i.version for i in (await connector.scan({})).items}

    assert sum(first[k] != changed[k] for k in first) == 1


async def test_failures_become_connector_errors(case: tuple[str, FakeService, Connector]) -> None:
    _, svc, connector = case
    svc.fail = True

    with pytest.raises(ConnectorError):
        await connector.scan({})


async def test_github_reports_a_truncated_tree_as_incomplete() -> None:
    svc = FakeService(github_routes)
    connector = github(svc)
    svc.content["_truncated"] = True

    assert (await connector.scan({})).complete is False


async def test_slack_never_claims_a_complete_scan_and_checks_its_token() -> None:
    svc = FakeService(slack_routes)
    good = slack(svc)
    bad = SlackConnector(svc.client(), token="xoxb-wrong", channels=["C1"])

    assert (await good.scan({})).complete is False  # a window of history, not everything
    with pytest.raises(ConnectorError, match="invalid_auth"):
        await bad.scan({})


async def test_the_crawler_respects_robots_txt_and_stays_on_its_site() -> None:
    svc = FakeService(web_routes)
    connector = web(svc)

    urls = {i.external_id for i in (await connector.scan({})).items}

    assert urls == {"https://intranet.example/docs/", "https://intranet.example/docs/leave"}
    assert not any("elsewhere" in str(r.url) or "private" in str(r.url) for r in svc.requests)


async def test_drive_refreshes_its_token_and_hands_it_back_for_storage() -> None:
    svc = FakeService(gdrive_routes)
    connector = gdrive(svc)

    await connector.scan({})

    updated = getattr(connector, "updated_credentials", None)
    assert updated is not None
    assert updated["access_token"] == "fresh-token"
    assert updated["refresh_token"] == "r"
    assert json.dumps(updated)  # storable as-is

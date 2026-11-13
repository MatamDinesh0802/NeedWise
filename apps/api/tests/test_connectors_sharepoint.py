"""The SharePoint/OneDrive connector against tests/fake_graph.py (built from the Microsoft
Graph documentation): which drive and folder, the first full enumeration and its delta link,
later changes and tombstones, folders that move or disappear, expired delta tokens,
throttling, downloads and rotating refresh tokens."""

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tests.fake_graph import (
    DRIVE_ID,
    GRAPH,
    HOST,
    ROOT_ID,
    SITE_URL,
    FakeGraph,
    Item,
)
from workmind.modules.connectors.base import ConnectorError, RemoteItem, ScanResult
from workmind.modules.connectors.oauth import OAuthApp, microsoft_scopes
from workmind.modules.connectors.registry import validate_config
from workmind.modules.connectors.sharepoint import SharePointConnector

APP = OAuthApp("client-id", "client-secret", "contoso.onmicrosoft.com")


def library() -> FakeGraph:
    """Documents/Policies/{Leave policy.docx, notes.md, photo.jpg, Archive/old.txt}, plus
    Documents/Budget.xlsx and Documents/Team/roster.csv outside Policies."""
    fake = FakeGraph()
    fake.put(Item("01POLICIES", "Policies", ROOT_ID, folder=True))
    fake.put(Item("01LEAVE", "Leave policy.docx", "01POLICIES", data=b"docx-bytes"))
    fake.put(Item("01NOTES", "notes.md", "01POLICIES", data=b"# Notes\n\nSome notes.\n"))
    fake.put(Item("01PHOTO", "photo.jpg", "01POLICIES", data=b"\xff\xd8"))
    fake.put(Item("01ARCHIVE", "Archive", "01POLICIES", folder=True))
    fake.put(Item("01OLD", "old.txt", "01ARCHIVE", data=b"Old notes."))
    fake.put(Item("01BUDGET", "Budget.xlsx", ROOT_ID, data=b"xlsx-bytes"))
    fake.put(Item("01TEAM", "Team", ROOT_ID, folder=True))
    fake.put(Item("01ROSTER", "roster.csv", "01TEAM", data=b"name\nAda\n"))
    return fake


def connector(
    fake: FakeGraph,
    *,
    site_url: str | None = SITE_URL,
    library_name: str | None = None,
    folder: str | None = None,
    credentials: dict[str, Any] | None = None,
    sleeps: list[float] | None = None,
) -> SharePointConnector:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return SharePointConnector(
        fake.client(),
        site_url=site_url,
        library=library_name,
        folder=folder,
        credentials=credentials or {"refresh_token": fake.refresh_token},
        app=APP,
        sleep=sleep,
    )


def ids(scan: ScanResult) -> set[str]:
    return {i.external_id for i in scan.items}


def by_id(items: list[RemoteItem]) -> dict[str, RemoteItem]:
    return {i.external_id: i for i in items}


async def test_the_first_sync_enumerates_the_library_and_keeps_the_delta_link() -> None:
    fake = library()

    scan = await connector(fake).scan({})

    assert scan.complete is True
    assert ids(scan) == {"01LEAVE", "01NOTES", "01OLD", "01BUDGET", "01ROSTER"}  # no .jpg
    assert scan.cursor["drive_id"] == DRIVE_ID
    assert scan.cursor["delta_link"].startswith(f"{GRAPH}/drives/{DRIVE_ID}/root/delta?token=")
    assert scan.cursor["folders"]["01ARCHIVE"] == ["01POLICIES", "Archive"]
    old = by_id(scan.items)["01OLD"]
    assert (old.title, old.filename, old.meta["path"]) == ("old", "old.txt", "Policies/Archive")
    assert old.url == f"{SITE_URL}/Shared%20Documents/Policies/Archive/old.txt"
    assert old.version.startswith('"c:')  # the cTag: content changes only
    deltas = [r for r in fake.requests if r.url.path.endswith("/root/delta")]
    assert len(deltas) == 4  # ten items, three per page: the next links were followed
    assert "%24select=" in str(deltas[0].url)


async def test_later_syncs_get_only_changes_and_tombstones() -> None:
    fake = library()
    source = connector(fake)
    first = await source.scan({})

    fake.write("01NOTES", b"# Notes\n\nNew notes.\n")
    fake.delete("01BUDGET")
    fake.put(Item("01NEW", "New hire.pdf", "01TEAM", data=b"%PDF"))
    second = await source.scan(first.cursor)

    assert second.complete is False  # only changes: nothing is deleted for being absent
    assert ids(second) == {"01NOTES", "01NEW"}
    assert second.deleted == ["01BUDGET"]
    assert by_id(second.items)["01NEW"].meta["path"] == "Team"
    assert second.cursor["delta_link"] != first.cursor["delta_link"]
    third = await source.scan(second.cursor)
    assert (third.items, third.deleted) == ([], [])


async def test_a_folder_selection_keeps_only_what_is_inside_it() -> None:
    fake = library()
    source = connector(fake, folder="Policies")
    first = await source.scan({})

    fake.move("01OLD", "01TEAM")  # out of Policies
    fake.move("01ROSTER", "01ARCHIVE")  # into Policies
    fake.write("01BUDGET", b"other")  # outside: not ours
    second = await source.scan(first.cursor)

    assert ids(first) == {"01LEAVE", "01NOTES", "01OLD"}
    assert first.cursor["scope"] == "01POLICIES"
    assert ids(second) == {"01ROSTER"}
    assert set(second.deleted) == {"01OLD", "01BUDGET"}  # never synced: ignored by the engine
    assert by_id(second.items)["01ROSTER"].meta["path"] == "Policies/Archive"


async def test_a_folder_leaving_the_selection_means_a_full_enumeration() -> None:
    """Graph reports a moved folder, not the files inside it: only a full listing tells."""
    fake = library()
    source = connector(fake, folder="Policies")
    first = await source.scan({})

    fake.move("01ARCHIVE", "01TEAM")
    second = await source.scan(first.cursor)

    assert second.complete is True  # so the engine deletes Archive/old.txt
    assert ids(second) == {"01LEAVE", "01NOTES"}


async def test_a_folder_entering_the_selection_means_a_full_enumeration() -> None:
    fake = library()
    source = connector(fake, folder="Policies")
    first = await source.scan({})

    fake.move("01TEAM", "01POLICIES")
    second = await source.scan(first.cursor)

    assert second.complete is True
    assert "01ROSTER" in ids(second)


async def test_a_deleted_folder_means_a_full_enumeration() -> None:
    fake = library()
    source = connector(fake)
    first = await source.scan({})

    fake.delete("01ARCHIVE")
    fake.items["01OLD"].deleted = True  # gone with it, but not reported one by one
    second = await source.scan(first.cursor)

    assert second.complete is True
    assert "01OLD" not in ids(second)


async def test_an_expired_delta_token_starts_over() -> None:
    fake = library()
    source = connector(fake)
    first = await source.scan({})

    fake.write("01NOTES", b"changed")
    fake.expired_before = len(fake.changes) + 1
    second = await source.scan(first.cursor)

    assert second.complete is True
    assert ids(second) == ids(first)
    assert any(r.url.path.endswith("/root/delta") for r in fake.requests)


async def test_a_new_selection_starts_over() -> None:
    fake = library()
    whole = await connector(fake).scan({})

    narrowed = await connector(fake, folder="Policies/Archive").scan(whole.cursor)

    assert narrowed.complete is True
    assert ids(narrowed) == {"01OLD"}


async def test_a_file_renamed_to_an_unreadable_type_leaves() -> None:
    fake = library()
    source = connector(fake)
    first = await source.scan({})

    fake.items["01NOTES"].name = "notes.zip"
    fake.changes.append("01NOTES")
    second = await source.scan(first.cursor)

    assert second.deleted == ["01NOTES"]


async def test_throttling_waits_as_graph_asks() -> None:
    fake = library()
    sleeps: list[float] = []
    fake.throttle, fake.retry_after = 2, "3"

    scan = await connector(fake, sleeps=sleeps).scan({})

    assert len(scan.items) == 5
    assert sleeps == [3.0, 3.0]


async def test_a_long_throttle_ends_the_sync_without_losing_the_position() -> None:
    fake = library()
    source = connector(fake)
    first = await source.scan({})
    fake.retry_after = "3600"
    fake.throttle = 10

    with pytest.raises(ConnectorError, match="throttling"):
        await source.scan(first.cursor)  # the engine keeps the stored cursor on an error


async def test_downloads_follow_the_pre_authenticated_url_without_the_token() -> None:
    fake = library()
    source = connector(fake)
    items = by_id((await source.scan({})).items)

    data = await source.fetch(items["01NOTES"])

    assert data == b"# Notes\n\nSome notes.\n"
    content, download = fake.requests[-2:]
    assert content.url.path == f"/v1.0/drives/{DRIVE_ID}/items/01NOTES/content"
    assert content.headers["authorization"].startswith("Bearer ")
    assert download.url.host == HOST
    assert "authorization" not in download.headers


async def test_a_file_deleted_after_the_scan_is_a_failed_item() -> None:
    fake = library()
    source = connector(fake)
    items = by_id((await source.scan({})).items)
    fake.delete("01NOTES")

    with pytest.raises(ConnectorError, match="not found"):
        await source.fetch(items["01NOTES"])


async def test_the_rotated_refresh_token_is_handed_back_with_its_scope() -> None:
    fake = library()
    source = connector(fake, credentials={"refresh_token": "graph-refresh-1"})

    await source.scan({})

    token_call = fake.requests[0]
    assert token_call.url.path == "/contoso.onmicrosoft.com/oauth2/v2.0/token"
    assert b"scope=offline_access+Sites.Read.All" in token_call.content
    assert source.updated_credentials is not None
    assert source.updated_credentials["refresh_token"] == "graph-refresh-2"


async def test_a_revoked_refresh_token_asks_to_reconnect() -> None:
    fake = library()

    with pytest.raises(ConnectorError, match="reconnect Microsoft 365"):
        await connector(fake, credentials={"refresh_token": "revoked"}).scan({})
    with pytest.raises(ConnectorError, match="connect Microsoft 365"):
        connector(fake, credentials={"other": "x"})


async def test_which_drive() -> None:
    fake = library()

    await connector(fake, site_url=None).scan({})  # the connecting account's OneDrive
    await connector(fake, site_url=None, library_name=DRIVE_ID).scan({})  # a drive by id
    await connector(fake, library_name="documents").scan({})  # a library by name

    paths = [r.url.path for r in fake.requests if r.url.host == "graph.microsoft.com"]
    assert "/v1.0/me/drive" in paths
    assert f"/v1.0/drives/{DRIVE_ID}" in paths
    assert any(p.endswith("/drives") for p in paths)
    with pytest.raises(ConnectorError, match="no document library named 'Nope'"):
        await connector(fake, library_name="Nope").scan({})
    with pytest.raises(ConnectorError, match="folder Nope not found"):
        await connector(fake, folder="Nope").scan({})
    with pytest.raises(ConnectorError, match="is a file"):
        await connector(fake, folder="Budget.xlsx").scan({})


async def test_links_from_graph_are_followed_only_on_graph() -> None:
    fake = library()
    source = connector(fake)
    first = await source.scan({})

    planted = {**first.cursor, "delta_link": "https://evil.example/delta?token=x"}
    again = await source.scan(planted)

    assert again.complete is True  # the planted link was ignored: a fresh enumeration
    assert not any(r.url.host == "evil.example" for r in fake.requests)

    class Hostile(FakeGraph):
        def _delta(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"value": [], "@odata.nextLink": "https://evil.example/next"}
            )

    with pytest.raises(ConnectorError, match="without a delta link"):
        await connector(Hostile()).scan({})


def test_the_permissions_asked_for_are_the_least_the_selection_needs() -> None:
    assert microsoft_scopes({"site_url": SITE_URL}) == ("offline_access", "Sites.Read.All")
    assert microsoft_scopes({"library": DRIVE_ID}) == ("offline_access", "Files.Read.All")
    assert microsoft_scopes({}) == ("offline_access", "Files.Read")


def test_configuration_is_validated() -> None:
    config = validate_config(
        "sharepoint",
        {"site_url": "https://Contoso.sharepoint.com/sites/finance/", "folder": "/Policies/"},
    )

    assert config == {"site_url": SITE_URL, "library": None, "folder": "Policies"}
    assert validate_config("sharepoint", {}) == {"site_url": None, "library": None, "folder": None}
    for bad in (
        {"site_url": "http://contoso.sharepoint.com/sites/x"},
        {"site_url": "https://contoso.sharepoint.com.evil.example/sites/x"},
        {"site_url": "https://intranet.contoso.com/sites/x"},
        {"site_url": "https://contoso.sharepoint.com/sites/../x"},
        {"folder": "Policies/../Secrets"},
    ):
        with pytest.raises(ValidationError):
            validate_config("sharepoint", bad)

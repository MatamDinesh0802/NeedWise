"""The links notifications carry: each must open a page that exists in apps/web."""

import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import workmind
from workmind.modules.work.links import approval_link, meeting_link, task_link, workflow_link

WEB_APP = Path(__file__).resolve().parents[2] / "web" / "src" / "app"
ID = uuid.UUID("0190f5a4-0000-7000-8000-000000000001")


def _page_exists(path: str) -> bool:
    """Whether apps/web/src/app has a page for this path ("[id]"-style folders match a value)."""
    folder = WEB_APP
    for part in [p for p in path.split("/") if p]:
        if (folder / part).is_dir():
            folder = folder / part
            continue
        dynamic = [d for d in folder.iterdir() if d.is_dir() and re.fullmatch(r"\[\w+\]", d.name)]
        if not dynamic:
            return False
        folder = dynamic[0]
    return (folder / "page.tsx").exists()


@pytest.mark.skipif(not WEB_APP.is_dir(), reason="the web app is not in this checkout")
@pytest.mark.parametrize(
    "link",
    [
        approval_link(ID),
        task_link(ID),
        meeting_link(ID),
        workflow_link("weekly-ops-report"),
        workflow_link("weekly-ops-report", ID),
    ],
)
def test_every_notification_link_opens_a_page_that_exists(link: str) -> None:
    assert link.startswith("/")
    assert not link.startswith("//")
    assert _page_exists(urlsplit(link).path), link


def test_the_workflow_link_names_the_workflow_and_the_run() -> None:
    assert workflow_link("weekly-ops-report", ID) == (
        f"/admin/workflows?workflow=weekly-ops-report&run={ID}"
    )


def test_notifications_take_their_links_from_the_links_module() -> None:
    helpers = ("approval_link(", "task_link(", "meeting_link(", "workflow_link(")
    passthrough = {"link", "n.link"}  # notify() itself, and the notifications API
    offenders = [
        f"{file.name}: link={value}"
        for file in Path(workmind.__file__).parent.rglob("*.py")
        for value in re.findall(r"\blink=([^,\n]+)", file.read_text())
        if value not in passthrough and not value.startswith(helpers)
    ]
    assert offenders == []

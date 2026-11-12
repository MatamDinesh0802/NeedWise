"""Workflows without a database: event matching, the event payload, trigger validation, and the
links notifications carry (each must open a page that exists in apps/web)."""

import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

import workmind
from workmind.modules.knowledge.models import Collection
from workmind.modules.work.events import DocumentReady, listens_to
from workmind.modules.work.links import (
    approval_link,
    document_link,
    meeting_link,
    task_link,
    workflow_link,
)
from workmind.modules.work.workflows import Definition, render

WEB_APP = Path(__file__).resolve().parents[2] / "web" / "src" / "app"
ID = uuid.UUID("0190f5a4-0000-7000-8000-000000000001")


def test_a_workflow_listens_to_its_event_and_only_its_collection() -> None:
    anywhere = {"trigger": {"event": "document.ready"}}
    handbook = {"trigger": {"event": "document.ready", "collection": "company-handbook"}}
    scheduled = {"trigger": {"schedule": "0 8 * * MON"}}

    assert listens_to(anywhere, "document.ready", "finance-reports")
    assert listens_to(handbook, "document.ready", "company-handbook")
    assert not listens_to(handbook, "document.ready", "finance-reports")
    assert not listens_to(scheduled, "document.ready", "company-handbook")
    assert not listens_to({}, "document.ready", "company-handbook")


def test_the_event_payload_fills_templates_and_keys_the_version() -> None:
    event = DocumentReady(
        document_id=ID,
        version_id=uuid.UUID(int=7),
        version=3,
        collection_id=uuid.uuid4(),
        title="Remote Work Policy",
        filename="remote-work.md",
        source="upload",
    )
    collection = Collection(key="company-handbook", name="Company Handbook")
    payload = event.payload(collection)

    assert event.key() == f"document.ready:{uuid.UUID(int=7)}"
    assert render(
        "New in {{ event.collection_name }}: {{ event.title }} v{{ event.version }} "
        "({{ event.link }})",
        {"event": payload},
    ) == (f"New in Company Handbook: Remote Work Policy v3 (/knowledge/documents/{ID})")
    assert render("{{ event.version }}", {"event": payload}) == 3  # a whole value keeps its type


def test_only_an_event_trigger_can_name_a_collection() -> None:
    steps = [{"id": "read", "research": "What changed?"}]
    Definition.model_validate(
        {"trigger": {"event": "document.ready", "collection": "company-handbook"}, "steps": steps}
    )
    with pytest.raises(ValidationError, match="only an event trigger"):
        Definition.model_validate({"trigger": {"collection": "company-handbook"}, "steps": steps})
    with pytest.raises(ValidationError):
        Definition.model_validate({"trigger": {"event": "meeting.ready"}, "steps": steps})


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
        document_link(ID),
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
    helpers = (
        "approval_link(",
        "task_link(",
        "meeting_link(",
        "workflow_link(",
        "document_link(",
    )
    passthrough = {"link", "n.link"}  # notify() itself, and the notifications API
    offenders = [
        f"{file.name}: link={value}"
        for file in Path(workmind.__file__).parent.rglob("*.py")
        for value in re.findall(r"\blink=([^,\n]+)", file.read_text())
        if value not in passthrough and not value.startswith(helpers)
    ]
    assert offenders == []

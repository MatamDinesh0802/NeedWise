"""The audit explorer (core): filters, cursor paging and the CSV export of the filtered view.
Needs the test database and TEST_VALKEY_URL."""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from needwise.core.config import Settings
from needwise.core.edition import Edition, License
from needwise.core.extensions import enable_extensions
from needwise.core.tenancy import tenant_transaction
from needwise.main import create_app
from needwise.modules.governance.audit import Actor, record_event
from needwise.modules.governance.models import AuditEvent
from tests.integration.conftest import VALKEY_URL, Acme, Database, SessionFactory, seed_acme

pytestmark = pytest.mark.skipif(not VALKEY_URL, reason="set TEST_VALKEY_URL to run")


async def add_events(sessions: SessionFactory, acme: Acme) -> None:
    """A few recognisable events on top of the seed's."""
    async with tenant_transaction(sessions, acme.tenant_id) as session:
        for n in range(5):
            await record_event(
                session,
                tenant_id=acme.tenant_id,
                actor=Actor(type="user", id=acme.users["alice"]),
                action="knowledge.document.reviewed",
                resource_type="document",
                resource_id=f"doc-{n}",
                details={"note": f"checked page {n}"},
            )
        await record_event(
            session,
            tenant_id=acme.tenant_id,
            actor=Actor(type="user", id=acme.users["erin"]),
            action="admin.collection.created",
            resource_type="collection",
            resource_id='=HYPERLINK("http://evil.test")',
            details={"key": "board-papers"},
        )


def page(api: TestClient, acme: Acme, who: str, **params: str | int) -> dict[str, Any]:
    response = api.get("/api/v1/admin/audit-events", params=params, headers=acme.as_(who))
    assert response.status_code == 200, response.text
    return response.json()


async def test_filters_narrow_the_log(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    await add_events(app_sessions, acme)
    alice = str(acme.users["alice"])

    by_prefix = page(api, acme, "erin", action="knowledge.")
    by_actor = page(api, acme, "erin", actor_id=alice)
    by_resource = page(api, acme, "erin", resource_type="document", resource_id="doc-3")
    by_text = page(api, acme, "erin", q="PAGE 4")  # case-insensitive, inside details
    future = page(
        api, acme, "erin", **{"from": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    )
    past = page(api, acme, "erin", to=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    recent = page(
        api, acme, "erin", **{"from": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    )

    def resources(body: dict[str, Any]) -> list[str | None]:
        return [e["resource_id"] for e in body["items"]]

    assert resources(by_prefix) == ["doc-4", "doc-3", "doc-2", "doc-1", "doc-0"]
    assert resources(by_actor) == ["doc-4", "doc-3", "doc-2", "doc-1", "doc-0"]
    assert resources(by_resource) == ["doc-3"]
    assert resources(by_text) == ["doc-4"]
    assert future["items"] == []
    assert past["items"] == []
    assert len(recent["items"]) >= 7  # the seed's event and ours


async def test_cursor_paging_walks_the_log_newest_first_without_gaps(
    api: TestClient, acme: Acme, app_sessions: SessionFactory
) -> None:
    await add_events(app_sessions, acme)
    seen: list[int] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, str | int] = {"limit": 2, "action": "knowledge."}
        if cursor:
            params["cursor"] = cursor
        body = page(api, acme, "amara.diallo", **params)  # the auditor reads the log too
        seen += [e["seq"] for e in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    bad = api.get("/api/v1/admin/audit-events", params={"cursor": "abc"}, headers=acme.as_("erin"))

    assert len(seen) == 5
    assert seen == sorted(seen, reverse=True)
    assert len(set(seen)) == 5
    assert bad.status_code == 422


async def test_the_csv_export_is_the_filtered_view_of_this_tenant_only(
    api: TestClient, acme: Acme, owner_sessions: SessionFactory, app_sessions: SessionFactory
) -> None:
    other = await seed_acme(owner_sessions)  # another company with the same people
    await add_events(app_sessions, acme)
    await add_events(app_sessions, other)

    export = api.get(
        "/api/v1/admin/audit-events/export.csv",
        params={"action": "admin.collection."},
        headers=acme.as_("erin"),
    )
    everything = api.get("/api/v1/admin/audit-events/export.csv", headers=acme.as_("amara.diallo"))
    member = api.get("/api/v1/admin/audit-events/export.csv", headers=acme.as_("bob"))
    signed_out = api.get("/api/v1/admin/audit-events/export.csv")

    assert export.status_code == 200, export.text
    assert export.headers["content-type"].startswith("text/csv")
    assert export.headers["content-disposition"].startswith('attachment; filename="audit-log-')
    assert export.headers["x-export-truncated"] == "false"
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert [r["action"] for r in rows] == ["admin.collection.created"]
    assert rows[0]["actor_name"] == "Erin Takahashi"
    # A cell that a spreadsheet would run as a formula is neutralized.
    assert rows[0]["resource_id"] == '\'=HYPERLINK("http://evil.test")'

    async with tenant_transaction(app_sessions, acme.tenant_id) as session:
        ours = set((await session.scalars(select(AuditEvent.id))).all())
        exports = (
            await session.scalars(select(AuditEvent).where(AuditEvent.action == "audit.export"))
        ).all()
    assert everything.status_code == 200
    seqs = {int(r["seq"]) for r in csv.DictReader(io.StringIO(everything.text))}
    assert len(seqs) == len(ours) - 1  # every event of this tenant except the second export's own
    assert "board-papers" in everything.text
    assert other.slug not in everything.text
    assert member.status_code == 403
    assert signed_out.status_code == 401
    assert [(e.actor_id, e.details["format"], e.details["filters"]) for e in exports] == [
        (acme.users["erin"], "csv", {"action": "admin.collection."}),
        (acme.users["amara.diallo"], "csv", {}),
    ]
    assert exports[0].details["events"] == 1


async def test_the_siem_export_is_enterprise_only(api: TestClient, acme: Acme) -> None:
    response = api.get("/api/v1/admin/audit-events/export.jsonl", headers=acme.as_("erin"))
    assert response.status_code == 402
    assert response.json()["code"] == "enterprise_feature"
    assert "'advanced_audit_export'" in response.json()["detail"]
    assert api.get("/api/v1/admin/audit-events/export.jsonl").status_code == 401


async def test_the_siem_export_streams_a_chain_anyone_can_verify(
    database: Database, tmp_path: Path, acme: Acme, app_sessions: SessionFactory
) -> None:
    pytest.importorskip("needwise_ee", reason="install the ee extra: uv sync --extra ee")
    await add_events(app_sessions, acme)
    now = datetime.now(UTC)
    license_ = License(
        "Acme Robotics",
        frozenset({"advanced_audit_export"}),
        now - timedelta(days=1),
        now + timedelta(days=30),
        key_id="test",
    )
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=database.app_url,
        valkey_url=VALKEY_URL or "",
        authz_outbox_worker=False,
        object_storage_path=str(tmp_path),
    )
    app = create_app(settings, extensions=enable_extensions(Edition(license_)))
    with TestClient(app) as licensed:
        full = licensed.get("/api/v1/admin/audit-events/export.jsonl", headers=acme.as_("erin"))
        member = licensed.get("/api/v1/admin/audit-events/export.jsonl", headers=acme.as_("bob"))
        lines = [json.loads(line) for line in full.text.splitlines()]
        newer = licensed.get(
            "/api/v1/admin/audit-events/export.jsonl",
            params={"after_seq": lines[-2]["seq"]},
            headers=acme.as_("amara.diallo"),
        )

    assert full.status_code == 200, full.text
    assert full.headers["content-type"].startswith("application/x-ndjson")
    assert member.status_code == 403
    # Recompute every hash from the exported fields alone, as a SIEM would.
    previous: str | None = None
    for expected_seq, record in enumerate(lines, start=1):
        fields = {k: v for k, v in record.items() if k not in ("prev_hash", "hash")}
        canonical = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(bytes.fromhex(previous or "") + canonical).hexdigest()
        assert record["seq"] == expected_seq
        assert record["tenant_id"] == str(acme.tenant_id)
        assert record["prev_hash"] == previous
        assert record["hash"] == digest
        previous = record["hash"]
    assert lines[-1]["action"] == "audit.export"  # the export is in the log it exports
    assert [r["seq"] for r in (json.loads(x) for x in newer.text.splitlines())] == [
        lines[-1]["seq"],
        lines[-1]["seq"] + 1,  # amara's export
    ]

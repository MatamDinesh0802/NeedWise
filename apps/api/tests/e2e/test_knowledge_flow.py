import time
import uuid

import httpx

from tests.e2e.conftest import same_origin_headers, sign_in


def _collection(client: httpx.Client, key: str) -> dict[str, object] | None:
    items = client.get("/api/v1/collections").json()["items"]
    return next((c for c in items if c["key"] == key), None)


def test_an_upload_is_ingested_by_the_worker_and_becomes_readable(browser: httpx.Client) -> None:
    sign_in(browser, "alice@acme.test")
    handbook = _collection(browser, "company-handbook")
    assert handbook is not None
    assert handbook["can_edit"] is True  # Alice manages HR, which edits the handbook

    filename = f"e2e-note-{uuid.uuid4().hex[:6]}.md"
    uploaded = browser.post(
        f"/api/v1/collections/{handbook['id']}/documents",
        params={"filename": filename},
        content=b"# E2E Note\n\n## Purpose\nThis note checks the ingestion worker end to end.\n",
        headers={**same_origin_headers(), "Content-Type": "text/markdown"},
    )
    assert uploaded.status_code == 202, uploaded.text
    document_id = uploaded.json()["document"]["id"]

    deadline, status = time.monotonic() + 90, {}
    while time.monotonic() < deadline:
        status = browser.get(f"/api/v1/documents/{document_id}").json()
        if status["status"] in ("ready", "failed"):
            break
        time.sleep(1)
    try:
        assert status["status"] == "ready", status
        assert status["chunk_count"] == 1
    finally:
        browser.delete(f"/api/v1/documents/{document_id}", headers=same_origin_headers())


def test_restricted_collections_stay_invisible_to_people_outside_them(
    browser: httpx.Client,
) -> None:
    sign_in(browser, "bob@acme.test")

    assert _collection(browser, "engineering-docs") is not None
    assert _collection(browser, "hr-employee-records") is None
    assert _collection(browser, "leadership-strategy") is None

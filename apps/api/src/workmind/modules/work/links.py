"""Where a notification opens in the web app. Every link a notification carries comes from here,
and tests/test_work_links.py checks each one against the pages in apps/web/src/app."""

import uuid
from urllib.parse import urlencode


def approval_link(approval_id: uuid.UUID) -> str:
    return f"/approvals/{approval_id}"


def task_link(task_id: uuid.UUID) -> str:
    return f"/tasks/{task_id}"


def meeting_link(meeting_id: uuid.UUID) -> str:
    return f"/meetings/{meeting_id}"


def document_link(document_id: uuid.UUID) -> str:
    """A Knowledge Hub document (owners, reviews, official status: docs/governance.md)."""
    return f"/knowledge/documents/{document_id}"


def workflow_link(key: str, run_id: uuid.UUID | None = None) -> str:
    """Admin > Workflows with this workflow's runs open (the run, when given, expanded)."""
    query = {"workflow": key, **({"run": str(run_id)} if run_id else {})}
    return f"/admin/workflows?{urlencode(query)}"

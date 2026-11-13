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


def governance_link() -> str:
    """Admin > Governance: what needs attention, for administrators (docs/governance.md)."""
    return "/admin/governance"


def legal_holds_link() -> str:
    """Admin > Legal holds (Enterprise, docs/enterprise.md#legal-hold)."""
    return "/admin/legal-holds"


def usage_link() -> str:
    """The usage page: everyone sees the budgets that apply to them there (docs/usage.md)."""
    return "/analytics"


def workflow_link(key: str, run_id: uuid.UUID | None = None) -> str:
    """Admin > Workflows with this workflow's runs open (the run, when given, expanded)."""
    query = {"workflow": key, **({"run": str(run_id)} if run_id else {})}
    return f"/admin/workflows?{urlencode(query)}"


def source_link(key: str, *, admin: bool) -> str:
    """A source in Admin > Sources (org admins), or in Knowledge Hub > Sources (the owner of the
    collection it syncs into). Both pages open the source named in `source`."""
    page = "/admin/sources" if admin else "/knowledge/sources"
    return f"{page}?{urlencode({'source': key})}"

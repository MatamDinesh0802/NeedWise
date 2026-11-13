"""The Enterprise package as the core sees it: an entry point whose setup registers cleanly."""

import importlib.metadata
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("needwise_ee", reason="install the ee extra: uv sync --extra ee")

from needwise.core.edition import FEATURES, Edition, License
from needwise.core.extensions import ENTRY_POINT_GROUP, ExtensionRegistry, enable_extensions
from needwise.modules.connectors.extensions import PERMISSION_SYNC
from needwise.modules.usage.extensions import CHARGEBACK_REPORT, DEPARTMENT_BUDGETS
from pydantic import ValidationError

import needwise_ee
from needwise_ee.budgets import DepartmentBudgets
from needwise_ee.chargeback import safe_cell
from needwise_ee.retention import PolicyIn, cutoffs

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def _enterprise() -> Edition:
    license_ = License(
        licensee="Acme Robotics",
        features=frozenset(FEATURES),
        issued_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        key_id="test",
    )
    return Edition(license_, clock=lambda: NOW)


def test_the_package_reports_the_version_it_was_released_as() -> None:
    # One version for the repository (docs/operations/releasing.md): the Enterprise package's
    # comes from its metadata, as the core's does, never a constant a release could miss.
    import needwise

    assert needwise_ee.__version__ == importlib.metadata.version("needwise-ee")
    assert needwise_ee.__version__ == needwise.__version__


def test_the_package_declares_the_core_entry_point() -> None:
    entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    assert [(ep.name, ep.value) for ep in entry_points] == [("needwise_ee", "needwise_ee:setup")]
    assert entry_points["needwise_ee"].load() is needwise_ee.setup


def test_setup_registers_only_known_features() -> None:
    registry = ExtensionRegistry(_enterprise())
    needwise_ee.setup(registry, registry.edition)
    assert set(needwise_ee.IMPLEMENTED) <= set(FEATURES)
    assert {r.feature for r in registry.routers} <= set(needwise_ee.IMPLEMENTED)
    assert {t.feature for t in registry.scheduled_tasks} <= set(needwise_ee.IMPLEMENTED)


def test_the_installed_package_enables_enterprise() -> None:
    registry = enable_extensions(_enterprise())
    assert registry.edition.enterprise
    assert registry.edition.has("retention_policies")


def _licensed(*features: str) -> Edition:
    license_ = License(
        licensee="Acme Robotics",
        features=frozenset(features),
        issued_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        key_id="test",
    )
    return Edition(license_, clock=lambda: NOW)


def test_setup_plugs_department_budgets_and_chargeback_into_the_usage_hooks() -> None:
    registry = ExtensionRegistry(_enterprise())
    needwise_ee.setup(registry, registry.edition)

    assert registry.implemented(DEPARTMENT_BUDGETS)
    assert registry.implemented(CHARGEBACK_REPORT)
    assert isinstance(registry.call(DEPARTMENT_BUDGETS)[0], DepartmentBudgets)


def test_each_hook_follows_its_own_feature() -> None:
    registry = ExtensionRegistry(_licensed("chargeback_reports"))
    needwise_ee.setup(registry, registry.edition)

    assert registry.implemented(CHARGEBACK_REPORT)
    assert not registry.implemented(DEPARTMENT_BUDGETS)
    assert registry.call(DEPARTMENT_BUDGETS) == []


@pytest.mark.parametrize(
    ("name", "cell"),
    [
        ("People & HR", "People & HR"),
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+1 Sales", "'+1 Sales"),
        ("-Ops", "'-Ops"),
        ("@Legal", "'@Legal"),
    ],
)
def test_department_names_never_run_as_spreadsheet_formulas(name: str, cell: str) -> None:
    assert safe_cell(name) == cell


def test_setup_registers_retention_the_siem_export_and_permission_sync() -> None:
    registry = ExtensionRegistry(_enterprise())
    needwise_ee.setup(registry, registry.edition)
    paths = {
        (getattr(route, "path", ""), extension.feature)
        for extension in registry.routers
        for route in extension.router.routes
    }
    assert set(needwise_ee.IMPLEMENTED) == {
        "department_budgets",
        "chargeback_reports",
        "retention_policies",
        "advanced_audit_export",
        "permission_sync",
        "legal_hold",
    }
    assert ("/admin/retention", "retention_policies") in paths
    assert ("/admin/retention/run", "retention_policies") in paths
    assert ("/admin/retention/documents/preview", "retention_policies") in paths
    assert ("/admin/audit-events/export.jsonl", "advanced_audit_export") in paths
    assert ("/admin/legal-holds", "legal_hold") in paths
    assert ("/admin/legal-holds/{hold_id}/release", "legal_hold") in paths
    assert [(t.name, t.every_s, t.feature) for t in registry.scheduled_tasks] == [
        ("needwise_ee.retention.apply", 86_400.0, "retention_policies"),
        ("needwise_ee.permission_sync.drive_changes", 60.0, "permission_sync"),
    ]
    assert registry.implemented(PERMISSION_SYNC)


def test_retention_never_goes_below_30_days() -> None:
    with pytest.raises(ValidationError):
        PolicyIn(conversations_days=29)
    policy = PolicyIn(conversations_days=30, read_notifications_days=365)
    limits = cutoffs(policy, NOW)
    assert limits["conversations"] == NOW - timedelta(days=30)
    assert limits["read_notifications"] == NOW - timedelta(days=365)
    assert limits["analytics_runs"] is None  # not set: kept forever
    assert limits["meeting_recordings"] is None

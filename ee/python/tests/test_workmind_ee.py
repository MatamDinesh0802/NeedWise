"""The Enterprise package as the core sees it: an entry point whose setup registers cleanly."""

import importlib.metadata
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("workmind_ee", reason="install the ee extra: uv sync --extra ee")

from pydantic import ValidationError
from workmind.core.edition import FEATURES, Edition, License
from workmind.core.extensions import ENTRY_POINT_GROUP, ExtensionRegistry, enable_extensions
from workmind.modules.usage.extensions import CHARGEBACK_REPORT, DEPARTMENT_BUDGETS

import workmind_ee
from workmind_ee.budgets import DepartmentBudgets
from workmind_ee.chargeback import safe_cell
from workmind_ee.retention import PolicyIn, cutoffs

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


def test_the_package_declares_the_core_entry_point() -> None:
    entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    assert [(ep.name, ep.value) for ep in entry_points] == [("workmind_ee", "workmind_ee:setup")]
    assert entry_points["workmind_ee"].load() is workmind_ee.setup


def test_setup_registers_only_known_features() -> None:
    registry = ExtensionRegistry(_enterprise())
    workmind_ee.setup(registry, registry.edition)
    assert set(workmind_ee.IMPLEMENTED) <= set(FEATURES)
    assert {r.feature for r in registry.routers} <= set(workmind_ee.IMPLEMENTED)
    assert {t.feature for t in registry.scheduled_tasks} <= set(workmind_ee.IMPLEMENTED)


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
    workmind_ee.setup(registry, registry.edition)

    assert registry.implemented(DEPARTMENT_BUDGETS)
    assert registry.implemented(CHARGEBACK_REPORT)
    assert isinstance(registry.call(DEPARTMENT_BUDGETS)[0], DepartmentBudgets)


def test_each_hook_follows_its_own_feature() -> None:
    registry = ExtensionRegistry(_licensed("chargeback_reports"))
    workmind_ee.setup(registry, registry.edition)

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


def test_setup_registers_retention_and_the_siem_export() -> None:
    registry = ExtensionRegistry(_enterprise())
    workmind_ee.setup(registry, registry.edition)
    paths = {
        (getattr(route, "path", ""), extension.feature)
        for extension in registry.routers
        for route in extension.router.routes
    }
    assert set(workmind_ee.IMPLEMENTED) == {
        "department_budgets",
        "chargeback_reports",
        "retention_policies",
        "advanced_audit_export",
    }
    assert ("/admin/retention", "retention_policies") in paths
    assert ("/admin/retention/run", "retention_policies") in paths
    assert ("/admin/audit-events/export.jsonl", "advanced_audit_export") in paths
    assert [(t.name, t.every_s, t.feature) for t in registry.scheduled_tasks] == [
        ("workmind_ee.retention.apply", 86_400.0, "retention_policies")
    ]


def test_retention_never_goes_below_30_days() -> None:
    with pytest.raises(ValidationError):
        PolicyIn(conversations_days=29)
    policy = PolicyIn(conversations_days=30, read_notifications_days=365)
    limits = cutoffs(policy, NOW)
    assert limits["conversations"] == NOW - timedelta(days=30)
    assert limits["read_notifications"] == NOW - timedelta(days=365)
    assert limits["analytics_runs"] is None  # not set: kept forever
    assert limits["meeting_recordings"] is None

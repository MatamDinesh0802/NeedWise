"""The Enterprise package as the core sees it: an entry point whose setup registers cleanly."""

import importlib.metadata
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("workmind_ee", reason="install the ee extra: uv sync --extra ee")

from pydantic import ValidationError
from workmind.core.edition import FEATURES, Edition, License
from workmind.core.extensions import ENTRY_POINT_GROUP, ExtensionRegistry, enable_extensions

import workmind_ee
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


def test_setup_registers_retention_and_the_siem_export() -> None:
    registry = ExtensionRegistry(_enterprise())
    workmind_ee.setup(registry, registry.edition)
    paths = {
        (getattr(route, "path", ""), extension.feature)
        for extension in registry.routers
        for route in extension.router.routes
    }
    assert set(workmind_ee.IMPLEMENTED) == {"retention_policies", "advanced_audit_export"}
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

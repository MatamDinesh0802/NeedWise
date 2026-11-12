"""Enterprise scheduled tasks on Celery: queued by beat, run only while their feature is
licensed, each run with its own Resources (closed afterwards)."""

from datetime import UTC, datetime, timedelta

import pytest
from celery import Celery

from workmind.core.config import Settings
from workmind.core.edition import Edition, License
from workmind.core.extensions import ExtensionRegistry
from workmind.workers import enterprise

NOW = datetime(2026, 11, 12, tzinfo=UTC)
NAME = "workmind_ee.retention.apply"


class FakeResources:
    opened: list["FakeResources"] = []  # noqa: RUF012

    def __init__(self, extensions: ExtensionRegistry) -> None:
        self.edition = extensions.edition
        self.closed = False

    @classmethod
    def create(cls, _settings: Settings, *, extensions: ExtensionRegistry) -> "FakeResources":
        resources = cls(extensions)
        cls.opened.append(resources)
        return resources

    async def aclose(self) -> None:
        self.closed = True


def test_a_scheduled_task_runs_only_while_its_feature_is_licensed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(enterprise, "Resources", FakeResources)
    FakeResources.opened.clear()
    clock = [NOW]
    lic = License("Acme", frozenset({"retention_policies"}), NOW, NOW + timedelta(days=1))
    registry = ExtensionRegistry(Edition(lic, clock=lambda: clock[0]))

    async def apply(resources: FakeResources) -> str:
        return f"applied in {resources.edition.name}"

    registry.add_scheduled_task(NAME, apply, every_s=3600, feature="retention_policies")  # pyright: ignore[reportArgumentType]
    celery = Celery("test")
    enterprise.register_scheduled_tasks(celery, Settings(_env_file=None), registry)  # pyright: ignore[reportCallIssue]

    assert celery.conf.beat_schedule == {NAME: {"task": NAME, "schedule": 3600}}
    task = celery.tasks[NAME]
    assert task() == "applied in enterprise"
    assert [r.closed for r in FakeResources.opened] == [True]

    clock[0] = NOW + timedelta(days=2)  # the license expired since startup
    assert task() is None
    assert len(FakeResources.opened) == 1  # skipped before opening anything


def test_community_registers_no_task() -> None:
    celery = Celery("test")
    enterprise.register_scheduled_tasks(
        celery,
        Settings(_env_file=None),  # pyright: ignore[reportCallIssue]
        ExtensionRegistry(Edition.community()),
    )
    assert celery.conf.beat_schedule == {}
    assert not [name for name in celery.tasks if name.startswith("workmind_ee.")]

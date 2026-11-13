"""The extension registry (ADR 0011): enterprise code loads only with a valid license, runs
only while its feature is licensed, and a failure in it leaves a working Community app."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from importlib.metadata import EntryPoint

import pytest
from fastapi.testclient import TestClient

from needwise.core import extensions as extensions_module
from needwise.core.config import Settings
from needwise.core.edition import Edition, License
from needwise.core.extensions import (
    ENTRY_POINT_GROUP,
    ExtensionRegistry,
    Hook,
    enable_extensions,
)
from needwise.main import create_app
from tests import ee_fakes

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def enterprise(*features: str, clock: list[datetime] | None = None) -> Edition:
    now = clock or [NOW]
    lic = License("Acme", frozenset(features), NOW - timedelta(days=1), NOW + timedelta(days=30))
    return Edition(lic, clock=lambda: now[0])


def entry_point(target: str, name: str = "needwise_ee") -> EntryPoint:
    return EntryPoint(name=name, value=target, group=ENTRY_POINT_GROUP)


Logs = list[tuple[str, str]]


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> Logs:
    events: Logs = []

    class Recorder:
        def __getattr__(self, level: str):
            return lambda event, **_: events.append((level, event))

    monkeypatch.setattr(extensions_module, "log", Recorder())
    return events


@pytest.fixture(autouse=True)
def _reset_calls() -> Iterator[None]:
    ee_fakes.calls.clear()
    yield
    ee_fakes.calls.clear()


# --- Discovery and setup --------------------------------------------------------------------


def test_community_never_loads_enterprise_code() -> None:
    # A target that cannot even be imported proves it is never touched.
    registry = enable_extensions(
        Edition.community(), entry_points=[entry_point("needwise_ee_missing:setup")]
    )
    assert registry.edition.name == "community"
    assert registry.routers == ()
    assert ee_fakes.calls == []


def test_a_licensed_setup_registers_its_extensions(logs: Logs) -> None:
    registry = enable_extensions(
        enterprise("retention_policies"), entry_points=[entry_point("tests.ee_fakes:good_setup")]
    )
    assert ee_fakes.calls == ["good:enterprise"]
    assert registry.edition.enterprise
    assert [(r.router, r.feature) for r in registry.routers] == [
        (ee_fakes.router, "retention_policies")
    ]
    assert logs == [("info", "enterprise_enabled")]


@pytest.mark.parametrize(
    "target",
    ["tests.ee_fakes:broken_setup", "needwise_ee_missing:setup", "tests.ee_fakes:not_callable"],
)
def test_a_failing_setup_leaves_a_clean_community_registry(target: str, logs: Logs) -> None:
    registry = enable_extensions(
        enterprise("retention_policies"),
        entry_points=[entry_point("tests.ee_fakes:good_setup", "a"), entry_point(target, "b")],
    )
    assert registry.edition.name == "community"
    assert not registry.edition.has("retention_policies")
    assert registry.routers == ()  # what the good setup registered is dropped too
    assert logs == [("exception", "enterprise_setup_failed")]


def test_a_license_without_the_enterprise_package_runs_as_community(logs: Logs) -> None:
    registry = enable_extensions(enterprise("retention_policies"), entry_points=[])
    assert registry.edition.name == "community"
    assert logs == [("warning", "enterprise_package_missing")]


# --- Registration rules ---------------------------------------------------------------------


async def _noop(_resources: object) -> None:
    return None


def test_scheduled_tasks_are_named_unique_and_not_too_frequent() -> None:
    registry = ExtensionRegistry(enterprise("retention_policies"))
    registry.add_scheduled_task(
        "needwise_ee.retention.apply", _noop, every_s=3600, feature="retention_policies"
    )
    assert [t.name for t in registry.scheduled_tasks] == ["needwise_ee.retention.apply"]
    with pytest.raises(ValueError, match="twice"):
        registry.add_scheduled_task(
            "needwise_ee.retention.apply", _noop, every_s=3600, feature="retention_policies"
        )
    for name in ("needwise.sync_due_sources", "needwise_ee.", "retention"):
        with pytest.raises(ValueError, match=r"start with 'needwise_ee\.'"):
            registry.add_scheduled_task(name, _noop, every_s=3600, feature="retention_policies")
    with pytest.raises(ValueError, match="at most every"):
        registry.add_scheduled_task(
            "needwise_ee.fast", _noop, every_s=1, feature="retention_policies"
        )


def test_every_extension_names_a_known_feature() -> None:
    registry = ExtensionRegistry(enterprise("retention_policies"))
    with pytest.raises(ValueError, match="unknown enterprise feature"):
        registry.add_router(ee_fakes.router, feature="everything")  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="unknown enterprise feature"):
        registry.add_hook(Hook[[], None]("x"), lambda: None, feature="nope")  # pyright: ignore[reportArgumentType]


# --- Hooks ----------------------------------------------------------------------------------

LABEL = Hook[[str, int], str]("test.label")


def test_hooks_run_only_while_their_feature_is_licensed() -> None:
    clock = [NOW]
    registry = ExtensionRegistry(enterprise("chargeback_reports", clock=clock))
    registry.add_hook(LABEL, lambda name, n: f"{name}x{n}", feature="chargeback_reports")
    registry.add_hook(LABEL, lambda name, n: f"budget:{name}", feature="department_budgets")

    assert registry.call(LABEL, "ops", 3) == ["opsx3"]  # the unlicensed one is skipped
    assert registry.implemented(LABEL)
    assert not registry.implemented(Hook[[], None]("other"))

    clock[0] = NOW + timedelta(days=31)  # the license expired
    assert registry.call(LABEL, "ops", 3) == []
    assert not registry.implemented(LABEL)


def test_hooks_do_nothing_in_community_and_errors_propagate() -> None:
    community = ExtensionRegistry(Edition.community())
    assert community.call(LABEL, "a", 1) == []

    def failing(name: str, n: int) -> str:
        raise PermissionError(f"{name} {n}")

    registry = ExtensionRegistry(enterprise("chargeback_reports"))
    registry.add_hook(LABEL, failing, feature="chargeback_reports")
    with pytest.raises(PermissionError):  # a guard must not fail open
        registry.call(LABEL, "a", 1)


def test_hooks_are_matched_by_identity_not_by_name() -> None:
    registry = ExtensionRegistry(enterprise("chargeback_reports"))
    registry.add_hook(LABEL, lambda name, n: name, feature="chargeback_reports")
    assert registry.call(Hook[[str, int], str]("test.label"), "a", 1) == []


# --- Mounting in the app ----------------------------------------------------------------------


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        authz_outbox_worker=False,
    )


def test_enterprise_routers_are_mounted_behind_sign_in_and_the_feature() -> None:
    registry = ExtensionRegistry(enterprise("retention_policies"))
    registry.add_router(ee_fakes.router, feature="retention_policies")
    app = create_app(_settings(), extensions=registry)

    assert "/api/v1/ee-test/ping" in app.openapi()["paths"]
    with TestClient(app) as client:
        assert app.state.resources.edition is registry.edition
        assert app.state.resources.extensions is registry
        response = client.get("/api/v1/ee-test/ping")
    assert response.status_code == 401  # sign-in comes first, the license second
    assert response.json()["code"] == "unauthenticated"


def test_the_edition_endpoint_needs_sign_in() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get("/api/v1/edition")
    assert response.status_code == 401


def test_the_app_is_community_without_a_license() -> None:
    app = create_app(_settings())
    with TestClient(app):
        assert app.state.resources.edition.name == "community"
        assert app.state.resources.extensions.routers == ()

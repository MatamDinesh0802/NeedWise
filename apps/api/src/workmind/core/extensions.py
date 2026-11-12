"""Extension points for the Enterprise edition (ADR 0011, docs/enterprise.md).

The core never imports workmind_ee. Enterprise code is an installed package that declares an
entry point in the group "workmind.ee":

    [project.entry-points."workmind.ee"]
    workmind_ee = "workmind_ee:setup"

At startup (the API in create_app, Celery workers and beat when they import their app),
`load_extensions` checks the license. Only with a valid one does it load those entry points and
call each `setup(registry, edition)`, which registers what the package adds:

- routers, mounted under /api/v1 behind sign-in and require_feature(feature);
- scheduled tasks, run by Celery beat, skipped while their feature is not licensed;
- hook implementations, called by the core at named extension points (`Hook`) while licensed.

Every extension names the licensed feature it belongs to and the core checks that feature each
time, so an extension stops working when its license expires. If any setup raises, the error is
logged, everything registered is dropped and the process runs as Community.
"""

import importlib.metadata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

import structlog
from fastapi import APIRouter

from workmind.core.config import Settings
from workmind.core.edition import FEATURES, Edition, Feature, load_edition

if TYPE_CHECKING:
    from workmind.core.resources import Resources

log = structlog.get_logger("workmind.extensions")

ENTRY_POINT_GROUP: Final = "workmind.ee"
# Enterprise Celery task names start with this, so they can never replace a core task.
TASK_PREFIX: Final = "workmind_ee."
MIN_TASK_INTERVAL_S: Final = 10.0


class Hook[**P, R]:
    """A named extension point. The core declares one where it wants enterprise behaviour:

        BEFORE_EXPORT = Hook[[uuid.UUID], None]("audit.before_export")

    enterprise setup adds implementations with `registry.add_hook(BEFORE_EXPORT, fn,
    feature=...)`, and the core runs them with `registry.call(BEFORE_EXPORT, tenant_id)`.
    Hooks compare by identity: use the core's constant, not a new Hook with the same name.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"Hook({self.name!r})"


@dataclass(frozen=True)
class RouterExtension:
    router: APIRouter
    feature: Feature


@dataclass(frozen=True)
class ScheduledTask:
    name: str
    run: Callable[["Resources"], Awaitable[object]]
    every_s: float
    feature: Feature


@dataclass(frozen=True)
class _HookImplementation:
    function: Callable[..., object]
    feature: Feature


type Setup = Callable[["ExtensionRegistry", Edition], None]


class ExtensionRegistry:
    """What enterprise setup registered, and the edition it runs under."""

    def __init__(self, edition: Edition) -> None:
        self.edition = edition
        self._routers: list[RouterExtension] = []
        self._tasks: dict[str, ScheduledTask] = {}
        self._hooks: dict[Hook[Any, Any], list[_HookImplementation]] = {}

    # --- Registration (enterprise setup) ------------------------------------------------------

    def add_router(self, router: APIRouter, *, feature: Feature) -> None:
        """Mounted under /api/v1; every route needs sign-in and the licensed feature (402)."""
        _check_feature(feature)
        self._routers.append(RouterExtension(router, feature))

    def add_scheduled_task(
        self,
        name: str,
        run: Callable[["Resources"], Awaitable[object]],
        *,
        every_s: float,
        feature: Feature,
    ) -> None:
        """Celery beat queues `name` every `every_s` seconds; the worker awaits `run` with fresh
        Resources, and skips it while `feature` is not licensed."""
        _check_feature(feature)
        if not name.startswith(TASK_PREFIX) or name == TASK_PREFIX:
            raise ValueError(f"scheduled task names start with {TASK_PREFIX!r}: {name!r}")
        if name in self._tasks:
            raise ValueError(f"scheduled task {name!r} is registered twice")
        if every_s < MIN_TASK_INTERVAL_S:
            raise ValueError(f"scheduled task {name!r} runs at most every {MIN_TASK_INTERVAL_S}s")
        self._tasks[name] = ScheduledTask(name, run, every_s, feature)

    def add_hook[**P, R](
        self, hook: Hook[P, R], implementation: Callable[P, R], *, feature: Feature
    ) -> None:
        _check_feature(feature)
        self._hooks.setdefault(hook, []).append(_HookImplementation(implementation, feature))

    # --- Use (core) ---------------------------------------------------------------------------

    @property
    def routers(self) -> tuple[RouterExtension, ...]:
        return tuple(self._routers)

    @property
    def scheduled_tasks(self) -> tuple[ScheduledTask, ...]:
        return tuple(self._tasks.values())

    def implemented(self, hook: Hook[Any, Any]) -> bool:
        """True if at least one implementation of `hook` is licensed right now."""
        return any(self.edition.has(i.feature) for i in self._hooks.get(hook, ()))

    def call[**P, R](self, hook: Hook[P, R], /, *args: P.args, **kwargs: P.kwargs) -> list[R]:
        """Runs every licensed implementation of `hook` in registration order and returns their
        results (empty in Community). Exceptions propagate to the caller: a hook that guards
        something must not fail open. For async hooks, R is an awaitable: await each result."""
        results: list[R] = []
        for implementation in self._hooks.get(hook, ()):
            if self.edition.has(implementation.feature):
                results.append(cast(R, implementation.function(*args, **kwargs)))
        return results


def _check_feature(feature: str) -> None:
    if feature not in FEATURES:
        raise ValueError(f"unknown enterprise feature {feature!r}; known: {sorted(FEATURES)}")


# --- Startup ------------------------------------------------------------------------------------


def load_extensions(
    settings: Settings, *, entry_points: Sequence[importlib.metadata.EntryPoint] | None = None
) -> ExtensionRegistry:
    """The edition from the settings, with enterprise extensions when it is Enterprise."""
    return enable_extensions(load_edition(settings), entry_points=entry_points)


def enable_extensions(
    edition: Edition, *, entry_points: Sequence[importlib.metadata.EntryPoint] | None = None
) -> ExtensionRegistry:
    """Community: an empty registry, and workmind_ee is never imported. Enterprise: the
    "workmind.ee" entry points' setup functions, all or nothing."""
    if not edition.enterprise:
        return ExtensionRegistry(edition)
    found = (
        list(entry_points)
        if entry_points is not None
        else list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))
    )
    if not found:
        log.warning(
            "enterprise_package_missing",
            detail="The license is valid but no workmind-ee package is installed",
            edition="community",
        )
        return ExtensionRegistry(Edition.community())
    registry = ExtensionRegistry(edition)
    for entry_point in sorted(found, key=lambda ep: ep.name):
        try:
            setup = entry_point.load()
            if not callable(setup):
                raise TypeError(f"{entry_point.value} is not callable")
            setup(registry, edition)
        except Exception:
            log.exception(
                "enterprise_setup_failed", entry_point=entry_point.name, edition="community"
            )
            return ExtensionRegistry(Edition.community())
    log.info(
        "enterprise_enabled",
        entry_points=[ep.name for ep in found],
        routers=len(registry.routers),
        scheduled_tasks=[t.name for t in registry.scheduled_tasks],
    )
    return registry

# Celery ships without type information.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""Enterprise scheduled tasks on Celery (ADR 0011). workmind_ee registers them through the
extension registry; this module turns each into a Celery task and a beat schedule entry."""

import asyncio

import structlog
from celery import Celery

from workmind.core.config import Settings
from workmind.core.extensions import ExtensionRegistry, ScheduledTask
from workmind.core.resources import Resources

log = structlog.get_logger("workmind.workers.enterprise")


def register_scheduled_tasks(
    celery: Celery, settings: Settings, extensions: ExtensionRegistry
) -> None:
    for task in extensions.scheduled_tasks:
        _register(celery, settings, extensions, task)


def _register(
    celery: Celery, settings: Settings, extensions: ExtensionRegistry, task: ScheduledTask
) -> None:
    async def run() -> object:
        # Checked on every run: a license that expired since startup stops the task.
        if not extensions.edition.has(task.feature):
            log.warning("enterprise_task_skipped", task=task.name, feature=task.feature)
            return None
        resources = Resources.create(settings, extensions=extensions)
        try:
            return await task.run(resources)
        finally:
            await resources.aclose()

    def entry() -> object:
        return asyncio.run(run())

    celery.task(name=task.name, shared=False)(entry)  # this app only, not every Celery app
    celery.conf.beat_schedule[task.name] = {"task": task.name, "schedule": task.every_s}

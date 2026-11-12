"""Seeds a tenant's analytics (ARCHITECTURE 16) from a directory next to the company file:

    analytics/semantic.yaml      the semantic model
    analytics/data/*.csv|*.xlsx  one table per file, named after the file

Safe to run repeatedly: the dataset is rebuilt only when a file changed (by SHA-256), and the
model is stored only when its text changed. Tables an admin added later are kept.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import tenant_transaction
from workmind.modules.analytics import dataset
from workmind.modules.analytics.service import Analytics, load_model
from workmind.modules.governance.audit import Actor

CONTENT_TYPES = {".csv": "text/csv", ".xlsx": dataset.XLSX}


@dataclass(frozen=True)
class AnalyticsSeedResult:
    model_updated: bool
    dataset_updated: bool
    tables: dict[str, int]  # table -> rows


def table_sources(data_dir: Path) -> list[dataset.TableSource]:
    return [
        dataset.TableSource(
            name=path.stem,
            data=path.read_bytes(),
            content_type=CONTENT_TYPES[path.suffix.lower()],
            filename=path.name,
        )
        for path in sorted(data_dir.glob("*"))
        if path.is_file() and path.suffix.lower() in CONTENT_TYPES
    ]


async def seed_analytics(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    directory: Path,
    root: Path,
) -> AnalyticsSeedResult | None:
    """None when the directory has no semantic.yaml."""
    model_file = directory / "semantic.yaml"
    if not model_file.is_file():
        return None
    service = Analytics(sessions, root)
    sources = table_sources(directory / "data")

    async with tenant_transaction(sessions, tenant_id) as session:
        loaded = await load_model(session, tenant_id)
    current = loaded.dataset_tables if loaded else {}
    path = loaded.dataset_path(root, tenant_id) if loaded else None
    unchanged = (
        path is not None
        and path.is_file()
        and all(
            isinstance(current.get(s.name), dict)
            and current[s.name].get("sha256") == dataset.sha256(s.data)
            for s in sources
        )
    )
    tables = current
    if sources and not unchanged:
        tables = await service.replace_tables(tenant_id, put=sources, actor=Actor.system())

    model_updated = await service.store_model(
        tenant_id, model_file.read_text(encoding="utf-8"), actor=Actor.system()
    )
    return AnalyticsSeedResult(
        model_updated=model_updated,
        dataset_updated=bool(sources) and not unchanged,
        tables={name: int(info.get("rows", 0)) for name, info in tables.items()},
    )

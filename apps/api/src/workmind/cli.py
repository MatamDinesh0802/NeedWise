"""`wm`: the WorkMind command line.

wm eval [options]         run the evaluation suites (see `wm eval --help`)
wm seed [options]         seed a demo company (see `wm seed --help`)
wm license dev [options]  write a development Enterprise license (see `wm license dev --help`)
"""

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command, rest = (args[0], args[1:]) if args else ("", [])
    if command == "eval":
        from workmind.evals.__main__ import main as run_eval

        return run_eval(rest)
    if command == "seed":
        from workmind.seed.__main__ import main as run_seed

        return run_seed(rest)
    if command == "license":
        from workmind.core.dev_license import main as run_license

        return run_license(rest)
    if command == "reindex" and len(rest) == 1:
        import asyncio

        return asyncio.run(_reindex(rest[0]))
    print(__doc__, file=sys.stderr)
    return 0 if command in ("-h", "--help") else 1


async def _reindex(tenant_slug: str) -> int:
    from sqlalchemy import select

    from workmind.core.config import Settings
    from workmind.core.resources import Resources
    from workmind.core.tenancy import global_transaction
    from workmind.modules.identity.models import Tenant
    from workmind.modules.knowledge.pipeline import IngestionDeps
    from workmind.modules.knowledge.reindex import reindex_tenant

    settings = Settings()
    resources = Resources.create(settings)
    try:
        async with global_transaction(resources.db) as session:
            tenant_id = await session.scalar(select(Tenant.id).where(Tenant.slug == tenant_slug))
        if tenant_id is None:
            print(f"tenant {tenant_slug!r} not found", file=sys.stderr)
            return 1
        await resources.qdrant.ensure_collection(
            resources.collection_name, dense_size=settings.embedding_dimensions
        )
        deps = IngestionDeps(
            resources.db,
            resources.storage,
            resources.embeddings,
            resources.qdrant,
            resources.collection_name,
        )
        result = await reindex_tenant(deps, tenant_id)
    finally:
        await resources.aclose()
    print(
        f"re-indexed {result.documents} documents ({result.chunks} chunks), "
        f"{result.relabelled} relabelled, {result.failed} failed",
        file=sys.stderr,
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

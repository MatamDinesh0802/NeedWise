"""`wm`: the WorkMind command line.

wm eval [options]         run the evaluation suites (see `wm eval --help`)
wm seed [options]         seed a demo company (see `wm seed --help`)
wm license dev [options]  write a development Enterprise license (see `wm license dev --help`)
wm reindex <tenant>       re-index a tenant's documents
wm authz sync [<tenant>]  bring authorization stores up to date: the current model, and
                          tuples recomputed from PostgreSQL (all active tenants by default);
                          after an upgrade or a restore (docs/operations/)
wm secrets rotate         re-encrypt stored model provider keys with SECRETS_ENCRYPTION_KEY
wm usage backfill [<tenant>]
                          import usage stored before usage events existed (all active tenants
                          by default); safe to run again (docs/usage.md)
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
    if command == "authz" and rest[:1] == ["sync"] and len(rest) <= 2:
        import asyncio

        return asyncio.run(_sync_authz(rest[1] if len(rest) == 2 else None))
    if command == "secrets" and rest == ["rotate"]:
        import asyncio

        return asyncio.run(_rotate_secrets())
    if command == "usage" and rest[:1] == ["backfill"] and len(rest) <= 2:
        import asyncio

        return asyncio.run(_backfill_usage(rest[1] if len(rest) == 2 else None))
    print(__doc__, file=sys.stderr)
    return 0 if command in ("-h", "--help") else 1


async def _backfill_usage(tenant_slug: str | None) -> int:
    from sqlalchemy import select

    from workmind.core.config import Settings
    from workmind.core.db import create_engine, create_session_factory
    from workmind.core.tenancy import global_transaction
    from workmind.modules.identity.models import Tenant
    from workmind.modules.usage.backfill import backfill_tenant

    settings = Settings()
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    try:
        async with global_transaction(sessions) as session:
            query = select(Tenant.id, Tenant.slug)
            query = (
                query.where(Tenant.slug == tenant_slug)
                if tenant_slug
                else query.where(Tenant.status == "active")
            )
            tenants = list((await session.execute(query)).all())
        if tenant_slug and not tenants:
            print(f"tenant {tenant_slug!r} not found", file=sys.stderr)
            return 1
        for tenant_id, slug in tenants:
            result = await backfill_tenant(sessions, tenant_id)
            print(
                f"{slug}: imported {result.messages} answers, {result.analytics_runs} BI "
                f"answers, {result.meetings} meetings",
                file=sys.stderr,
            )
    finally:
        await engine.dispose()
    return 0


async def _sync_authz(tenant_slug: str | None) -> int:
    import httpx

    from workmind.core.config import Settings
    from workmind.core.db import create_engine, create_session_factory
    from workmind.modules.authz.openfga import OpenFgaClient
    from workmind.modules.authz.sync import sync_tenants

    settings = Settings()
    engine = create_engine(settings.migrations_database_url)  # owner role, as for seeding
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            fga = OpenFgaClient(
                http, settings.openfga_api_url, settings.openfga_api_key.get_secret_value()
            )
            synced = await sync_tenants(create_session_factory(engine), fga, tenant_slug)
    finally:
        await engine.dispose()
    if tenant_slug and not synced:
        print(f"tenant {tenant_slug!r} not found", file=sys.stderr)
        return 1
    for tenant in synced:
        print(
            f"{tenant.slug}: store {tenant.store_id}, {tenant.written} tuples written, "
            f"{tenant.deleted} removed",
            file=sys.stderr,
        )
    return 0


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


async def _rotate_secrets() -> int:
    from workmind.core.config import Settings
    from workmind.core.db import create_engine, create_session_factory
    from workmind.modules.model_catalog.rotation import rotate_keys
    from workmind.modules.model_catalog.secrets import KeyRing

    settings = Settings()
    keyring = KeyRing.from_settings(settings)
    engine = create_engine(settings.database_url)
    try:
        report = await rotate_keys(create_session_factory(engine), keyring)
    finally:
        await engine.dispose()
    print(
        f"model provider keys: {report.rotated} re-encrypted with key {keyring.active_key_id}, "
        f"{report.current} already on it, {report.failed} could not be decrypted",
        file=sys.stderr,
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

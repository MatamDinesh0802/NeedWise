"""python -m workmind.seed <company.yaml> [--skip-keycloak]

Development only: creates the demo organization in Keycloak (with logins for demo personas)
and the matching tenant, departments, groups and people in WorkMind.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx

from workmind.core.config import Settings, get_settings
from workmind.core.db import create_engine, create_session_factory
from workmind.modules.authz.openfga import OpenFgaClient
from workmind.modules.authz.sync import ensure_store, reconcile
from workmind.modules.knowledge.pipeline import IngestionDeps
from workmind.modules.knowledge.storage import LocalObjectStorage
from workmind.modules.retrieval.qdrant import QdrantClient
from workmind.providers.embeddings import EmbeddingUnavailableError
from workmind.providers.factory import create_embeddings, secret
from workmind.seed.analytics_load import seed_analytics
from workmind.seed.apply import SeedResult, seed_database, seed_keycloak
from workmind.seed.company import CompanySpec, load_company
from workmind.seed.corpus_load import load_corpus
from workmind.seed.governance_load import seed_governance
from workmind.seed.keycloak_admin import KeycloakAdmin


async def _run(
    spec: CompanySpec,
    settings: Settings,
    *,
    skip_keycloak: bool,
    skip_openfga: bool,
    corpus_dir: Path | None,
    analytics_dir: Path | None = None,
) -> SeedResult:
    subjects: dict[str, str] = {}
    if not skip_keycloak:
        async with httpx.AsyncClient(timeout=15) as http:
            admin = KeycloakAdmin(http, settings.keycloak_url, settings.keycloak_realm)
            await admin.login(
                settings.keycloak_admin_user, settings.keycloak_admin_password.get_secret_value()
            )
            subjects = await seed_keycloak(spec, admin, settings.demo_password.get_secret_value())
    engine = create_engine(settings.migrations_database_url)  # owner role
    sessions = create_session_factory(engine)
    try:
        result = await seed_database(spec, sessions, subjects)
        if analytics_dir is not None:
            await _load_analytics(settings, sessions, result, analytics_dir)
        if corpus_dir is not None:
            await _load_corpus(settings, sessions, result, spec, corpus_dir)
        governed = await seed_governance(sessions, result.tenant_id, spec)
        print(
            f"Governance: {governed.owners_set} document owners and "
            f"{governed.reviews_set} review dates set."
        )
        if not skip_openfga:
            async with httpx.AsyncClient(timeout=15) as http:
                fga = OpenFgaClient(
                    http, settings.openfga_api_url, settings.openfga_api_key.get_secret_value()
                )
                store_id = await ensure_store(sessions, fga, result.tenant_id)
                synced = await reconcile(sessions, fga, result.tenant_id, store_id)
            print(
                f"Authorization store {store_id}: {synced.written} tuples written, "
                f"{synced.deleted} removed."
            )
        return result
    finally:
        await engine.dispose()


async def _load_analytics(
    settings: Settings, sessions: Any, result: SeedResult, analytics_dir: Path
) -> None:
    seeded = await seed_analytics(
        sessions, result.tenant_id, analytics_dir, settings.analytics_path
    )
    if seeded is None:
        return
    rows = sum(seeded.tables.values())
    print(
        f"Analytics: {len(seeded.tables)} tables ({rows} rows), "
        f"dataset {'rebuilt' if seeded.dataset_updated else 'unchanged'}, "
        f"model {'updated' if seeded.model_updated else 'unchanged'}."
    )


async def _load_corpus(
    settings: Settings, sessions: Any, result: SeedResult, spec: CompanySpec, corpus_dir: Path
) -> None:
    owner = next(p.email for p in spec.people if p.org_role == "owner")
    async with httpx.AsyncClient(timeout=120) as http:
        deps = IngestionDeps(
            sessions=sessions,
            storage=LocalObjectStorage(settings.object_storage_path),
            embeddings=create_embeddings(settings, http),
            qdrant=QdrantClient(http, settings.qdrant_url, secret(settings.qdrant_api_key)),
            collection_name=settings.qdrant_collection,
        )
        try:
            loaded = await load_corpus(deps, result.tenant_id, corpus_dir, uploader_email=owner)
        except EmbeddingUnavailableError as exc:
            print(
                f"\nThe embedding server is not reachable ({exc}).\n"
                "Start it with `make embeddings`, or seed without documents: --skip-corpus.",
                file=sys.stderr,
            )
            raise SystemExit(3) from exc
    print(
        f"Documents: {loaded.indexed} indexed ({loaded.chunks} chunks), "
        f"{loaded.unchanged} unchanged"
        + (f", {len(loaded.failed)} failed:" if loaded.failed else ".")
    )
    for failure in loaded.failed:
        print(f"  - {failure}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m workmind.seed", description=__doc__)
    parser.add_argument("company_file", type=Path)
    parser.add_argument("--skip-keycloak", action="store_true", help="don't create logins")
    parser.add_argument("--skip-openfga", action="store_true", help="don't sync authorization")
    parser.add_argument("--skip-corpus", action="store_true", help="don't load documents")
    parser.add_argument(
        "--skip-analytics", action="store_true", help="don't load the analytics model and data"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.app_env == "production":
        print("Refusing to seed demo data with APP_ENV=production.", file=sys.stderr)
        return 2

    spec = load_company(args.company_file)
    corpus_dir = args.company_file.parent / "corpus"
    load = not args.skip_corpus and (corpus_dir / "manifest.yaml").exists()
    analytics_dir = args.company_file.parent / "analytics"
    analytics = not args.skip_analytics and (analytics_dir / "semantic.yaml").exists()
    result = asyncio.run(
        _run(
            spec,
            settings,
            skip_keycloak=args.skip_keycloak,
            skip_openfga=args.skip_openfga,
            corpus_dir=corpus_dir if load else None,
            analytics_dir=analytics_dir if analytics else None,
        )
    )

    print(
        f"Seeded {spec.company.name} ({spec.company.slug}): {result.people} people, "
        f"{result.departments} departments, {result.groups} groups, "
        f"{result.keycloak_logins} logins, {len(result.collections)} collections."
    )
    if result.keycloak_logins:
        print("\nDemo logins (password: the DEMO_PASSWORD setting, default 'workmind-demo'):")
        for person in spec.demo_people:
            print(f"  {person.email:<22} {person.name:<17} {person.title or ''}")
        print(f"\nSign in at {settings.public_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

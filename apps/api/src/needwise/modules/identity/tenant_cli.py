"""`nw tenant`: create, list, delete and verify tenants (docs/multi-tenancy.md).

    nw tenant list
    nw tenant create <slug> --name NAME [--owner-email E [--owner-name N]] [--keycloak [--domain D]]
    nw tenant delete <slug> --confirm <slug> [--keycloak] [--override-legal-holds <slug>]
    nw tenant verify-empty <tenant-id> [--slug S] [--store STORE_ID] [--keycloak]

Runs with the owner database role (MIGRATIONS_DATABASE_URL). `--keycloak` also manages the
tenant's Keycloak organization with KEYCLOAK_ADMIN_USER / KEYCLOAK_ADMIN_PASSWORD. A tenant
with an active legal hold is refused unless `--override-legal-holds` repeats the tenant too
(docs/enterprise.md#legal-hold). Exit codes: 0 done, 1 refused or something remains after a
deletion, 2 bad arguments.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select

from needwise.core.config import Settings
from needwise.core.db import create_engine, create_session_factory
from needwise.core.tenancy import global_transaction, tenant_transaction
from needwise.modules.authz.openfga import OpenFgaClient
from needwise.modules.identity.lifecycle import (
    Stores,
    TenantError,
    create_tenant,
    delete_tenant,
    verify_empty,
)
from needwise.modules.identity.models import Tenant, User
from needwise.modules.knowledge.storage import LocalObjectStorage
from needwise.modules.retrieval.qdrant import QdrantClient
from needwise.providers.factory import secret
from needwise.seed.keycloak_admin import KeycloakAdmin


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nw tenant", description="Tenant lifecycle")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="every tenant with its status and people")
    create = commands.add_parser("create", help="create a tenant and its authorization store")
    create.add_argument("slug")
    create.add_argument("--name", required=True)
    create.add_argument("--owner-email", help="the first owner, linked at their first sign-in")
    create.add_argument("--owner-name")
    create.add_argument("--keycloak", action="store_true", help="also create the organization")
    create.add_argument("--domain", help="the organization's email domain (with --keycloak)")
    remove = commands.add_parser("delete", help="delete a tenant from every store, then verify")
    remove.add_argument("tenant", help="slug or id")
    remove.add_argument("--confirm", required=True, help="the slug again, to confirm")
    remove.add_argument("--keycloak", action="store_true", help="also delete the organization")
    remove.add_argument(
        "--override-legal-holds",
        metavar="TENANT",
        help="delete even though legal holds are active: the tenant again, typed on purpose",
    )
    verify = commands.add_parser("verify-empty", help="list what remains of a deleted tenant")
    verify.add_argument("tenant_id", type=uuid.UUID)
    verify.add_argument("--slug", help="also look for stores and organizations named after it")
    verify.add_argument("--store", help="the tenant's OpenFGA store id")
    verify.add_argument("--keycloak", action="store_true")
    return parser


@asynccontextmanager
async def _stores(settings: Settings, *, keycloak: bool) -> AsyncGenerator[Stores]:
    engine = create_engine(settings.migrations_database_url)
    valkey = Redis.from_url(settings.valkey_url)  # pyright: ignore[reportUnknownMemberType]
    async with httpx.AsyncClient(timeout=30) as http:
        admin: KeycloakAdmin | None = None
        if keycloak:
            admin = KeycloakAdmin(http, settings.keycloak_url, settings.keycloak_realm)
            await admin.login(
                settings.keycloak_admin_user, settings.keycloak_admin_password.get_secret_value()
            )
        try:
            yield Stores(
                owner=create_session_factory(engine),
                fga=OpenFgaClient(
                    http, settings.openfga_api_url, settings.openfga_api_key.get_secret_value()
                ),
                qdrant=QdrantClient(http, settings.qdrant_url, secret(settings.qdrant_api_key)),
                storage=LocalObjectStorage(settings.object_storage_path),
                analytics_root=Path(settings.analytics_path),
                valkey=valkey,
                keycloak=admin,
            )
        finally:
            await valkey.aclose()
            await engine.dispose()


async def _list(stores: Stores) -> int:
    async with global_transaction(stores.owner) as session:
        tenants = list(await session.scalars(select(Tenant).order_by(Tenant.slug)))
    for tenant in tenants:
        async with tenant_transaction(stores.owner, tenant.id) as session:
            people = await session.scalar(select(func.count()).select_from(User))
        print(f"{tenant.slug}\t{tenant.id}\t{tenant.status}\t{people} people\t{tenant.name}")
    return 0


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    async with _stores(settings, keycloak=bool(getattr(args, "keycloak", False))) as stores:
        if args.command == "list":
            return await _list(stores)
        if args.command == "create":
            created = await create_tenant(
                stores,
                slug=args.slug,
                name=args.name,
                owner_email=args.owner_email,
                owner_name=args.owner_name,
                domain=args.domain,
            )
            print(f"created tenant {created.slug} ({created.id}); OpenFGA store {created.store_id}")
            if created.owner_id:
                print(f"owner {args.owner_email} invited: linked at their first sign-in")
            if created.keycloak_org:
                print(f"Keycloak organization {created.slug} ({created.keycloak_org})")
            return 0
        if args.command == "delete":
            if args.confirm != args.tenant:
                print("refused: --confirm must repeat the tenant exactly", file=sys.stderr)
                return 1
            override = args.override_legal_holds
            if override is not None and override != args.tenant:
                print(
                    "refused: --override-legal-holds must repeat the tenant exactly",
                    file=sys.stderr,
                )
                return 1
            report = await delete_tenant(
                stores, args.tenant, override_legal_holds=override is not None
            )
            print(
                f"deleted tenant {report.slug} ({report.tenant_id}): {report.sessions} sessions, "
                f"{report.keys} Valkey keys, {report.files} files, points in "
                f"{len(report.collections)} index collections, OpenFGA store {report.store_id}, "
                f"{report.keycloak_users} Keycloak users"
            )
            return _report(report.remaining.lines())
        remaining = await verify_empty(stores, args.tenant_id, slug=args.slug, store_id=args.store)
        return _report(remaining.lines())


def _report(lines: Sequence[str]) -> int:
    if not lines:
        print("verified: nothing of the tenant remains")
        return 0
    print("NOT EMPTY, still present:", file=sys.stderr)
    for line in lines:
        print(f"  {line}", file=sys.stderr)
    return 1


def main(argv: Sequence[str], settings: Settings | None = None) -> int:
    try:
        args = _parser().parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return asyncio.run(_run(args, settings or Settings()))
    except TenantError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1

"""python -m workmind.seed <company.yaml> [--skip-keycloak]

Development only: creates the demo organization in Keycloak (with logins for demo personas)
and the matching tenant, departments, groups and people in WorkMind.
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from workmind.core.config import Settings, get_settings
from workmind.core.db import create_engine, create_session_factory
from workmind.seed.apply import SeedResult, seed_database, seed_keycloak
from workmind.seed.company import CompanySpec, load_company
from workmind.seed.keycloak_admin import KeycloakAdmin


async def _run(spec: CompanySpec, settings: Settings, *, skip_keycloak: bool) -> SeedResult:
    subjects: dict[str, str] = {}
    if not skip_keycloak:
        async with httpx.AsyncClient(timeout=15) as http:
            admin = KeycloakAdmin(http, settings.keycloak_url, settings.keycloak_realm)
            await admin.login(
                settings.keycloak_admin_user, settings.keycloak_admin_password.get_secret_value()
            )
            subjects = await seed_keycloak(spec, admin, settings.demo_password.get_secret_value())
    engine = create_engine(settings.migrations_database_url)  # owner role
    try:
        return await seed_database(spec, create_session_factory(engine), subjects)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m workmind.seed", description=__doc__)
    parser.add_argument("company_file", type=Path)
    parser.add_argument("--skip-keycloak", action="store_true", help="only seed the database")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.app_env == "production":
        print("Refusing to seed demo data with APP_ENV=production.", file=sys.stderr)
        return 2

    spec = load_company(args.company_file)
    result = asyncio.run(_run(spec, settings, skip_keycloak=args.skip_keycloak))

    print(
        f"Seeded {spec.company.name} ({spec.company.slug}): {result.people} people, "
        f"{result.departments} departments, {result.groups} groups, "
        f"{result.keycloak_logins} logins."
    )
    if result.keycloak_logins:
        print("\nDemo logins (password: the DEMO_PASSWORD setting, default 'workmind-demo'):")
        for person in spec.demo_people:
            print(f"  {person.email:<22} {person.name:<17} {person.title or ''}")
        print(f"\nSign in at {settings.public_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

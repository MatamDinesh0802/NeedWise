# 0005. Separate owner/app database roles; Row-Level Security fails closed

- Status: Accepted
- Date: 2026-11-02

## Context
A single forgotten `WHERE tenant_id = …` would leak data across tenants. Database owners and
superusers bypass Row-Level Security (RLS) by default.

## Decision
- `workmind_owner` owns the schema and is used only by Alembic migrations.
- `workmind_app` is the runtime role: DML only, `NOBYPASSRLS`, never owns tables.
- From Phase 1, every tenant-owned table has `ENABLE` + `FORCE ROW LEVEL SECURITY` with a
  policy on `current_setting('app.tenant_id', true)`. The app sets it per transaction with
  `set_config(..., true)`. If unset, the setting is NULL and no rows match: **fail closed**.
- Roles are created in `deploy/postgres/init`. Default privileges grant the app role access
  to every table the owner creates.

## Consequences
- Tenant isolation holds even when application code has a bug.
- Two connection strings to manage (`DATABASE_URL`, `MIGRATIONS_DATABASE_URL`).
- Background jobs must set the tenant explicitly, which is desirable anyway.

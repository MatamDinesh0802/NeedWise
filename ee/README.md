# Enterprise Edition (`ee/`)

Features for large organizations, under the commercial license in [`LICENSE`](LICENSE), not
Apache-2.0. Everything outside this directory is the Apache-2.0 core, which is complete on its
own. See [docs/enterprise.md](../docs/enterprise.md) for the editions, licensing and the
extension points, and [ADR 0011](../docs/adr/0011-open-core-boundary-and-license-keys.md) for
the design.

```text
ee/
├── LICENSE                     the Enterprise Edition license
└── python/                     the needwise-ee package (module needwise_ee)
    ├── pyproject.toml          declares the "needwise.ee" entry point -> needwise_ee:setup
    ├── src/needwise_ee/        setup(registry, edition) registers routers, tasks and hooks
    └── tests/
```

The package is the optional `ee` extra of `apps/api` (`uv sync --extra ee`) and is built into
the API image. The core loads it only with a valid license key; without one it is never
imported. Get a development license with `make ee-dev-license` (accepted only with
`APP_ENV=dev`).

**Status:** the boundary is built (license verification, the extension registry, the package,
`GET /api/v1/edition`, 402 for unlicensed features). Implemented here, one module per feature:

| Module | Feature | Plugs in as |
|---|---|---|
| `budgets.py` | `department_budgets` | the core's `usage.department_budgets` hook ([docs/usage.md](../docs/usage.md)) |
| `chargeback.py` | `chargeback_reports` | the core's `usage.chargeback_report` hook |
| `retention.py` | `retention_policies` | a router (`/admin/retention`) and a daily task ([docs/enterprise.md](../docs/enterprise.md#retention-policies)) |
| `document_retention.py` | `retention_policies` | a router (`/admin/retention/documents`), applied by the same daily task ([docs/enterprise.md](../docs/enterprise.md#document-retention)) |
| `audit_export.py` | `advanced_audit_export` | a router (`/admin/audit-events/export.jsonl`) |
| `permission_sync.py` | `permission_sync` | the core's `connectors.permission_sync` hook and a task every minute (the Drive changes feed) ([docs/permission-sync.md](../docs/permission-sync.md)) |
| `legal_hold.py` | `legal_hold` | a router (`/admin/legal-holds`); the core enforces holds on every deletion path, in both editions ([docs/enterprise.md](../docs/enterprise.md#legal-hold)) |

No official licenses are issued yet.

Rules that keep the boundary clean:

- Code outside `ee/` never imports from `ee/` (a test enforces it). Enterprise code plugs in
  through the core's extension registry, and every extension names the licensed feature the
  core checks before running it.
- Security basics are never placed here. Everything needed to run NeedWise safely is in the
  Apache-2.0 core.
- Tables for Enterprise features come from the core's migrations, with forced Row-Level
  Security like every tenant table.
- External contributions to `ee/` are not accepted.

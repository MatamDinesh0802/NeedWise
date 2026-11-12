# Enterprise Edition (`ee/`)

Features for large organizations, under the commercial license in [`LICENSE`](LICENSE), not
Apache-2.0. Everything outside this directory is the Apache-2.0 core, which is complete on its
own. See [docs/enterprise.md](../docs/enterprise.md) for the editions, licensing and the
extension points, and [ADR 0011](../docs/adr/0011-open-core-boundary-and-license-keys.md) for
the design.

```text
ee/
├── LICENSE                     the Enterprise Edition license
└── python/                     the workmind-ee package (module workmind_ee)
    ├── pyproject.toml          declares the "workmind.ee" entry point -> workmind_ee:setup
    ├── src/workmind_ee/        setup(registry, edition) registers routers, tasks and hooks
    └── tests/
```

The package is the optional `ee` extra of `apps/api` (`uv sync --extra ee`) and is built into
the API image. The core loads it only with a valid license key; without one it is never
imported. Get a development license with `make ee-dev-license` (accepted only with
`APP_ENV=dev`).

**Status:** the boundary is built (license verification, the extension registry, the package,
`GET /api/v1/edition`, 402 for unlicensed features). No Enterprise feature is implemented yet:
`setup` registers nothing. Phase 11 adds retention policies, department budgets, chargeback
reports and advanced audit export here. No official licenses are issued yet.

Rules that keep the boundary clean:

- Code outside `ee/` never imports from `ee/` (a test enforces it). Enterprise code plugs in
  through the core's extension registry, and every extension names the licensed feature the
  core checks before running it.
- Security basics are never placed here. Everything needed to run WorkMind safely is in the
  Apache-2.0 core.
- Tables for Enterprise features come from the core's migrations, with forced Row-Level
  Security like every tenant table.
- External contributions to `ee/` are not accepted.

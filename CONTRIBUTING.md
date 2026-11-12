# Contributing to WorkMind AI

Thanks for helping. This guide covers setup, conventions and what a pull request needs.

## Setup

See [Local development](README.md#local-development). In short: `make install`, `make infra`,
`make api-dev`, `make web-dev`.

## Before you open a pull request

- `make lint test` passes.
- New behavior has tests. Permission-related changes need authorization tests: every role ×
  action that the change touches, including the "deny" cases.
- Documentation and ADRs are updated when a design decision changes.
- The PR does one thing. Large changes start as an issue or discussion first.

## Documentation

The documentation site is built from `docs/` with MkDocs (`mkdocs.yml`). `make docs-serve`
serves it on http://127.0.0.1:8495 while you write; `make docs` builds it with `--strict`, as CI
does, so a broken link or anchor fails. Write Markdown that also reads well on GitHub, and add a
new page to `nav` in `mkdocs.yml`.

`docs/reference/` is generated from the code. After changing a setting (`core/config.py`) or an
API route, run `make docs-gen` and commit the result; a unit test fails until you do.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org): `feat:`, `fix:`, `perf:`,
`eval:`, `build:`, `ci:`, `docs:`, `refactor:`, `test:`, `chore:`. release-please turns them into
the changelog and the next version ([docs/operations/releasing.md](docs/operations/releasing.md)).

## Dependencies and images

Lockfiles (`apps/api/uv.lock`, `apps/web/pnpm-lock.yaml`) are committed, images are pinned by
digest and third-party actions by commit SHA. `make scan` runs the same supply-chain checks as
CI, with Docker only. A known vulnerability in a dependency needs a fix or a written, expiring
exception ([docs/security/supply-chain.md](docs/security/supply-chain.md)).

## Developer Certificate of Origin (DCO)

Every commit must be signed off, certifying that you wrote the change or have the right to
submit it under the project's license ([developercertificate.org](https://developercertificate.org)):

```bash
git commit -s -m "feat: add document version history"
```

This adds `Signed-off-by: Your Name <you@example.com>`. CI rejects commits without it.

## Licensing of contributions

Contributions to everything outside `ee/` are made under the [Apache License 2.0](LICENSE).
The `ee/` directory is under a separate commercial license and does not accept external
contributions.

## Security

Never open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

Never commit real company data, credentials or personal information. Test data must be
synthetic (see `seed/`).
